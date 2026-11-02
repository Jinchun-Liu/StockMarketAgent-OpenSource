from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from apps.scheduler import main as scheduler_main

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.ingestion import SourceRuntimeMode
from stock_assistant.modules.ingestion.r1_sources import DEMO_REPLAY_SOURCE_IDS
from stock_assistant.platform.sources.r1_free_live import (
    FREE_SOURCE_CATALOG,
    active_free_source_definitions,
)


def test_scheduler_dispatches_ingest_then_full_late_start_chain() -> None:
    dispatched: set[str] = set()

    ingest = scheduler_main.due_dispatches(datetime(2026, 7, 29, 0, 55, tzinfo=UTC), dispatched)
    assert ingest == (("2026-07-29:ingest", False),)
    dispatched.add(ingest[0][0])

    brief = scheduler_main.due_dispatches(datetime(2026, 7, 29, 1, 5, tzinfo=UTC), dispatched)
    assert brief == (("2026-07-29:brief", True),)


def test_scheduler_does_not_dispatch_twice_or_on_weekends() -> None:
    run_at = datetime(2026, 7, 29, 1, 5, tzinfo=UTC)
    assert scheduler_main.due_dispatches(run_at, {"2026-07-29:brief"}) == ()
    assert scheduler_main.due_dispatches(datetime(2026, 8, 1, 1, 5, tzinfo=UTC), set()) == ()


def test_scheduler_does_not_dispatch_before_ingest_cutoff() -> None:
    assert scheduler_main.due_dispatches(datetime(2026, 7, 29, 0, 54, tzinfo=UTC), set()) == ()


def test_global_event_refresh_is_hourly_manifest_bound_and_stock_agnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_at = datetime(2026, 8, 27, 4, 17, tzinfo=UTC)
    request = scheduler_main.build_global_event_refresh_request(run_at)
    equivalent = scheduler_main.build_global_event_refresh_request(
        run_at.astimezone(ZoneInfo("Asia/Shanghai"))
    )
    wire = request.model_dump(mode="json")

    assert request.scheduled_for == run_at
    assert equivalent.context.request_id == request.context.request_id
    assert equivalent.context.idempotency_key == request.context.idempotency_key
    assert equivalent.scheduled_for == request.scheduled_for
    assert len(request.fixture_manifest_sha256) == 64
    assert request.context.idempotency_key.endswith(request.fixture_manifest_sha256)
    assert scheduler_main.global_event_refresh_due(run_at, None) is True
    assert scheduler_main.global_event_refresh_due(run_at, run_at) is False
    assert (
        scheduler_main.global_event_refresh_due(
            run_at,
            datetime(2026, 8, 27, 3, 17, tzinfo=UTC),
        )
        is True
    )
    assert "instrument_id" not in wire
    assert "watchlist" not in wire
    assert "active_target" not in wire

    events = scheduler_main.synthetic_global_events()
    corrected = (
        events[0].model_copy(update={"materiality": events[0].materiality - 0.01}),
        *events[1:],
    )
    monkeypatch.setattr(scheduler_main, "synthetic_global_events", lambda: corrected)
    corrected_request = scheduler_main.build_global_event_refresh_request(run_at)

    assert corrected_request.fixture_manifest_sha256 != request.fixture_manifest_sha256
    assert corrected_request.context.request_id != request.context.request_id
    assert corrected_request.context.idempotency_key != request.context.idempotency_key


def test_global_event_refresh_dispatch_uses_registered_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[tuple[str, tuple[object, ...], bool]] = []

    class FakeResult:
        id = "global-refresh-result"

    class FakeSignature:
        def apply_async(self) -> FakeResult:
            return FakeResult()

    def fake_signature(
        name: str,
        *,
        args: tuple[object, ...],
        immutable: bool,
    ) -> FakeSignature:
        dispatched.append((name, args, immutable))
        return FakeSignature()

    monkeypatch.setattr(
        scheduler_main,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            enable_r2_synthetic_research=True,
            assert_safe_production=lambda: None,
        ),
    )
    monkeypatch.setattr(scheduler_main.celery_app, "signature", fake_signature)

    result_id = scheduler_main.dispatch_global_event_refresh(
        datetime(2026, 8, 27, 4, 17, tzinfo=UTC)
    )

    assert result_id == "global-refresh-result"
    assert dispatched[0][0] == "stock_assistant.policies.refresh.v1"
    assert dispatched[0][2] is True
    payload = dispatched[0][1][0]
    assert isinstance(payload, dict)
    assert "instrument_id" not in payload
    assert "watchlist" not in payload


def test_scheduler_fixture_dispatch_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        enable_r1_fixture=False,
        r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        assert_safe_production=lambda: None,
    )
    monkeypatch.setattr(scheduler_main, "get_settings", lambda: settings)

    with pytest.raises(ApplicationError) as exc_info:
        scheduler_main.dispatch_fixture_cycle(
            datetime(2026, 7, 29, 1, 5, tzinfo=UTC), include_brief=True
        )

    assert exc_info.value.code is ErrorCode.PRECONDITION_FAILED
    assert exc_info.value.safe_message == "R1_FIXTURE_DISABLED"


def test_fixture_once_dispatches_independent_ingests_before_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waited: list[tuple[int, bool]] = []
    grouped: list[tuple[object, ...]] = []
    callbacks: list[object] = []

    class FakeResult:
        id = "fixture-chord-id"

        def get(self, *, timeout: int, propagate: bool) -> None:
            waited.append((timeout, propagate))

    class FakeCanvas:
        def apply_async(self) -> FakeResult:
            return FakeResult()

    settings = SimpleNamespace(
        enable_r1_fixture=True,
        r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        assert_safe_production=lambda: None,
    )
    monkeypatch.setattr(scheduler_main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scheduler_main,
        "_signature",
        lambda request: (
            request.job_name,
            getattr(request.payload, "source_id", "brief"),
        ),
    )
    monkeypatch.setattr(
        scheduler_main,
        "group",
        lambda items: grouped.append(tuple(items)) or ("group", tuple(items)),
        raising=False,
    )
    monkeypatch.setattr(
        scheduler_main,
        "chord",
        lambda header, callback: callbacks.append(callback) or FakeCanvas(),
        raising=False,
    )
    monkeypatch.setattr(
        scheduler_main,
        "chain",
        lambda *_items: pytest.fail("source ingests must not be dispatched as a chain"),
        raising=False,
    )

    result_id = scheduler_main.dispatch_fixture_cycle(
        datetime(2026, 7, 29, 1, 5, tzinfo=UTC),
        include_brief=True,
        wait_for_completion=True,
    )

    assert result_id == "fixture-chord-id"
    assert len(grouped) == 1
    assert [item[1] for item in grouped[0]] == list(DEMO_REPLAY_SOURCE_IDS.values())
    assert callbacks == [("stock_assistant.briefings.premarket_generate.v1", "brief")]
    assert waited == [(120, True)]


def test_dev_live_once_requires_explicit_mode_and_waits_for_active_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waited: list[tuple[int, bool]] = []
    grouped: list[tuple[object, ...]] = []

    class FakeResult:
        id = "dev-live-chord-id"

        def get(self, *, timeout: int, propagate: bool) -> None:
            waited.append((timeout, propagate))

    class FakeCanvas:
        def apply_async(self) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(
        scheduler_main,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r1_dev_live=True,
            r1_runtime_mode=SourceRuntimeMode.DEV_LIVE,
            assert_safe_production=lambda: None,
        ),
    )
    monkeypatch.setattr(
        scheduler_main,
        "_signature",
        lambda request: (request.job_name, getattr(request.payload, "capability", "brief")),
    )
    monkeypatch.setattr(
        scheduler_main,
        "group",
        lambda items: grouped.append(tuple(items)) or ("group", tuple(items)),
    )
    monkeypatch.setattr(scheduler_main, "chord", lambda _header, _callback: FakeCanvas())

    result_id = scheduler_main.dispatch_dev_live_cycle(datetime(2026, 7, 29, 1, 5, tzinfo=UTC))

    assert result_id == "dev-live-chord-id"
    assert [item[1] for item in grouped[0]] == [
        item.capability for item in active_free_source_definitions(FREE_SOURCE_CATALOG)
    ]
    assert waited == [(120, True)]
