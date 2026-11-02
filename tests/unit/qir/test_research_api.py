from __future__ import annotations

from datetime import UTC, datetime, timedelta

import apps.api.routes.research as research
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from tests.contract.pae.test_public_snapshot_contracts import _materialize_bundle

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.config.settings import Settings
from stock_assistant.contracts.behavior import (
    BehaviorPressurePage,
    BehaviorPressureQuery,
    InterpretationOrigin,
    PressureMode,
)
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)
from stock_assistant.contracts.public_snapshots import validate_public_snapshot_bundle

NOW = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)
SIGNING_KEY = b"r1-cursor-test-material-32-bytes!!"


def _request() -> Request:
    app = FastAPI()
    app.state.cursor_signing_key = SIGNING_KEY
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/instruments/XSHG:603986/behavior-pressure-snapshots",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "app": app,
        }
    )


def _briefing_v1_2() -> PremarketBriefingViewV1_2:
    pointer, manifest, assets = _materialize_bundle("valid_dev_live")
    briefing = validate_public_snapshot_bundle(
        pointer_document=pointer,
        manifest_document=manifest,
        asset_documents=assets,
    ).briefing
    assert isinstance(briefing, PremarketBriefingViewV1_2)
    return briefing.model_copy(
        update={
            "instrument": briefing.instrument.model_copy(
                update={"instrument_id": "XSHG:603986", "symbol": "603986.SH"}
            )
        }
    )


@pytest.mark.asyncio
async def test_today_composes_v1_3_from_visible_v1_2_without_external_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[BehaviorPressureQuery] = []

    class FakeRepository:
        def __init__(self, _engine: object) -> None:
            pass

        async def query_assessments(
            self,
            query: BehaviorPressureQuery,
        ) -> BehaviorPressurePage:
            captured.append(query)
            return BehaviorPressurePage(
                schema_version="1.0",
                items=[],
                next_cursor=None,
                has_more=False,
                as_of=query.context.as_of,
            )

    monkeypatch.setattr(research, "PostgresBehaviorPressureRepository", FakeRepository)
    monkeypatch.setattr(research, "_database_engine", lambda _request: object())
    monkeypatch.setattr(research, "get_settings", Settings)
    monkeypatch.setattr(research, "utc_now", lambda: NOW)

    response = await research.get_today(
        request=_request(),
        briefing=_briefing_v1_2(),
    )

    assert isinstance(response.data, PremarketBriefingViewV1_3)
    assert response.data.instrument.symbol == "603986"
    assert response.data.flash_interpretation.origin is InterpretationOrigin.RULES
    assert response.data.pro_interpretation is None
    assert len(captured) == 1


def test_signed_cursor_rejects_tampering_cross_mode_and_expiry() -> None:
    cursor = research._sign_cursor(
        inner_cursor="repository-page-2",
        mode=PressureMode.POST_CLOSE,
        as_of=NOW,
        signing_key=SIGNING_KEY,
        issued_at=NOW,
    )
    assert research._decode_cursor(
        cursor,
        expected_mode=PressureMode.POST_CLOSE,
        expected_as_of=NOW,
        signing_key=SIGNING_KEY,
        now=NOW,
    ) == ("repository-page-2", NOW)

    with pytest.raises(ApplicationError) as tampered:
        research._decode_cursor(
            cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
            expected_mode=PressureMode.POST_CLOSE,
            expected_as_of=NOW,
            signing_key=SIGNING_KEY,
            now=NOW,
        )
    assert tampered.value.code is ErrorCode.VALIDATION_ERROR

    with pytest.raises(ApplicationError, match="different research mode"):
        research._decode_cursor(
            cursor,
            expected_mode=PressureMode.INTRADAY_10M,
            expected_as_of=NOW,
            signing_key=SIGNING_KEY,
            now=NOW,
        )
    with pytest.raises(ApplicationError, match="expired"):
        research._decode_cursor(
            cursor,
            expected_mode=PressureMode.POST_CLOSE,
            expected_as_of=NOW,
            signing_key=SIGNING_KEY,
            now=NOW + timedelta(minutes=16),
        )


@pytest.mark.asyncio
async def test_behavior_history_signs_repository_cursor_and_preserves_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[BehaviorPressureQuery] = []

    class FakeRepository:
        def __init__(self, _engine: object) -> None:
            pass

        async def query_assessments(
            self,
            query: BehaviorPressureQuery,
        ) -> BehaviorPressurePage:
            captured.append(query)
            return BehaviorPressurePage(
                schema_version="1.0",
                items=[],
                next_cursor="raw-repository-cursor",
                has_more=True,
                as_of=query.context.as_of,
            )

    monkeypatch.setattr(research, "PostgresBehaviorPressureRepository", FakeRepository)
    monkeypatch.setattr(research, "_database_engine", lambda _request: object())
    monkeypatch.setattr(research, "utc_now", lambda: NOW)
    response = await research.get_behavior_pressure_snapshots(
        request=_request(),
        instrument_id="XSHG:603986",
        mode=PressureMode.POST_CLOSE,
        as_of=NOW,
        cursor=None,
        limit=25,
    )

    assert captured[0].limit == 25
    assert captured[0].context.as_of == NOW
    assert captured[0].cursor is None
    assert response.data.next_cursor is not None
    inner, cutoff = research._decode_cursor(
        response.data.next_cursor,
        expected_mode=PressureMode.POST_CLOSE,
        expected_as_of=NOW,
        signing_key=SIGNING_KEY,
        now=NOW,
    )
    assert (inner, cutoff) == ("raw-repository-cursor", NOW)
