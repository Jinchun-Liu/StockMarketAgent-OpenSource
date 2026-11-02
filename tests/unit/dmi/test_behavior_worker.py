from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from apps.worker import tasks
from pydantic import SecretStr

from stock_assistant.contracts.behavior import (
    BehaviorComputeRequest,
    BehaviorPressurePage,
    BehaviorPressureSnapshotV1_0,
    HistoricalAnalogCandidate,
    HistoricalAnalogCandidatePage,
    HistoricalAnalogState,
    IndexAvailability,
    PressureClusterName,
    PressureMode,
)
from stock_assistant.contracts.common import InstrumentRef, MutationContext
from stock_assistant.contracts.forecasting import MarketBar
from stock_assistant.contracts.market_data import (
    MarketBarRevision,
    MarketDataManifest,
    MarketDataManifestStatus,
    MarketDataRevisionPage,
)
from stock_assistant.modules.behavior.dev_reference import (
    build_dev_only_behavior_inputs,
    validated_dev_only_behavior_inputs,
)
from stock_assistant.modules.behavior.scoring import compose_behavior_pressure
from stock_assistant.platform.db.behavior import (
    PersistedAnalogObservation,
    PostgresBehaviorPressureRepository,
)


def _request(*, mode: PressureMode = PressureMode.INTRADAY_10M) -> BehaviorComputeRequest:
    window_end = datetime(2026, 8, 7, 2, 0, tzinfo=UTC)
    window_start = (
        window_end - timedelta(minutes=10)
        if mode is PressureMode.INTRADAY_10M
        else window_end - timedelta(days=20)
    )
    return BehaviorComputeRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=window_end,
            requested_at=window_end,
            idempotency_key="behavior-worker-dev-only-0001",
        ),
        mode=mode,
        window_start=window_start,
        window_end=window_end,
        diagnostic_semantics_hash="a" * 64,
        data_manifest_hash="b" * 64,
        rights_snapshot_hash="c" * 64,
    )


def _revisions(request: BehaviorComputeRequest) -> list[MarketBarRevision]:
    instrument = InstrumentRef(
        instrument_id="XSHG:603986",
        symbol="603986",
        exchange="XSHG",
        market="CN",
    )
    result = []
    step = (request.window_end - request.window_start) / 10
    for index in range(10):
        closed_at = request.window_start + step * (index + 1)
        opened_at = request.window_start + step * index
        open_price = Decimal("80") + Decimal(index) / Decimal(10)
        close_price = open_price + Decimal(index % 3 - 1) / Decimal(10)
        bar = MarketBar(
            instrument=instrument,
            interval="1min",
            opened_at=opened_at,
            closed_at=closed_at,
            received_at=closed_at,
            open_price=open_price,
            high_price=max(open_price, close_price) + Decimal("0.2"),
            low_price=min(open_price, close_price) - Decimal("0.2"),
            close_price=close_price,
            volume=Decimal(1000 + index * 100),
            adjusted=False,
            source_id="dev_only_fixture",
            data_version="dev-only-v1",
            adjustment_version="none",
            rights_snapshot_hash=request.rights_snapshot_hash,
        )
        result.append(
            MarketBarRevision(
                revision_id=uuid4(),
                instrument=instrument,
                effective_at=closed_at,
                available_at=closed_at,
                first_seen_at=closed_at,
                revision_known_at=closed_at,
                source_id="dev_only_fixture",
                rights_snapshot_hash=request.rights_snapshot_hash,
                content_hash=hashlib.sha256(str(index).encode()).hexdigest(),
                bar=bar,
            )
        )
    return result


def _page(
    request: BehaviorComputeRequest,
    revisions: list[MarketBarRevision],
) -> MarketDataRevisionPage:
    return MarketDataRevisionPage(
        schema_version="1.0",
        items=revisions,
        has_more=False,
        as_of=request.context.as_of,
        manifest=MarketDataManifest(
            status=MarketDataManifestStatus.INCOMPLETE,
            requested_from=request.window_start,
            requested_until=request.window_end,
            as_of=request.context.as_of,
            row_count=len(revisions),
            source_id="repository_query",
            rights_snapshot_hash=request.rights_snapshot_hash,
            content_hash=request.data_manifest_hash,
            gap_reason_code=(
                "OBJECT_STORE_NOT_MATERIALIZED" if revisions else "MARKET_DATA_NOT_FOUND"
            ),
        ),
    )


def _run_worker(
    *,
    monkeypatch: pytest.MonkeyPatch,
    request: BehaviorComputeRequest,
    page: MarketDataRevisionPage,
) -> BehaviorPressureSnapshotV1_0:
    class FakeEngine:
        async def dispose(self) -> None:
            return None

    class FakeMarketRepository:
        def __init__(self, _engine: FakeEngine) -> None:
            pass

        async def query_revisions(self, _request: object) -> MarketDataRevisionPage:
            return page

    class FakeUnitOfWork:
        connection = object()

        def __init__(self, _engine: FakeEngine) -> None:
            pass

        async def __aenter__(self) -> FakeUnitOfWork:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    class FakeBehaviorRepository:
        def __init__(self, _bind: object) -> None:
            pass

        async def query_assessments(self, query: object) -> BehaviorPressurePage:
            if request.mode is not PressureMode.INTRADAY_10M or not page.items:
                return BehaviorPressurePage(
                    schema_version="1.0",
                    items=[],
                    as_of=request.context.as_of,
                )
            dev_inputs = validated_dev_only_behavior_inputs(request=request, page=page)
            assert dev_inputs is not None
            prior_as_of = request.context.as_of - timedelta(days=1)
            prior_request = request.model_copy(
                update={
                    "mode": PressureMode.POST_CLOSE,
                    "window_start": request.window_start - timedelta(days=1),
                    "window_end": request.window_end - timedelta(days=1),
                    "context": request.context.model_copy(
                        update={"as_of": prior_as_of, "requested_at": prior_as_of}
                    ),
                }
            )
            prior_score = compose_behavior_pressure(
                cluster_scores=dev_inputs.cluster_scores,
                reference_raw_totals=dev_inputs.reference_raw_totals,
                reference_population_hash=dev_inputs.reference_population_hash,
            )
            prior_snapshot = tasks.materialize_snapshot(
                request=prior_request,
                score_result=prior_score,
                max_input_revision_known_at=prior_as_of,
            )
            return BehaviorPressurePage(
                schema_version="1.0",
                items=[prior_snapshot],
                as_of=request.context.as_of,
            )

    class FakeService:
        def __init__(self, _repository: object, _publisher: object) -> None:
            pass

        async def persist(
            self,
            *,
            request: BehaviorComputeRequest,
            snapshot: BehaviorPressureSnapshotV1_0,
            trading_date: object,
            maintain_transition_state: bool = True,
        ) -> BehaviorPressureSnapshotV1_0:
            del request, trading_date, maintain_transition_state
            return snapshot

    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="test",
            enable_r1_fixture=True,
            enable_r1_dev_live=False,
            database_url=SecretStr("postgresql+asyncpg://local/test"),
        ),
    )
    monkeypatch.setattr(tasks, "create_async_engine", lambda _url: FakeEngine())
    monkeypatch.setattr(tasks, "PostgresMarketDataRepository", FakeMarketRepository)
    monkeypatch.setattr(tasks, "PostgresUnitOfWork", FakeUnitOfWork)
    monkeypatch.setattr(
        tasks,
        "PostgresBehaviorPressureRepository",
        FakeBehaviorRepository,
    )
    monkeypatch.setattr(
        tasks,
        "PostgresBehaviorOutboxPublisher",
        lambda _uow: object(),
    )
    monkeypatch.setattr(tasks, "BehaviorPressureApplicationService", FakeService)
    result = asyncio.run(tasks._execute_behavior_compute_async(request))
    assert isinstance(result, BehaviorPressureSnapshotV1_0)
    return result


def test_dev_only_inputs_require_explicit_complete_market_revisions() -> None:
    request = _request()
    revisions = _revisions(request)
    first = build_dev_only_behavior_inputs(revisions=revisions)
    second = build_dev_only_behavior_inputs(revisions=list(reversed(revisions)))

    assert build_dev_only_behavior_inputs(revisions=[]) is None
    assert first == second
    assert first is not None
    assert first.release_tier == "DEV_ONLY"
    assert set(first.cluster_scores) == set(PressureClusterName)
    assert all(0 <= score <= 100 for score in first.cluster_scores.values())
    assert len(first.reference_raw_totals) == 1000


def test_behavior_market_query_excludes_the_previous_window_close() -> None:
    request = _request()

    query = tasks._behavior_market_revision_query(request)

    assert query.effective_from == request.window_start + timedelta(microseconds=1)
    assert query.effective_until == request.window_end
    assert query.context == request.context
    assert query.instrument_ids == [request.instrument_id]


def test_dev_only_inputs_reject_manifest_or_rights_lineage_mismatch() -> None:
    request = _request()
    page = _page(request, _revisions(request))
    wrong_manifest = page.model_copy(
        update={"manifest": page.manifest.model_copy(update={"content_hash": "d" * 64})}
    )
    wrong_rights = page.model_copy(
        update={"manifest": page.manifest.model_copy(update={"rights_snapshot_hash": "e" * 64})}
    )

    assert validated_dev_only_behavior_inputs(request=request, page=page) is not None
    assert validated_dev_only_behavior_inputs(request=request, page=wrong_manifest) is None
    assert validated_dev_only_behavior_inputs(request=request, page=wrong_rights) is None


def test_worker_without_market_revisions_abstains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    snapshot = _run_worker(
        monkeypatch=monkeypatch,
        request=request,
        page=_page(request, []),
    )

    assert snapshot.availability is IndexAvailability.ABSTAINED
    assert snapshot.score is None
    assert snapshot.completeness_percent == 0


def test_worker_scores_only_manifest_and_rights_bound_dev_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    snapshot = _run_worker(
        monkeypatch=monkeypatch,
        request=request,
        page=_page(request, _revisions(request)),
    )

    assert snapshot.availability is IndexAvailability.READY
    assert snapshot.score is not None and 0 <= snapshot.score <= 100
    assert snapshot.completeness_percent == 100
    assert {item.cluster for item in snapshot.clusters if item.inherited_from_post_close} == {
        PressureClusterName.REFERENCE_COST_EXTENSION,
        PressureClusterName.SHORT_PATH_EXTENSION,
        PressureClusterName.PRICE_IMPACT,
        PressureClusterName.SPREAD_PROXY,
        PressureClusterName.DOWNSIDE_TAIL_ASYMMETRY,
    }


def test_worker_post_close_path_loads_candidates_and_builds_exact_analog_summary() -> None:
    request = _request(mode=PressureMode.POST_CLOSE)
    dev_inputs = build_dev_only_behavior_inputs(revisions=_revisions(request))
    assert dev_inputs is not None
    score = compose_behavior_pressure(
        cluster_scores=dev_inputs.cluster_scores,
        reference_raw_totals=dev_inputs.reference_raw_totals,
        reference_population_hash=dev_inputs.reference_population_hash,
    )
    selection_as_of = request.context.as_of
    candidates = [
        HistoricalAnalogCandidate(
            assessment_id=uuid4(),
            as_of=selection_as_of - timedelta(days=100 + index),
            assessment_revision_known_at=selection_as_of - timedelta(days=99 + index),
            vector=[float(index) / 100, *([0.0] * 13)],
            approximate_distance=float(index),
        )
        for index in range(50)
    ]

    class FakeRepository:
        async def query_analog_candidates(
            self,
            _query: object,
        ) -> HistoricalAnalogCandidatePage:
            return HistoricalAnalogCandidatePage(
                schema_version="1.0",
                items=candidates,
                selection_as_of=selection_as_of,
            )

        async def load_analog_observations(
            self,
            *,
            assessment_ids: list[object],
            selection_as_of: datetime,
        ) -> list[PersistedAnalogObservation]:
            del assessment_ids
            return [
                PersistedAnalogObservation(
                    assessment_id=item.assessment_id,
                    instrument_id=f"XSHG:PEER{index:04d}",
                    trading_day_index=index,
                    as_of=item.as_of,
                    assessment_revision_known_at=item.assessment_revision_known_at,
                    label_available_at=selection_as_of - timedelta(days=1),
                    vector=tuple(item.vector),
                    outcomes={
                        "return_1d": Decimal("0.01"),
                        "return_3d": Decimal("0.02"),
                        "industry_excess": Decimal("0.005"),
                        "mae": Decimal("-0.03"),
                        "mfe": Decimal("0.04"),
                    },
                )
                for index, item in enumerate(candidates)
            ]

    summary = asyncio.run(
        tasks._load_post_close_analogs(
            repository=cast(PostgresBehaviorPressureRepository, FakeRepository()),
            request=request,
            score_result=score,
        )
    )

    assert summary.state is HistoricalAnalogState.READY
    assert summary.sample_count == 50
    assert len(summary.ranges) == 5
