import io
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from apps.refresh import entrypoint
from apps.refresh.main import _database_url, _parser
from apps.runtime import coordinator as coordinator_module
from apps.runtime.behavior_semantics import BEHAVIOR_SEMANTICS_HASH
from apps.runtime.coordinator import ContinuousPreviewCoordinator, RefreshResult
from apps.runtime.research import RuntimeResearchEnricher
from apps.scheduler.requests import build_dev_live_ingest_requests, trading_premarket_window
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.contract.pae.test_public_snapshot_contracts import (
    _canonical_bytes,
    _materialize_bundle,
    _replace_asset_and_reseal,
)

from stock_assistant.config.settings import Settings
from stock_assistant.contracts.behavior import (
    BehaviorPressurePage,
    BehaviorPressureQuery,
    InterpretationOrigin,
    InterpretationStatus,
)
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.event_views import EventEvidenceViewV1_2
from stock_assistant.contracts.portfolio import R1_FIXED_TARGET_INSTRUMENT_ID
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
    SourceAdvanceState,
    SourceCoverageStatus,
)
from stock_assistant.contracts.public_snapshots import (
    PublicCapabilityRunState,
    PublicCapabilityRunStatus,
    PublicRunState,
    PublicSnapshotCompleteness,
    PublicSnapshotManifestV1_1,
    PublicSnapshotPointerV1,
    PublicSourceGrade,
    validate_public_snapshot_bundle,
)
from stock_assistant.contracts.sectors import (
    ExchangeCalendarQuery,
    ExchangeCalendarResult,
    TradingDay,
    TradingDayStatus,
)
from stock_assistant.modules.briefings.degraded_preview import (
    DegradedPreviewDecision,
    DegradedPreviewReason,
    DegradedPreviewState,
)
from stock_assistant.modules.briefings.projection import (
    briefing_section_for_event,
    project_briefing_item,
)
from stock_assistant.platform.calendars import ExchangeCalendarsXshgAdapter
from stock_assistant.platform.publication.public_safety import PublicSafetyError, scan_public_json
from stock_assistant.platform.publication.s3_snapshots import (
    CurrentSnapshotMissing,
    S3SnapshotPublisher,
    SnapshotPublicationError,
    SnapshotPublicationResult,
)
from stock_assistant.platform.publication.snapshot_bundle import (
    SnapshotBuildError,
    SnapshotDocuments,
    build_snapshot_documents,
)
from stock_assistant.ports.persistence import BehaviorPressureRepositoryPort

NOW = datetime(2026, 8, 3, 0, 55, tzinfo=UTC)


class _Calendar:
    def sessions_in_range(self, _start: object, _end: object) -> tuple[date, ...]:
        return (date(2026, 7, 31), date(2026, 8, 3))

    def session_open(self, session: object) -> datetime:
        value = cast(date, session)
        return datetime(value.year, value.month, value.day, 1, 30, tzinfo=UTC)

    def session_break_start(self, session: object) -> datetime | None:
        if session == date(2026, 8, 3):
            return None
        value = cast(date, session)
        return datetime(value.year, value.month, value.day, 3, 30, tzinfo=UTC)

    def session_break_end(self, session: object) -> datetime | None:
        if session == date(2026, 8, 3):
            return None
        value = cast(date, session)
        return datetime(value.year, value.month, value.day, 5, 0, tzinfo=UTC)

    def session_close(self, session: object) -> datetime:
        value = cast(date, session)
        hour = 3 if value == date(2026, 8, 3) else 7
        return datetime(value.year, value.month, value.day, hour, 0, tzinfo=UTC)


def _context() -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        as_of=NOW,
        requested_at=NOW,
    )


def _research_briefing(briefing: PremarketBriefingViewV1_2) -> PremarketBriefingViewV1_3:
    return __import__("asyncio").run(
        RuntimeResearchEnricher(
            settings=Settings(),
            behavior_repository=None,
        ).enrich(
            briefing=briefing,
            context=OperationContext(
                schema_version="1.0",
                as_of=briefing.as_of,
                requested_at=briefing.generated_at,
            ),
            evidence_ids=[],
        )
    )


def test_research_enricher_publishes_v1_3_with_safe_model_fallback() -> None:
    legacy = validate_public_snapshot_bundle(
        pointer_document=_official_bundle().pointer_document,
        manifest_document=_official_bundle().manifest_document,
        asset_documents=_official_bundle().asset_documents,
    ).briefing
    assert isinstance(legacy, PremarketBriefingViewV1_2)
    legacy = legacy.model_copy(
        update={"instrument": legacy.instrument.model_copy(update={"symbol": "603986.SH"})}
    )

    enriched = _research_briefing(legacy)

    assert enriched.schema_version == "1.3"
    assert enriched.instrument.symbol == "603986"
    assert enriched.flash_interpretation.origin is InterpretationOrigin.RULES
    assert enriched.flash_interpretation.status is InterpretationStatus.FALLBACK
    assert enriched.pro_interpretation is None
    assert enriched.behavior_pressure_summary.score is None
    assert enriched.plain_language_update.checked_count == len(legacy.source_coverage)


def test_research_enricher_rejects_split_instrument_identity() -> None:
    legacy = validate_public_snapshot_bundle(
        pointer_document=_official_bundle().pointer_document,
        manifest_document=_official_bundle().manifest_document,
        asset_documents=_official_bundle().asset_documents,
    ).briefing
    legacy = legacy.model_copy(
        update={"instrument": legacy.instrument.model_copy(update={"exchange": "XNYS"})}
    )

    with pytest.raises(ValueError, match="fixed XSHG:603986 identity"):
        _research_briefing(legacy)


def test_research_enricher_prefers_current_behavior_semantics() -> None:
    from tests.contract.pae.test_r1_research_contracts import _snapshot

    current_hash = BEHAVIOR_SEMANTICS_HASH
    stale = _snapshot()
    current = stale.model_copy(
        update={
            "assessment_id": UUID("4a8a881c-f87e-56b2-882a-eb4474b8ab57"),
            "pit_lineage": stale.pit_lineage.model_copy(
                update={"comparison_semantics_hash": current_hash}
            ),
        }
    )
    captured: list[BehaviorPressureQuery] = []

    class FakeRepository:
        async def query_assessments(
            self,
            request: BehaviorPressureQuery,
        ) -> BehaviorPressurePage:
            captured.append(request)
            return BehaviorPressurePage(
                schema_version="1.0",
                items=[stale, current],
                as_of=current.pit_lineage.assessment_revision_known_at,
            )

    selected = __import__("asyncio").run(
        RuntimeResearchEnricher(
            settings=Settings(),
            behavior_repository=cast(BehaviorPressureRepositoryPort, FakeRepository()),
        )._latest_behavior(
            OperationContext(
                schema_version="1.0",
                as_of=current.pit_lineage.assessment_revision_known_at,
                requested_at=current.pit_lineage.assessment_revision_known_at,
            )
        )
    )

    assert captured[0].limit == 100
    assert selected == current


def test_xshg_adapter_emits_every_natural_day_and_detects_partial_session() -> None:
    adapter = ExchangeCalendarsXshgAdapter(calendar_factory=_Calendar)
    result = __import__("asyncio").run(
        adapter.get_calendar(
            ExchangeCalendarQuery(
                schema_version="1.0",
                context=_context(),
                exchanges=["XSHG"],
                date_from=date(2026, 7, 31),
                date_to=date(2026, 8, 3),
            )
        )
    )

    assert [item.trading_date for item in result.days] == [
        date(2026, 7, 31),
        date(2026, 8, 1),
        date(2026, 8, 2),
        date(2026, 8, 3),
    ]
    assert [item.status for item in result.days] == [
        TradingDayStatus.OPEN,
        TradingDayStatus.CLOSED,
        TradingDayStatus.CLOSED,
        TradingDayStatus.PARTIAL,
    ]
    assert all(item.first_seen_at == NOW for item in result.days)


def test_request_window_uses_previous_xshg_session_instead_of_previous_weekday() -> None:
    days = (
        TradingDay(
            schema_version="1.0",
            exchange="XSHG",
            trading_date=date(2026, 7, 30),
            status=TradingDayStatus.OPEN,
            first_seen_at=NOW,
            calendar_version="fixture",
        ),
        TradingDay(
            schema_version="1.0",
            exchange="XSHG",
            trading_date=date(2026, 7, 31),
            status=TradingDayStatus.CLOSED,
            first_seen_at=NOW,
            calendar_version="fixture",
        ),
        TradingDay(
            schema_version="1.0",
            exchange="XSHG",
            trading_date=date(2026, 8, 3),
            status=TradingDayStatus.OPEN,
            first_seen_at=NOW,
            calendar_version="fixture",
        ),
    )

    window_start, window_end = trading_premarket_window(NOW, days)

    assert window_start.isoformat() == "2026-07-30T15:00:00+08:00"
    assert window_end.isoformat() == "2026-08-03T08:55:00+08:00"


def test_request_window_before_cutoff_uses_latest_complete_xshg_session() -> None:
    before_cutoff = datetime(2026, 8, 3, 0, 54, 59, tzinfo=UTC)
    days = tuple(
        TradingDay(
            schema_version="1.0",
            exchange="XSHG",
            trading_date=trading_date,
            status=TradingDayStatus.OPEN,
            first_seen_at=before_cutoff,
            calendar_version="fixture",
        )
        for trading_date in (date(2026, 7, 29), date(2026, 7, 30), date(2026, 8, 3))
    )

    window_start, window_end = trading_premarket_window(before_cutoff, days)

    assert window_start.isoformat() == "2026-07-29T15:00:00+08:00"
    assert window_end.isoformat() == "2026-07-30T08:55:00+08:00"


def test_preview_profile_is_explicit_and_production_gate_remains_fail_closed() -> None:
    preview = Settings(
        _env_file=None,
        app_env="preview",
        enable_r1_fixture=False,
        enable_r1_dev_live=True,
        r1_runtime_mode="dev_live",
        public_snapshot_bucket="preview-example-bucket",
        public_base_url="https://preview.example.com",
        product_commit="a" * 40,
    )
    preview.assert_safe_production()

    production = preview.model_copy(update={"app_env": "production"})
    with pytest.raises(ValueError, match="cannot be enabled in production"):
        production.assert_safe_production()


def test_database_url_uses_sqlalchemy_escaping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "stock_assistant")
    monkeypatch.setenv("DB_USERNAME", "stock_preview")
    monkeypatch.setenv("DB_PASSWORD", "a@b:c/d?#%")

    rendered = _database_url(master=False)

    assert "a@b:c/d?#%" not in rendered
    assert "stock_preview" in rendered
    assert rendered.endswith("?ssl=require")


def test_refresh_entrypoint_redacts_unstable_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_argv: list[str] | None = None) -> int:
        raise RuntimeError("password=must-not-reach-logs")

    monkeypatch.setattr(entrypoint, "main", fail)

    assert entrypoint.run(["seed"]) == 1
    captured = capsys.readouterr()
    assert "REFRESH_OPERATION_FAILED" in captured.err
    assert "must-not-reach-logs" not in captured.err


@pytest.mark.parametrize("operation", ["seed-last-session", "behavior-last-session"])
def test_refresh_parser_admits_operator_only_last_session_operations(operation: str) -> None:
    assert _parser().parse_args([operation]).operation == operation


class _ClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.versions: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.current_versions: dict[str, str] = {}
        self.etags: dict[str, str] = {}
        self.put_keys: list[str] = []
        self.put_requests: list[dict[str, object]] = []
        self.after_current_put: Callable[[_FakeS3, bytes], None] | None = None
        self.current_put_version: object | None = None
        self.omit_current_put_version = False
        self.omit_current_get_version = False
        self.versioned_get_override: str | None = None
        self.first_pointer_error_code: str | None = None
        self.list_error_code: str | None = "AccessDenied"
        self.listed_current_keys: list[str] | None = None
        self.list_requests: list[dict[str, object]] = []

    def write_concurrent(self, *, key: str, body: bytes) -> str:
        return self._write(key=key, body=body)

    def _write(self, *, key: str, body: bytes) -> str:
        version_id = str(sum(1 for stored_key, _ in self.versions if stored_key == key) + 1)
        etag = f'"{__import__("hashlib").sha256(body).hexdigest()[:32]}"'
        self.objects[key] = body
        self.etags[key] = etag
        self.current_versions[key] = version_id
        self.versions[(key, version_id)] = (body, etag)
        return version_id

    def put_object(self, **kwargs: object) -> dict[str, object]:
        key = cast(str, kwargs["Key"])
        body = cast(bytes, kwargs["Body"])
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _ClientError("PreconditionFailed")
        expected = kwargs.get("IfMatch")
        if expected is not None and expected != self.etags.get(key):
            raise _ClientError("PreconditionFailed")
        version_id = self._write(key=key, body=body)
        self.put_keys.append(key)
        self.put_requests.append(dict(kwargs))
        response_version: object = version_id
        if key == "data/current.json" and self.current_put_version is not None:
            response_version = self.current_put_version
        response = {"ETag": self.etags[key], "VersionId": response_version}
        if key == "data/current.json" and self.omit_current_put_version:
            del response["VersionId"]
        if key == "data/current.json" and self.after_current_put is not None:
            self.after_current_put(self, body)
        return response

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = cast(str, kwargs["Key"])
        version_id = cast(str | None, kwargs.get("VersionId"))
        if (
            key == "data/current.json"
            and version_id is None
            and key not in self.objects
            and self.first_pointer_error_code is not None
        ):
            raise _ClientError(self.first_pointer_error_code)
        resolved_version = version_id or self.current_versions.get(key)
        stored = self.versions.get((key, resolved_version)) if resolved_version else None
        if stored is None:
            raise _ClientError("NoSuchKey")
        body, etag = stored
        returned_version = (
            self.versioned_get_override
            if version_id is not None and self.versioned_get_override is not None
            else resolved_version
        )
        if version_id is None and key == "data/current.json" and self.omit_current_get_version:
            returned_version = None
        return {
            "Body": io.BytesIO(body),
            "ETag": etag,
            "VersionId": returned_version,
        }

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        self.list_requests.append(dict(kwargs))
        if self.list_error_code is not None:
            raise _ClientError(self.list_error_code)
        keys = (
            self.listed_current_keys
            if self.listed_current_keys is not None
            else [key for key in self.objects if key.startswith(cast(str, kwargs["Prefix"]))]
        )
        limited = keys[: cast(int, kwargs["MaxKeys"])]
        return {
            "IsTruncated": len(keys) > len(limited),
            "KeyCount": len(limited),
            "Contents": [{"Key": key} for key in limited],
        }


def _official_bundle() -> SnapshotDocuments:
    pointer, manifest, assets = _materialize_bundle("valid_dev_live")
    evidence_path = next(path for path in assets if "/evidence/" in path)
    evidence_payload = json.loads(assets[evidence_path])
    evidence_payload["instrument_ids"] = ["XSHG:603986"]
    evidence_payload["evidence"][0]["source_url"] = "https://www.gov.cn/zhengce/content/2026-08/04"
    pointer, manifest, assets = _replace_asset_and_reseal(
        pointer,
        manifest,
        assets,
        asset_path=evidence_path,
        replacement=_canonical_bytes(evidence_payload),
    )
    evidence = EventEvidenceViewV1_2.model_validate(evidence_payload)
    briefing_path = next(path for path in assets if path.endswith("/briefing.json"))
    briefing_payload = json.loads(assets[briefing_path])
    briefing_payload["instrument"] = {
        "instrument_id": "XSHG:603986",
        "symbol": "603986",
        "exchange": "XSHG",
        "market": "CN",
        "asset_class": "equity",
        "currency": "CNY",
    }
    section_name = briefing_section_for_event(evidence.event_type)
    briefing_payload["sections"] = {
        name: {"items": [project_briefing_item(evidence).model_dump(mode="json")]}
        if name == section_name
        else {"items": []}
        for name in (
            "statutory_announcements",
            "financial_changes",
            "market_snapshot",
            "company_industry_news",
            "macro_flashes",
        )
    }
    pointer, manifest, assets = _replace_asset_and_reseal(
        pointer,
        manifest,
        assets,
        asset_path=briefing_path,
        replacement=_canonical_bytes(briefing_payload),
    )
    return SnapshotDocuments(
        pointer=PublicSnapshotPointerV1.model_validate_json(pointer),
        manifest=PublicSnapshotManifestV1_1.model_validate_json(manifest),
        pointer_document=pointer,
        manifest_document=manifest,
        asset_documents=MappingProxyType(assets),
    )


def _mutate_briefing_bundle(
    snapshot: SnapshotDocuments,
    mutate: Callable[[dict[str, object]], None],
) -> SnapshotDocuments:
    briefing_path = next(
        path for path in snapshot.asset_documents if path.endswith("/briefing.json")
    )
    payload = json.loads(snapshot.asset_documents[briefing_path])
    mutate(payload)
    pointer, manifest, assets = _replace_asset_and_reseal(
        snapshot.pointer_document,
        snapshot.manifest_document,
        dict(snapshot.asset_documents),
        asset_path=briefing_path,
        replacement=_canonical_bytes(payload),
    )
    return SnapshotDocuments(
        pointer=PublicSnapshotPointerV1.model_validate_json(pointer),
        manifest=PublicSnapshotManifestV1_1.model_validate_json(manifest),
        pointer_document=pointer,
        manifest_document=manifest,
        asset_documents=MappingProxyType(assets),
    )


def _briefing_item_payload(payload: dict[str, object]) -> dict[str, object]:
    sections = cast(dict[str, object], payload["sections"])
    statutory = cast(dict[str, object], sections["statutory_announcements"])
    items = cast(list[dict[str, object]], statutory["items"])
    return items[0]


class _CoordinatorPublisher:
    def __init__(self, current: object | None = None) -> None:
        self.current = current
        self.published: list[object] = []

    def read_current(self) -> object:
        if self.current is None:
            raise CurrentSnapshotMissing("missing")
        return self.current

    def publish(self, snapshot: object) -> SnapshotPublicationResult:
        self.published.append(snapshot)
        return SnapshotPublicationResult(
            snapshot_id="10000000-0000-4000-8000-000000000001",
            pointer_switched=True,
            idempotent_winner=False,
            current_etag='"etag"',
            current_version_id="1",
        )


def _coordinator(
    *,
    publisher: _CoordinatorPublisher | None = None,
    public_smoke: Callable[..., dict[str, str]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ContinuousPreviewCoordinator:
    return ContinuousPreviewCoordinator(
        engine=cast(AsyncEngine, object()),
        calendar=cast(ExchangeCalendarsXshgAdapter, object()),
        publisher=cast(S3SnapshotPublisher, publisher or _CoordinatorPublisher()),
        product_commit="a" * 40,
        advisory_lock_id=731055,
        freshness_grace_minutes=10,
        public_base_url="https://preview.example.com",
        public_smoke=public_smoke or (lambda **_kwargs: {"status": "ok"}),
        clock=clock or (lambda: datetime.now(UTC)),
    )


def test_s3_publisher_writes_pointer_last_after_full_readback() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()

    result = S3SnapshotPublisher(client=client, bucket="preview").publish(snapshot)

    assert result.pointer_switched is True
    assert client.put_keys[-1] == "data/current.json"
    assert client.objects["data/current.json"] == snapshot.pointer_document
    assert client.put_keys.index(snapshot.pointer.manifest_path) > max(
        client.put_keys.index(path) for path in snapshot.asset_documents
    )


@pytest.mark.parametrize("missing_code", ["404", "NoSuchKey"])
def test_s3_publisher_treats_only_not_found_as_first_pointer(
    missing_code: str,
) -> None:
    client = _FakeS3()
    client.first_pointer_error_code = missing_code
    result = S3SnapshotPublisher(client=client, bucket="preview").publish(_official_bundle())
    assert result.pointer_switched is True
    current_request = next(
        request for request in client.put_requests if request["Key"] == "data/current.json"
    )
    assert current_request["IfNoneMatch"] == "*"


def test_s3_publisher_never_treats_access_denied_as_first_pointer() -> None:
    client = _FakeS3()
    client.first_pointer_error_code = "403"
    with pytest.raises(SnapshotPublicationError, match="CURRENT_POINTER_ABSENCE_UNPROVEN"):
        S3SnapshotPublisher(client=client, bucket="preview").publish(_official_bundle())
    assert "data/current.json" not in client.put_keys


def test_s3_publisher_uses_exact_list_fallback_to_prove_403_pointer_absence() -> None:
    client = _FakeS3()
    client.first_pointer_error_code = "403"
    client.list_error_code = None
    result = S3SnapshotPublisher(client=client, bucket="preview").publish(_official_bundle())
    assert result.pointer_switched is True
    assert client.list_requests == [
        {"Bucket": "preview", "Prefix": "data/current.json", "MaxKeys": 1}
    ]
    current_request = next(
        request for request in client.put_requests if request["Key"] == "data/current.json"
    )
    assert current_request["IfNoneMatch"] == "*"


def test_s3_publisher_never_treats_403_as_missing_when_exact_list_is_nonempty() -> None:
    client = _FakeS3()
    client.first_pointer_error_code = "AccessDenied"
    client.list_error_code = None
    client.listed_current_keys = ["data/current.json"]
    with pytest.raises(SnapshotPublicationError, match="SNAPSHOT_REMOTE_READ_FAILED"):
        S3SnapshotPublisher(client=client, bucket="preview").publish(_official_bundle())
    assert "data/current.json" not in client.put_keys


def test_s3_publisher_validates_and_cas_restores_exact_pointer_version() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    publisher = S3SnapshotPublisher(client=client, bucket="preview")
    published = publisher.publish(snapshot)
    assert published.current_version_id is not None
    client.put_object(Bucket="preview", Key="data/current.json", Body=b"{}")

    target, restored_version = publisher.restore_pointer_version(
        version_id=published.current_version_id,
        expected_snapshot_id=str(snapshot.pointer.snapshot_id),
    )

    assert target.validated.pointer == snapshot.pointer
    assert restored_version is not None
    assert client.objects["data/current.json"] == snapshot.pointer_document


def test_snapshot_documents_reject_model_bytes_split_brain_and_copy_assets() -> None:
    snapshot = _official_bundle()
    with pytest.raises(SnapshotBuildError, match="SPLIT_BRAIN"):
        SnapshotDocuments(
            pointer=snapshot.pointer.model_copy(
                update={"snapshot_id": UUID("20000000-0000-4000-8000-000000000099")}
            ),
            manifest=snapshot.manifest,
            pointer_document=snapshot.pointer_document,
            manifest_document=snapshot.manifest_document,
            asset_documents=snapshot.asset_documents,
        )

    mutable_assets = dict(snapshot.asset_documents)
    copied = SnapshotDocuments(
        pointer=snapshot.pointer,
        manifest=snapshot.manifest,
        pointer_document=snapshot.pointer_document,
        manifest_document=snapshot.manifest_document,
        asset_documents=MappingProxyType(mutable_assets),
    )
    asset_path = next(iter(mutable_assets))
    original = copied.asset_documents[asset_path]
    mutable_assets[asset_path] = b"mutated"
    assert copied.asset_documents[asset_path] == original


def test_s3_publisher_preserves_quoted_etag_for_cas_and_accepts_immutable_replay() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    publisher = S3SnapshotPublisher(client=client, bucket="preview")
    first = publisher.publish(snapshot)
    second = publisher.publish(snapshot)

    current_requests = [
        request for request in client.put_requests if request["Key"] == "data/current.json"
    ]
    assert first.current_etag is not None
    assert first.current_etag.startswith('"') and first.current_etag.endswith('"')
    assert current_requests[-1]["IfMatch"] == first.current_etag
    assert second.pointer_switched is True


def test_s3_publisher_rejects_immutable_key_collision() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    publisher = S3SnapshotPublisher(client=client, bucket="preview")
    publisher.publish(snapshot)
    asset_path = next(iter(snapshot.asset_documents))
    client.write_concurrent(key=asset_path, body=b'{"different":true}')

    with pytest.raises(SnapshotPublicationError, match="IMMUTABLE_KEY_COLLISION"):
        publisher.publish(snapshot)


def test_s3_publisher_rejects_different_post_put_winner() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    client.after_current_put = lambda value, _body: value.write_concurrent(
        key="data/current.json",
        body=b"{}",
    )

    with pytest.raises(SnapshotPublicationError, match="CONCURRENT_CONFLICT"):
        S3SnapshotPublisher(client=client, bucket="preview").publish(snapshot)


def test_s3_publisher_classifies_same_bytes_different_version_as_idempotent_winner() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    client.after_current_put = lambda value, body: value.write_concurrent(
        key="data/current.json",
        body=body,
    )

    result = S3SnapshotPublisher(client=client, bucket="preview").publish(snapshot)

    assert result.pointer_switched is False
    assert result.idempotent_winner is True
    assert result.current_version_id == client.current_versions["data/current.json"]


@pytest.mark.parametrize("mode", ["missing", "null"])
def test_s3_publisher_rejects_missing_or_null_pointer_version_receipt(mode: str) -> None:
    client = _FakeS3()
    if mode == "missing":
        client.omit_current_put_version = True
    else:
        client.current_put_version = "null"

    with pytest.raises(SnapshotPublicationError, match="VERSION_ID_MISSING"):
        S3SnapshotPublisher(client=client, bucket="preview").publish(_official_bundle())


def test_s3_publisher_rejects_missing_current_identity_and_wrong_historical_version() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    publisher = S3SnapshotPublisher(client=client, bucket="preview")
    published = publisher.publish(snapshot)
    assert published.current_version_id is not None

    client.omit_current_get_version = True
    with pytest.raises(SnapshotPublicationError, match="VERSION_ID_MISSING"):
        publisher.publish(snapshot)
    client.omit_current_get_version = False
    client.versioned_get_override = "wrong-version"
    with pytest.raises(SnapshotPublicationError, match="REMOTE_VERSION_MISMATCH"):
        publisher.read_pointer_version(version_id=published.current_version_id)


def test_s3_rollback_rejects_post_put_concurrent_overwrite() -> None:
    client = _FakeS3()
    snapshot = _official_bundle()
    publisher = S3SnapshotPublisher(client=client, bucket="preview")
    published = publisher.publish(snapshot)
    assert published.current_version_id is not None
    client.write_concurrent(key="data/current.json", body=b"{}")
    client.after_current_put = lambda value, _body: value.write_concurrent(
        key="data/current.json",
        body=b"{}",
    )

    with pytest.raises(SnapshotPublicationError, match="CONCURRENT_CONFLICT"):
        publisher.restore_pointer_version(
            version_id=published.current_version_id,
            expected_snapshot_id=str(snapshot.pointer.snapshot_id),
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("headline", "tampered headline"),
        ("fact_summary", "tampered facts"),
        ("inference", "tampered inference"),
        ("change_type", "update"),
        ("published_at", "2026-07-25T00:00:01Z"),
        ("first_seen_at", "2026-07-25T00:05:01Z"),
        ("source_roles", ["discovery"]),
        ("event_revision", 2),
        ("evidence_ids", ["00000000-0000-0000-0000-000000009999"]),
    ],
)
def test_publisher_rejects_resealed_briefing_field_projection_tampering(
    field: str,
    replacement: object,
) -> None:
    snapshot = _mutate_briefing_bundle(
        _official_bundle(),
        lambda payload: _briefing_item_payload(payload).__setitem__(field, replacement),
    )
    client = _FakeS3()

    with pytest.raises(ValueError, match="exact evidence projection"):
        S3SnapshotPublisher(client=client, bucket="preview").publish(snapshot)
    assert client.put_keys == []


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_section"])
def test_publisher_rejects_resealed_briefing_event_set_and_section_tampering(
    mutation: str,
) -> None:
    def mutate(payload: dict[str, object]) -> None:
        sections = cast(dict[str, object], payload["sections"])
        statutory = cast(dict[str, object], sections["statutory_announcements"])
        items = cast(list[dict[str, object]], statutory["items"])
        original = dict(items[0])
        if mutation == "missing":
            items.clear()
        elif mutation == "extra":
            original["event_id"] = "00000000-0000-0000-0000-000000009999"
            items.append(original)
        else:
            macro = cast(dict[str, object], sections["macro_flashes"])
            macro_items = cast(list[dict[str, object]], macro["items"])
            macro_items.append(original)
            if mutation == "wrong_section":
                items.clear()

    snapshot = _mutate_briefing_bundle(_official_bundle(), mutate)
    client = _FakeS3()
    with pytest.raises(ValueError):
        S3SnapshotPublisher(client=client, bucket="preview").publish(snapshot)
    assert client.put_keys == []


def test_snapshot_builder_preserves_exact_evidence_join_and_canonical_bundle() -> None:
    fixture = _official_bundle()
    validated_fixture = validate_public_snapshot_bundle(
        pointer_document=fixture.pointer_document,
        manifest_document=fixture.manifest_document,
        asset_documents=fixture.asset_documents,
    )
    legacy_briefing = validated_fixture.briefing.model_copy(
        update={
            "instrument": validated_fixture.briefing.instrument.model_copy(
                update={"symbol": "603986.SH"}
            )
        }
    )
    generated_at = legacy_briefing.generated_at
    source_gaps = tuple(
        coverage
        for coverage in validated_fixture.briefing.source_coverage
        if coverage.status is not SourceCoverageStatus.OK
    )
    decision = DegradedPreviewDecision(
        state=DegradedPreviewState.PARTIAL,
        completeness=PublicSnapshotCompleteness.PARTIAL,
        run_state=PublicRunState.DEGRADED,
        briefing=legacy_briefing,
        trading_sessions_old=0,
        reason=DegradedPreviewReason.CURRENT_SOURCE_GAPS,
        source_gaps=source_gaps,
    )
    evidence_by_event_id = dict(validated_fixture.evidence_by_event_id)

    built = build_snapshot_documents(
        decision=decision,
        research_briefing=_research_briefing(legacy_briefing),
        evidence_by_event_id=evidence_by_event_id,
        snapshot_id=UUID("20000000-0000-4000-8000-000000000002"),
        run_id=UUID("30000000-0000-4000-8000-000000000003"),
        product_commit=fixture.pointer.product_commit,
        run_started_at=generated_at - timedelta(seconds=1),
        run_completed_at=generated_at,
        published_at=generated_at,
        fresh_until=generated_at + timedelta(days=1),
    )
    validated = validate_public_snapshot_bundle(
        pointer_document=built.pointer_document,
        manifest_document=built.manifest_document,
        asset_documents=built.asset_documents,
    )

    assert validated.evidence_by_event_id == evidence_by_event_id
    assert validated.briefing.schema_version == "1.3"
    assert validated.briefing.instrument.symbol == "603986"
    assert built.pointer_document.endswith(b"\n")
    assert built.manifest_document.endswith(b"\n")
    assert all(document.endswith(b"\n") for document in built.asset_documents.values())


def test_snapshot_builder_publishes_all_unhealthy_current_as_degraded() -> None:
    fixture = _official_bundle()
    validated_fixture = validate_public_snapshot_bundle(
        pointer_document=fixture.pointer_document,
        manifest_document=fixture.manifest_document,
        asset_documents=fixture.asset_documents,
    )
    failed_coverages = [
        coverage.model_copy(
            update={
                "status": SourceCoverageStatus.UNAVAILABLE,
                "last_success_at": None,
                "gap_reason": "fixture source unavailable",
                "progress": coverage.progress.model_copy(
                    update={
                        "advance_state": SourceAdvanceState.NOT_ADVANCED,
                        "business_watermark": None,
                    }
                ),
            }
        )
        for coverage in validated_fixture.briefing.source_coverage
    ]
    primary = next(
        coverage
        for coverage in failed_coverages
        if (
            coverage.source_id,
            coverage.capability,
        )
        == (
            validated_fixture.briefing.ingestion_progress.source_id,
            validated_fixture.briefing.ingestion_progress.capability,
        )
    )
    briefing = validated_fixture.briefing.model_copy(
        update={
            "source_coverage": failed_coverages,
            "ingestion_progress": validated_fixture.briefing.ingestion_progress.model_copy(
                update={
                    "business_watermark": primary.progress.business_watermark,
                    "batch_audit_id": primary.progress.batch_audit_id,
                    "advance_state": primary.progress.advance_state,
                }
            ),
        }
    )
    decision = DegradedPreviewDecision(
        state=DegradedPreviewState.PARTIAL,
        completeness=PublicSnapshotCompleteness.PARTIAL,
        run_state=PublicRunState.DEGRADED,
        briefing=briefing,
        trading_sessions_old=0,
        reason=DegradedPreviewReason.CURRENT_ALL_SOURCES_UNHEALTHY,
        source_gaps=tuple(failed_coverages),
    )
    generated_at = briefing.generated_at

    built = build_snapshot_documents(
        decision=decision,
        research_briefing=_research_briefing(briefing),
        evidence_by_event_id=dict(validated_fixture.evidence_by_event_id),
        snapshot_id=UUID("21000000-0000-4000-8000-000000000002"),
        run_id=UUID("31000000-0000-4000-8000-000000000003"),
        product_commit=fixture.pointer.product_commit,
        run_started_at=generated_at - timedelta(seconds=1),
        run_completed_at=generated_at,
        published_at=generated_at,
        fresh_until=generated_at + timedelta(days=1),
    )
    validated = validate_public_snapshot_bundle(
        pointer_document=built.pointer_document,
        manifest_document=built.manifest_document,
        asset_documents=built.asset_documents,
    )

    assert validated.manifest.completeness is PublicSnapshotCompleteness.PARTIAL
    assert validated.run_status.status is PublicRunState.DEGRADED
    assert {item.state for item in validated.run_status.capabilities} == {
        PublicCapabilityRunState.FAILED
    }


def test_catch_up_retries_only_active_retryable_source_failures() -> None:
    active_retryable = PublicCapabilityRunStatus(
        source_id="research_preview_market",
        capability="market.snapshot",
        source_grade=PublicSourceGrade.FREE_OFFICIAL_UNVERIFIED,
        state=PublicCapabilityRunState.FAILED,
        safe_reason_code="R1_SOURCE_UNAVAILABLE",
    )
    active_structural = active_retryable.model_copy(
        update={
            "state": PublicCapabilityRunState.BLOCKED,
            "safe_reason_code": "R1_SOURCE_NOT_QUALIFIED",
        }
    )
    inactive_retryable = active_retryable.model_copy(update={"source_id": "inactive_vendor_source"})

    assert ContinuousPreviewCoordinator._active_retryable_source_ids([active_retryable]) == (
        "research_preview_market",
    )
    assert ContinuousPreviewCoordinator._active_retryable_source_ids([active_structural]) == ()
    assert ContinuousPreviewCoordinator._active_retryable_source_ids([inactive_retryable]) == ()


def test_dev_live_request_filter_selects_only_the_retryable_active_source() -> None:
    window = (
        datetime(2026, 7, 31, 7, 0, tzinfo=UTC),
        datetime(2026, 8, 3, 0, 55, tzinfo=UTC),
    )
    requests = build_dev_live_ingest_requests(
        NOW,
        window=window,
        source_ids=("research_preview_market",),
    )

    assert [(item.payload.source_id, item.payload.capability) for item in requests] == [
        ("research_preview_market", "market.snapshot")
    ]
    with pytest.raises(ValueError, match="active sources"):
        build_dev_live_ingest_requests(
            NOW,
            window=window,
            source_ids=("inactive_vendor_source",),
        )


def test_same_session_late_briefing_is_current_and_lkg_query_is_window_bounded() -> None:
    validated = validate_public_snapshot_bundle(
        pointer_document=_official_bundle().pointer_document,
        manifest_document=_official_bundle().manifest_document,
        asset_documents=_official_bundle().asset_documents,
    )
    base = validated.briefing
    window_start = base.comparison_since
    window_end = base.as_of
    run_at = window_end + timedelta(minutes=10)
    late_current = base.model_copy(
        update={"as_of": run_at - timedelta(minutes=1), "generated_at": run_at}
    )
    lkg = base.model_copy(
        update={
            "as_of": window_start,
            "comparison_since": window_start - timedelta(days=1),
            "generated_at": window_start,
        }
    )
    repository = SimpleNamespace(get_latest_briefing=AsyncMock(side_effect=[late_current, lkg]))

    current, previous = __import__("asyncio").run(
        _coordinator()._briefing_candidates(
            repository=repository,
            run_at=run_at,
            window_start=window_start,
            window_end=window_end,
        )
    )

    assert current == late_current
    assert previous == lkg
    assert [call.kwargs["as_of"] for call in repository.get_latest_briefing.await_args_list] == [
        run_at,
        window_start,
    ]


def test_publish_evaluates_candidates_after_briefing_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _official_bundle()
    validated = validate_public_snapshot_bundle(
        pointer_document=snapshot.pointer_document,
        manifest_document=snapshot.manifest_document,
        asset_documents=snapshot.asset_documents,
    )
    initial_run_at = validated.briefing.generated_at
    source_finished_at = initial_run_at + timedelta(seconds=5)
    evaluation_at = initial_run_at + timedelta(seconds=10)
    completed_at = evaluation_at + timedelta(seconds=1)
    current = validated.briefing.model_copy(
        update={
            "as_of": source_finished_at,
            "generated_at": source_finished_at,
            "instrument": validated.briefing.instrument.model_copy(
                update={"instrument_id": R1_FIXED_TARGET_INSTRUMENT_ID}
            ),
        }
    )
    repository = SimpleNamespace(get_latest_briefing=AsyncMock(side_effect=[current, None]))
    publisher = _CoordinatorPublisher()
    clock_values = iter((initial_run_at, evaluation_at, completed_at))
    coordinator = _coordinator(publisher=publisher, clock=lambda: next(clock_values))
    monkeypatch.setattr(coordinator_module, "execute_premarket_brief", AsyncMock())
    monkeypatch.setattr(
        coordinator_module,
        "PostgresR1PremarketRepository",
        lambda _engine: repository,
    )
    monkeypatch.setattr(
        coordinator,
        "_load_evidence",
        AsyncMock(return_value=dict(validated.evidence_by_event_id)),
    )
    monkeypatch.setattr(
        coordinator,
        "_fresh_until",
        lambda **_kwargs: validated.manifest.fresh_until,
    )
    build_calls: list[dict[str, object]] = []

    def build(**kwargs: object) -> SnapshotDocuments:
        build_calls.append(kwargs)
        return snapshot

    monkeypatch.setattr(coordinator_module, "build_snapshot_documents", build)
    calendar = ExchangeCalendarResult(
        schema_version="1.0",
        days=[],
        as_of=initial_run_at,
        calendar_version="test",
    )

    result = __import__("asyncio").run(
        coordinator._publish(
            run_at=initial_run_at,
            calendar_and_window=(
                calendar,
                (validated.briefing.comparison_since, validated.briefing.as_of),
            ),
        )
    )

    assert result.pointer_switched is True
    assert repository.get_latest_briefing.await_args_list[0].kwargs["as_of"] == evaluation_at
    decision = cast(DegradedPreviewDecision, build_calls[0]["decision"])
    assert decision.briefing is current
    assert build_calls[0]["run_started_at"] == initial_run_at
    assert build_calls[0]["run_completed_at"] == completed_at


def test_catch_up_passes_only_retryable_source_ids_to_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = PublicCapabilityRunStatus(
        source_id="research_preview_macro",
        capability="macro.flashes",
        source_grade=PublicSourceGrade.FREE_OFFICIAL_UNVERIFIED,
        state=PublicCapabilityRunState.FAILED,
        safe_reason_code="R1_SOURCE_UNAVAILABLE",
    )
    window = (
        datetime(2026, 7, 31, 7, 0, tzinfo=UTC),
        datetime(2026, 8, 3, 0, 55, tzinfo=UTC),
    )
    current = SimpleNamespace(
        validated=SimpleNamespace(
            manifest=SimpleNamespace(
                data_as_of=window[1],
                snapshot_id=UUID("10000000-0000-4000-8000-000000000001"),
            ),
            run_status=SimpleNamespace(capabilities=[capability]),
        )
    )
    coordinator = _coordinator(publisher=_CoordinatorPublisher(current))
    calendar = cast(ExchangeCalendarResult, object())
    monkeypatch.setattr(
        coordinator,
        "_calendar_and_window",
        AsyncMock(return_value=(calendar, window)),
    )
    ingest = AsyncMock(return_value=RefreshResult(operation="ingest", source_results=1))
    publish = AsyncMock(
        return_value=RefreshResult(
            operation="publish",
            snapshot_id="10000000-0000-4000-8000-000000000001",
            pointer_switched=True,
        )
    )
    monkeypatch.setattr(coordinator, "_ingest", ingest)
    monkeypatch.setattr(coordinator, "_publish", publish)

    @asynccontextmanager
    async def unlocked(_engine: object, *, lock_id: int):
        assert lock_id == 731055
        yield

    monkeypatch.setattr(coordinator_module, "postgres_advisory_lock", unlocked)
    result = __import__("asyncio").run(coordinator.catch_up(run_at=NOW))

    assert result.catch_up_action == "ACTIVE_SOURCE_RETRYABLE_FAILURE"
    assert ingest.await_args.kwargs["source_ids"] == ("research_preview_macro",)


def test_all_refresh_operations_are_no_op_on_closed_xshg_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _CoordinatorPublisher()
    coordinator = _coordinator(publisher=publisher)
    calendar = cast(ExchangeCalendarResult, object())
    monkeypatch.setattr(
        coordinator,
        "_calendar_and_window",
        AsyncMock(return_value=(calendar, None)),
    )
    ingest = AsyncMock(side_effect=AssertionError("closed day must not ingest"))
    publish = AsyncMock(side_effect=AssertionError("closed day must not publish"))
    monkeypatch.setattr(coordinator, "_ingest", ingest)
    monkeypatch.setattr(coordinator, "_publish", publish)

    @asynccontextmanager
    async def unlocked(_engine: object, *, lock_id: int):
        assert lock_id == 731055
        yield

    monkeypatch.setattr(coordinator_module, "postgres_advisory_lock", unlocked)
    results = [
        __import__("asyncio").run(operation(run_at=NOW))
        for operation in (
            coordinator.ingest,
            coordinator.publish,
            coordinator.seed,
            coordinator.catch_up,
        )
    ]

    assert [(item.operation, item.status, item.catch_up_action) for item in results] == [
        ("ingest", "NO_OP", "XSHG_HOLIDAY_NO_OP"),
        ("publish", "NO_OP", "XSHG_HOLIDAY_NO_OP"),
        ("seed", "NO_OP", "XSHG_HOLIDAY_NO_OP"),
        ("catch_up", "NO_OP", "XSHG_HOLIDAY_NO_OP"),
    ]
    assert publisher.published == []


def test_operator_seed_uses_latest_complete_session_on_closed_xshg_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_at = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    calendar = ExchangeCalendarResult(
        schema_version="1.0",
        days=[
            TradingDay(
                schema_version="1.0",
                exchange="XSHG",
                trading_date=trading_date,
                status=status,
                first_seen_at=run_at,
                calendar_version="weekend-seed-test",
            )
            for trading_date, status in (
                (date(2026, 8, 6), TradingDayStatus.OPEN),
                (date(2026, 8, 7), TradingDayStatus.OPEN),
                (date(2026, 8, 8), TradingDayStatus.CLOSED),
                (date(2026, 8, 9), TradingDayStatus.CLOSED),
            )
        ],
        as_of=run_at,
        calendar_version="weekend-seed-test",
    )
    coordinator = _coordinator(publisher=_CoordinatorPublisher())
    monkeypatch.setattr(
        coordinator,
        "_calendar_and_window",
        AsyncMock(return_value=(calendar, None)),
    )
    ingest = AsyncMock(
        return_value=RefreshResult(
            operation="ingest",
            source_results=5,
            degraded_sources=1,
        )
    )
    publish = AsyncMock(
        return_value=RefreshResult(
            operation="publish",
            snapshot_id="10000000-0000-4000-8000-000000000001",
            pointer_switched=True,
        )
    )
    monkeypatch.setattr(coordinator, "_ingest", ingest)
    monkeypatch.setattr(coordinator, "_publish", publish)

    @asynccontextmanager
    async def unlocked(_engine: object, *, lock_id: int):
        assert lock_id == 731055
        yield

    monkeypatch.setattr(coordinator_module, "postgres_advisory_lock", unlocked)

    result = __import__("asyncio").run(coordinator.seed_last_session(run_at=run_at))

    expected_window = (
        datetime(2026, 8, 6, 15, 0, tzinfo=coordinator_module._SHANGHAI),
        datetime(2026, 8, 7, 8, 55, tzinfo=coordinator_module._SHANGHAI),
    )
    assert result == RefreshResult(
        operation="seed-last-session",
        source_results=5,
        degraded_sources=1,
        snapshot_id="10000000-0000-4000-8000-000000000001",
        pointer_switched=True,
    )
    assert ingest.await_args.kwargs == {
        "run_at": run_at,
        "calendar_and_window": (calendar, expected_window),
    }
    assert publish.await_args.kwargs == {
        "run_at": run_at,
        "calendar_and_window": (calendar, expected_window),
    }


def test_operator_seed_fails_closed_without_two_recent_xshg_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_at = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)
    calendar = ExchangeCalendarResult(
        schema_version="1.0",
        days=[
            TradingDay(
                schema_version="1.0",
                exchange="XSHG",
                trading_date=date(2026, 8, 9),
                status=TradingDayStatus.CLOSED,
                first_seen_at=run_at,
                calendar_version="weekend-seed-test",
            )
        ],
        as_of=run_at,
        calendar_version="weekend-seed-test",
    )
    coordinator = _coordinator(publisher=_CoordinatorPublisher())
    monkeypatch.setattr(
        coordinator,
        "_calendar_and_window",
        AsyncMock(return_value=(calendar, None)),
    )

    @asynccontextmanager
    async def unlocked(_engine: object, *, lock_id: int):
        assert lock_id == 731055
        yield

    monkeypatch.setattr(coordinator_module, "postgres_advisory_lock", unlocked)

    with pytest.raises(
        coordinator_module.RefreshCoordinatorError,
        match="RECENT_XSHG_SESSION_WINDOW_UNAVAILABLE",
    ):
        __import__("asyncio").run(coordinator.seed_last_session(run_at=run_at))


def test_publish_runs_public_smoke_after_pointer_and_propagates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _official_bundle()
    validated = validate_public_snapshot_bundle(
        pointer_document=snapshot.pointer_document,
        manifest_document=snapshot.manifest_document,
        asset_documents=snapshot.asset_documents,
    )
    source_gaps = tuple(
        coverage
        for coverage in validated.briefing.source_coverage
        if coverage.status is not SourceCoverageStatus.OK
    )
    decision = DegradedPreviewDecision(
        state=DegradedPreviewState.PARTIAL,
        completeness=PublicSnapshotCompleteness.PARTIAL,
        run_state=PublicRunState.DEGRADED,
        briefing=validated.briefing,
        trading_sessions_old=0,
        reason=DegradedPreviewReason.CURRENT_SOURCE_GAPS,
        source_gaps=source_gaps,
    )
    smoke_calls: list[dict[str, object]] = []

    def smoke(**kwargs: object) -> dict[str, str]:
        smoke_calls.append(kwargs)
        return {"status": "ok"}

    publisher = _CoordinatorPublisher()
    coordinator = _coordinator(publisher=publisher, public_smoke=smoke)
    monkeypatch.setattr(coordinator_module, "execute_premarket_brief", AsyncMock())
    monkeypatch.setattr(
        coordinator_module,
        "PostgresR1PremarketRepository",
        lambda _engine: object(),
    )
    monkeypatch.setattr(
        coordinator_module,
        "DegradedPreviewPolicy",
        lambda: SimpleNamespace(decide=lambda _request: decision),
    )
    monkeypatch.setattr(
        coordinator_module,
        "DegradedPreviewRequest",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(coordinator_module, "build_snapshot_documents", lambda **_kwargs: snapshot)
    monkeypatch.setattr(
        coordinator,
        "_briefing_candidates",
        AsyncMock(return_value=(validated.briefing, None)),
    )
    monkeypatch.setattr(
        coordinator,
        "_load_evidence",
        AsyncMock(return_value=dict(validated.evidence_by_event_id)),
    )
    monkeypatch.setattr(
        coordinator,
        "_fresh_until",
        lambda **_kwargs: validated.manifest.fresh_until,
    )
    calendar = ExchangeCalendarResult(
        schema_version="1.0",
        days=[],
        as_of=validated.briefing.generated_at,
        calendar_version="test",
    )
    window = (validated.briefing.comparison_since, validated.briefing.as_of)

    result = __import__("asyncio").run(
        coordinator._publish(
            run_at=validated.briefing.generated_at,
            calendar_and_window=(calendar, window),
        )
    )

    assert result.pointer_switched is True
    assert smoke_calls == [
        {
            "base_url": "https://preview.example.com",
            "expected_product_sha": "a" * 40,
            "expected_snapshot_id": "10000000-0000-4000-8000-000000000001",
            "attempts": 3,
        }
    ]
    failing = _coordinator(
        publisher=_CoordinatorPublisher(),
        public_smoke=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("smoke failed")),
    )
    for name in ("_briefing_candidates", "_load_evidence", "_fresh_until"):
        monkeypatch.setattr(failing, name, getattr(coordinator, name))
    with pytest.raises(RuntimeError, match="smoke failed"):
        __import__("asyncio").run(
            failing._publish(
                run_at=validated.briefing.generated_at,
                calendar_and_window=(calendar, window),
            )
        )


def test_public_scanner_rejects_internal_state_and_nonofficial_urls_before_put() -> None:
    scan_public_json(
        b'{"source_url":"https://static.cninfo.com.cn/finalpage/test.pdf"}',
        label="cninfo-public-attachment",
    )
    scan_public_json(
        b'{"source_url":"https://quantdata888.duckdns.org"}',
        label="registered-research-preview",
    )
    scan_public_json(
        b'{"source_url":"https://quantdata888.duckdns.org/"}',
        label="registered-normalized-research-preview",
    )
    scan_public_json(
        b'{"source_url":"https://ts.gyzcloud.top/docs"}',
        label="registered-legacy-research-docs",
    )
    scan_public_json(
        b'{"source_url":"https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71"}',
        label="registered-research-docs",
    )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://quantdata888.duckdns.org/mcp/token=redacted"}',
            label="unregistered-research-path",
        )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://www.yuque.com/tenglong-emkah/foo0eo/ztr29xs6iarfom71?token=not-public"}',
            label="unregistered-research-query",
        )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://ts.gyzcloud.top/docs/"}',
            label="unregistered-legacy-research-path",
        )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://ts.gyzcloud.top/docs?token=not-public"}',
            label="unregistered-legacy-research-query",
        )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://ts.gyzcloud.top/mcp/token=redacted"}',
            label="unregistered-legacy-credential-path",
        )
    with pytest.raises(PublicSafetyError):
        scan_public_json(b'{"cursor":"opaque"}', label="unsafe")
    with pytest.raises(PublicSafetyError):
        scan_public_json(b'{"source_url":"https://example.com/a"}', label="unsafe")
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://static.cninfo.com.cn/finalpage/test.pdf?token=redacted"}',
            label="unsafe-cninfo-query",
        )
    with pytest.raises(PublicSafetyError):
        scan_public_json(
            b'{"source_url":"https://static.cninfo.com.cn/not-finalpage/test.pdf"}',
            label="unsafe-cninfo-path",
        )

    client = _FakeS3()
    safe = _official_bundle()
    evidence_path = next(path for path in safe.asset_documents if "/evidence/" in path)
    unsafe_payload = json.loads(safe.asset_documents[evidence_path])
    unsafe_payload["evidence"][0]["source_url"] = "https://example.com/a"
    pointer, manifest, assets = _replace_asset_and_reseal(
        safe.pointer_document,
        safe.manifest_document,
        dict(safe.asset_documents),
        asset_path=evidence_path,
        replacement=_canonical_bytes(unsafe_payload),
    )
    snapshot = SnapshotDocuments(
        pointer=PublicSnapshotPointerV1.model_validate_json(pointer),
        manifest=PublicSnapshotManifestV1_1.model_validate_json(manifest),
        pointer_document=pointer,
        manifest_document=manifest,
        asset_documents=MappingProxyType(assets),
    )
    with pytest.raises(PublicSafetyError):
        S3SnapshotPublisher(client=client, bucket="preview").publish(snapshot)
    assert client.put_keys == []


@pytest.mark.parametrize(
    "secret_like",
    [
        "-----BEGIN" + " PRIVATE KEY-----",
        "AKIA" + "A" * 16,
        "ASIA" + "B" * 16,
        "aws_secret_access_key=" + "c" * 12,
        "access_token=" + "d" * 12,
        "cookie=" + "e" * 12,
    ],
)
def test_dynamic_public_scanner_rejects_shared_secret_signatures(secret_like: str) -> None:
    document = json.dumps({"note": secret_like}).encode()
    with pytest.raises(PublicSafetyError, match="secret-like"):
        scan_public_json(document, label="dynamic")


def test_dynamic_public_scanner_decodes_json_escapes_before_secret_scan() -> None:
    document = b'{"note":"access\\u005ftoken=abcdefghijkl"}'
    with pytest.raises(PublicSafetyError, match="secret-like"):
        scan_public_json(document, label="dynamic")
