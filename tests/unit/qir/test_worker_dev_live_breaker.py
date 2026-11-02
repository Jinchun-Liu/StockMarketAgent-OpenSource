import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from apps.scheduler.requests import build_dev_live_ingest_requests
from apps.worker import tasks

from stock_assistant.contracts.ingestion import RightsCheck, RightsDecision
from stock_assistant.platform.sources.free_source_runner import (
    FreeSourceProcessResult,
    FreeSourceRunnerError,
    FreeSourceSubprocessRunner,
)

NOW = datetime(2026, 7, 31, 1, 5, tzinfo=UTC)


async def _no_sleep(_seconds: float) -> None:
    return None


class _AllowedLegacyTestPolicy:
    """Test-only grant for the disabled legacy runner's circuit breaker."""

    async def authorize(self, check: RightsCheck) -> RightsDecision:
        return RightsDecision(
            allowed=True,
            reason_code="TEST_ONLY_ALLOWED",
            decided_at=check.context.as_of,
            rights_snapshot_hash=check.rights_snapshot_hash,
            retention_until=check.context.as_of + timedelta(days=1),
        )


def test_dev_live_breaker_persists_across_tasks_and_can_be_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_dev_live_ingest_requests(NOW)[0].model_copy(
        update={
            "payload": build_dev_live_ingest_requests(NOW)[0].payload.model_copy(
                update={
                    "source_id": "gov_cn_public_macro",
                    "capability": "macro.flashes",
                }
            )
        }
    )
    calls = 0
    outcomes = [
        "FREE_SOURCE_UNAVAILABLE",
        "FREE_SOURCE_UNAVAILABLE",
        "FREE_SOURCE_UNAVAILABLE",
        None,
    ]

    def execute(
        _function: str,
        _arguments: object,
        _timeout: float,
        _max_rows: int,
        _max_bytes: int,
    ) -> FreeSourceProcessResult:
        nonlocal calls
        calls += 1
        outcome = outcomes.pop(0)
        return FreeSourceProcessResult(
            returncode=0,
            stdout=json.dumps(
                {"ok": outcome is None, "rows": []}
                if outcome is None
                else {"ok": False, "error_code": outcome}
            ),
        )

    monkeypatch.setattr(
        tasks,
        "FreeSourceSubprocessRunner",
        lambda: FreeSourceSubprocessRunner(executor=execute, sleep=_no_sleep),
    )
    monkeypatch.setattr(tasks, "FreeDevelopmentRightsPolicy", _AllowedLegacyTestPolicy)
    tasks._reset_free_live_runner_cache()

    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_UNAVAILABLE"):
        asyncio.run(tasks._fetch_free_live(request))
    assert calls == 2

    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_UNAVAILABLE"):
        asyncio.run(tasks._fetch_free_live(request))
    assert calls == 3

    with pytest.raises(FreeSourceRunnerError, match="FREE_SOURCE_CIRCUIT_OPEN"):
        asyncio.run(tasks._fetch_free_live(request))
    assert calls == 3

    tasks._reset_free_live_runner_cache()
    batch = asyncio.run(tasks._fetch_free_live(request))

    assert calls == 4
    assert batch.source_id == request.payload.source_id


def test_dev_live_runner_is_scoped_to_the_current_worker_process() -> None:
    tasks._reset_free_live_runner_cache()

    first = tasks._free_live_runner()
    second = tasks._free_live_runner()

    assert first is second

    tasks._reset_free_live_runner_cache()
    assert tasks._free_live_runner() is not first
