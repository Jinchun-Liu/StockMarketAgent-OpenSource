import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from apps.api.http import RequestContextMiddleware, application_error_handler
from apps.api.routes import premarket
from apps.api.routes.premarket import (
    load_event_evidence,
    load_latest_briefing,
    router,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_assistant.common.errors import ApplicationError
from stock_assistant.config.settings import Settings
from stock_assistant.contracts.event_views import EventEvidenceView, EventEvidenceViewV1_1
from stock_assistant.contracts.ingestion import (
    SourceAccessChannel,
    SourceProvenance,
    SourceReleaseTier,
    SourceRightsStatus,
    SourceRuntimeMode,
)
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingView,
    PremarketBriefingViewV1_1,
)
from stock_assistant.contracts.r1_http import (
    R1_SOURCE_PROVENANCE_MEDIA_TYPE,
    R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2,
)

_FIXTURE_ROOT = Path("tests/contract/arc/fixtures/r1/valid")


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


def _briefing() -> PremarketBriefingView:
    return PremarketBriefingView.model_validate_json(
        (_FIXTURE_ROOT / "premarket-briefing.v1.0.json").read_text()
    )


def _event() -> EventEvidenceView:
    collection = json.loads((_FIXTURE_ROOT / "event-evidence-scenarios.v1.0.json").read_text())
    return EventEvidenceView.model_validate(collection[0]["payload"])


def _provenance() -> SourceProvenance:
    return SourceProvenance(
        schema_version="1.1",
        provider="stock_market_agent",
        access_channel=SourceAccessChannel.DEMO_REPLAY,
        adapter_name="r1_demo_replay",
        adapter_version="1.0.0",
        endpoint_name="replay.statutory_announcements",
        source_identifier="XSHG:603986:statutory_announcements",
        runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        release_tier=SourceReleaseTier.DEV_ONLY,
        rights_status=SourceRightsStatus.UNVERIFIED,
    )


def _briefing_v1_1() -> PremarketBriefingViewV1_1:
    payload = _briefing().model_dump(mode="json")
    payload["schema_version"] = "1.1"
    payload["runtime_mode"] = "demo_replay"
    payload["release_tier"] = "dev_only"
    payload["ingestion_progress"]["provenance"] = _provenance().model_dump(mode="json")
    for coverage in payload["source_coverage"]:
        coverage["provenance"] = _provenance().model_dump(mode="json")
    return PremarketBriefingViewV1_1.model_validate(payload)


def _event_v1_1() -> EventEvidenceViewV1_1:
    payload = _event().model_dump(mode="json")
    payload["schema_version"] = "1.1"
    payload["runtime_mode"] = "demo_replay"
    for evidence in payload["evidence"]:
        evidence["event_time"] = evidence["published_at"]
        evidence["provenance"] = _provenance().model_dump(mode="json")
    return EventEvidenceViewV1_1.model_validate(payload)


def _client(
    *,
    briefing: PremarketBriefingView | PremarketBriefingViewV1_1 | None = None,
    event: EventEvidenceView | EventEvidenceViewV1_1 | None = None,
) -> TestClient:
    app = FastAPI(lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router, prefix="/api/v1")
    expected_briefing = briefing or _briefing()
    expected_event = event or _event()

    async def briefing_loader() -> PremarketBriefingView | PremarketBriefingViewV1_1:
        return expected_briefing

    async def evidence_loader() -> EventEvidenceView | EventEvidenceViewV1_1:
        return expected_event

    app.dependency_overrides[load_latest_briefing] = briefing_loader
    app.dependency_overrides[load_event_evidence] = evidence_loader
    return TestClient(app)


class _ModeBoundR1Repository:
    def __init__(
        self,
        stored: dict[
            tuple[SourceRuntimeMode, SourceReleaseTier],
            PremarketBriefingView | PremarketBriefingViewV1_1,
        ],
        *,
        events: dict[
            tuple[SourceRuntimeMode, SourceReleaseTier],
            EventEvidenceView | EventEvidenceViewV1_1,
        ]
        | None = None,
    ) -> None:
        self._stored = stored
        self._events = events or {}
        self.calls: list[tuple[SourceRuntimeMode, SourceReleaseTier]] = []
        self.evidence_calls: list[tuple[SourceRuntimeMode, SourceReleaseTier]] = []

    async def get_latest_briefing(
        self,
        *,
        as_of: object,
        expected_runtime_mode: SourceRuntimeMode,
        expected_release_tier: SourceReleaseTier,
    ) -> PremarketBriefingView | PremarketBriefingViewV1_1 | None:
        del as_of
        selection = (expected_runtime_mode, expected_release_tier)
        self.calls.append(selection)
        return self._stored.get(selection)

    async def get_event_evidence(
        self,
        *,
        event_id: object,
        selector: object,
        expected_runtime_mode: SourceRuntimeMode,
        expected_release_tier: SourceReleaseTier,
    ) -> EventEvidenceView | EventEvidenceViewV1_1 | None:
        del event_id, selector
        selection = (expected_runtime_mode, expected_release_tier)
        self.evidence_calls.append(selection)
        return self._events.get(selection)


def _mode_bound_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: Settings,
    repository: _ModeBoundR1Repository,
) -> TestClient:
    app = FastAPI(lifespan=_lifespan)
    app.state.db_engine = object()
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.include_router(router, prefix="/api/v1")
    monkeypatch.setattr(premarket, "get_settings", lambda: settings)
    monkeypatch.setattr(premarket, "_database_engine", lambda _request: object())
    monkeypatch.setattr(
        premarket,
        "PostgresR1PremarketRepository",
        lambda _engine: repository,
    )
    return TestClient(app)


def test_latest_briefing_uses_frozen_success_envelope() -> None:
    response = _client().get("/api/v1/briefings/premarket/latest")

    assert response.status_code == 200
    assert response.headers["vary"] == "Accept"
    assert response.json()["data"] == _briefing().model_dump(mode="json")
    assert response.json()["meta"]["as_of"] == "2026-07-25T01:00:00Z"


def test_event_evidence_uses_query_cutoff_and_success_envelope() -> None:
    event = _event()
    response = _client().get(
        f"/api/v1/events/{event.event_id}/evidence",
        params={"as_of": event.as_of.isoformat()},
    )

    assert response.status_code == 200
    assert response.headers["vary"] == "Accept"
    assert response.json()["data"] == event.model_dump(mode="json")
    assert response.json()["meta"]["as_of"] == "2026-07-25T01:00:00Z"


def test_routes_default_to_the_stable_v1_0_projection_for_v1_1_storage() -> None:
    briefing = _briefing_v1_1()
    event = _event_v1_1()
    client = _client(briefing=briefing, event=event)

    briefing_response = client.get("/api/v1/briefings/premarket/latest")
    evidence_response = client.get(
        f"/api/v1/events/{event.event_id}/evidence",
        params={"as_of": event.as_of.isoformat()},
    )

    assert briefing_response.status_code == 200
    assert briefing_response.headers["vary"] == "Accept"
    assert briefing_response.headers["content-type"] == "application/json"
    assert briefing_response.json()["data"]["schema_version"] == "1.0"
    assert "runtime_mode" not in briefing_response.json()["data"]
    assert "provenance" not in briefing_response.json()["data"]["source_coverage"][0]
    assert evidence_response.status_code == 200
    assert evidence_response.headers["vary"] == "Accept"
    assert evidence_response.headers["content-type"] == "application/json"
    assert evidence_response.json()["data"]["schema_version"] == "1.0"
    assert "runtime_mode" not in evidence_response.json()["data"]
    assert "provenance" not in evidence_response.json()["data"]["evidence"][0]
    assert "event_time" not in evidence_response.json()["data"]["evidence"][0]


def test_routes_return_v1_1_only_for_the_explicit_source_provenance_media_type() -> None:
    briefing = _briefing_v1_1()
    event = _event_v1_1()
    client = _client(briefing=briefing, event=event)
    headers = {"Accept": R1_SOURCE_PROVENANCE_MEDIA_TYPE}

    briefing_response = client.get("/api/v1/briefings/premarket/latest", headers=headers)
    evidence_response = client.get(
        f"/api/v1/events/{event.event_id}/evidence",
        params={"as_of": event.as_of.isoformat()},
        headers=headers,
    )

    assert briefing_response.status_code == 200
    assert briefing_response.headers["vary"] == "Accept"
    assert briefing_response.headers["content-type"] == R1_SOURCE_PROVENANCE_MEDIA_TYPE
    assert briefing_response.json()["data"] == briefing.model_dump(mode="json")
    assert briefing_response.json()["data"]["schema_version"] == "1.1"
    assert briefing_response.json()["data"]["source_coverage"][0]["provenance"]
    assert evidence_response.status_code == 200
    assert evidence_response.headers["vary"] == "Accept"
    assert evidence_response.headers["content-type"] == R1_SOURCE_PROVENANCE_MEDIA_TYPE
    assert evidence_response.json()["data"] == event.model_dump(mode="json")
    assert evidence_response.json()["data"]["schema_version"] == "1.1"
    assert evidence_response.json()["data"]["evidence"][0]["provenance"]


def test_vendor_representation_fails_closed_for_v1_0_storage_with_vary_accept() -> None:
    briefing = _briefing()
    event = _event()
    client = _client(briefing=briefing, event=event)
    headers = {"Accept": R1_SOURCE_PROVENANCE_MEDIA_TYPE}

    briefing_response = client.get("/api/v1/briefings/premarket/latest", headers=headers)
    evidence_response = client.get(
        f"/api/v1/events/{event.event_id}/evidence",
        params={"as_of": event.as_of.isoformat()},
        headers=headers,
    )

    for response in (briefing_response, evidence_response):
        assert response.status_code == 412
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.headers["vary"] == "Accept"
        assert response.json()["code"] == "PRECONDITION_FAILED"
        assert response.json()["detail"] == "R1_SOURCE_PROVENANCE_UNAVAILABLE"


def test_rejected_vendor_representation_falls_back_to_acceptable_json() -> None:
    response = _client(briefing=_briefing_v1_1()).get(
        "/api/v1/briefings/premarket/latest",
        headers={"Accept": f"{R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2};q=0, application/json"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["vary"] == "Accept"
    assert response.json()["data"]["schema_version"] == "1.0"


def test_wildcard_uses_available_v1_1_when_json_is_explicitly_rejected() -> None:
    response = _client(briefing=_briefing_v1_1()).get(
        "/api/v1/briefings/premarket/latest",
        headers={"Accept": "application/json;q=0, */*;q=1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == R1_SOURCE_PROVENANCE_MEDIA_TYPE
    assert response.headers["vary"] == "Accept"
    assert response.json()["data"]["schema_version"] == "1.1"


def test_route_returns_not_acceptable_when_every_supported_representation_is_rejected() -> None:
    response = _client(briefing=_briefing_v1_1()).get(
        "/api/v1/briefings/premarket/latest",
        headers={"Accept": f"{R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2};q=0"},
    )

    assert response.status_code == 406
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["vary"] == "Accept"
    assert response.json()["code"] == "PRECONDITION_FAILED"
    assert response.json()["detail"] == "R1_REPRESENTATION_NOT_ACCEPTABLE"


@pytest.mark.parametrize(
    ("runtime_mode", "release_tier"),
    [
        (SourceRuntimeMode.DEMO_REPLAY, SourceReleaseTier.DEV_ONLY),
        (SourceRuntimeMode.DEV_LIVE, SourceReleaseTier.DEV_ONLY),
        (SourceRuntimeMode.AUTHORIZED, SourceReleaseTier.AUTHORIZED),
    ],
)
def test_latest_briefing_read_is_bound_to_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
    runtime_mode: SourceRuntimeMode,
    release_tier: SourceReleaseTier,
) -> None:
    repository = _ModeBoundR1Repository({(runtime_mode, release_tier): _briefing_v1_1()})
    settings = Settings(app_env="development", r1_runtime_mode=runtime_mode, _env_file=None)
    client = _mode_bound_client(monkeypatch, settings=settings, repository=repository)

    response = client.get(
        "/api/v1/briefings/premarket/latest",
        params={"runtime_mode": SourceRuntimeMode.AUTHORIZED.value},
    )

    assert response.status_code == 200
    assert repository.calls == [(runtime_mode, release_tier)]


def test_production_briefing_read_is_forced_to_authorized_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _ModeBoundR1Repository(
        {
            (SourceRuntimeMode.DEMO_REPLAY, SourceReleaseTier.DEV_ONLY): _briefing_v1_1(),
            (SourceRuntimeMode.AUTHORIZED, SourceReleaseTier.AUTHORIZED): _briefing_v1_1(),
        }
    )
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        _env_file=None,
    )
    client = _mode_bound_client(monkeypatch, settings=settings, repository=repository)

    response = client.get("/api/v1/briefings/premarket/latest")

    assert response.status_code == 200
    assert repository.calls == [(SourceRuntimeMode.AUTHORIZED, SourceReleaseTier.AUTHORIZED)]


def test_latest_briefing_mode_mismatch_returns_stable_not_found_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _ModeBoundR1Repository(
        {(SourceRuntimeMode.AUTHORIZED, SourceReleaseTier.AUTHORIZED): _briefing_v1_1()}
    )
    settings = Settings(
        app_env="development",
        r1_runtime_mode=SourceRuntimeMode.DEV_LIVE,
        _env_file=None,
    )
    client = _mode_bound_client(monkeypatch, settings=settings, repository=repository)

    response = client.get(
        "/api/v1/briefings/premarket/latest",
        headers={"Accept": R1_SOURCE_PROVENANCE_MEDIA_TYPE},
        params={"runtime_mode": SourceRuntimeMode.AUTHORIZED.value},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_FOUND"
    assert repository.calls == [(SourceRuntimeMode.DEV_LIVE, SourceReleaseTier.DEV_ONLY)]


@pytest.mark.parametrize(
    "accept",
    ["application/json", R1_SOURCE_PROVENANCE_MEDIA_TYPE],
)
def test_production_evidence_read_rejects_development_sources_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    accept: str,
) -> None:
    event = _event_v1_1()
    repository = _ModeBoundR1Repository(
        {},
        events={(SourceRuntimeMode.DEMO_REPLAY, SourceReleaseTier.DEV_ONLY): event},
    )
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        _env_file=None,
    )
    client = _mode_bound_client(monkeypatch, settings=settings, repository=repository)

    response = client.get(
        f"/api/v1/events/{event.event_id}/evidence",
        params={"as_of": event.as_of.isoformat()},
        headers={"Accept": accept},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_FOUND"
    assert repository.evidence_calls == [
        (SourceRuntimeMode.AUTHORIZED, SourceReleaseTier.AUTHORIZED)
    ]
