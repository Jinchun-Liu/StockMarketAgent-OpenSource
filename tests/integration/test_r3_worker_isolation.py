"""Original worker process fences, unknown sends, retained output and typed recovery."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.test_active_target_repository import _database_url, _upgrade
from tests.integration.test_research_repository import (
    _create,
    _dispatched_payload,
    _lookup,
    _mutation,
)

from stock_assistant.contracts.research import (
    ResearchSessionMutationRequest,
    ResearchSessionStatus,
    ResearchStage,
    ResearchStageTransitionRequest,
)
from stock_assistant.platform.db.research import PostgresResearchRepository

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "case",
    [
        "unknown_send",
        "saved_output",
        "competing_worker",
        "late_write",
        "new_job",
        "owner_deadline",
        "purge",
        "serialization",
    ],
)
@pytest.mark.parametrize("seed", [11, 23, 47])
async def test_worker_process_recovery_boundaries(case: str, seed: int) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=1)
    request = _create(now, f"r3-worker-{uuid4()}")
    processes = []

    async def launch(task, hold="none"):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.integration.r3_worker_checkpoint_child",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        process.stdin.write(
            (
                json.dumps(
                    {
                        "database_url": database_url,
                        "seed": seed,
                        "task": task.model_dump(mode="json"),
                        "hold": hold,
                    }
                )
                + "\n"
            ).encode()
        )
        await process.stdin.drain()
        return process

    async def line(process):
        raw = await asyncio.wait_for(process.stdout.readline(), timeout=40)
        if not raw:
            error = await process.stderr.read()
            pytest.fail(error.decode())
        return json.loads(raw)

    async def finish(process):
        value = await line(process)
        await asyncio.wait_for(process.wait(), timeout=40)
        assert process.returncode == 0
        return value

    async def release(process):
        process.stdin.write(b"release\n")
        await process.stdin.drain()
        return await finish(process)

    async def mutation():
        return ResearchSessionMutationRequest(
            schema_version="1.0",
            context=_mutation(datetime.now(UTC), f"r3-mutate-{uuid4()}"),
            session_id=request.session_id,
        )

    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        first = await launch(payload, "artifact" if case == "saved_output" else "support")
        assert (await line(first))["event"] == (
            "artifact-write" if case == "saved_output" else "support-send"
        )
        before = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert before is not None
        if case != "saved_output":
            assert before.support_rounds_reserved == 1 and before.support_pending_ids
        else:
            assert before.report is not None and before.narrative_candidate is not None
        if case == "competing_worker":
            competing = await finish(await launch(payload))
            assert competing["reason"] == "RESEARCH_JOB_ALREADY_RUNNING"
            assert competing["tasks"] == []
        if case == "owner_deadline":
            wrong = payload.model_copy(
                update={
                    "context": payload.context.model_copy(
                        update={"actor_id": "different-test-owner"}
                    )
                }
            )
            # Same-job lock is released before testing owner and deadline checks.
            first.kill()
            await first.wait()
            rejected = await finish(await launch(wrong))
            assert rejected["tasks"] == [] and rejected.get("status") != "succeeded"
            expired = payload.model_copy(
                update={
                    "context": payload.context.model_copy(
                        update={"deadline_at": datetime.now(UTC) - timedelta(seconds=1)}
                    )
                }
            )
            rejected = await finish(await launch(expired))
            assert rejected["tasks"] == [] and rejected.get("status") != "succeeded"
            return
        if case in {"late_write", "purge", "new_job"}:
            if case == "late_write":
                await repository.cancel(await mutation())
            elif case == "purge":
                await repository.cancel(await mutation())
                await repository.delete(await mutation())
            else:
                await repository.transition(
                    ResearchStageTransitionRequest(
                        schema_version="1.0",
                        context=_mutation(datetime.now(UTC), f"r3-failed-{uuid4()}"),
                        session_id=request.session_id,
                        job_id=request.job_id,
                        expected_statuses=[ResearchSessionStatus.RUNNING],
                        status=ResearchSessionStatus.FAILED,
                        stage=ResearchStage.VALIDATING_REPORT,
                        message="固定模拟传输失败。",
                        reason_code="MODEL_UNAVAILABLE",
                        retryable=True,
                    )
                )
                retried = await repository.retry(await mutation())
                assert retried.job_id is not None and retried.job_id != request.job_id
                pending = next(
                    item.payload
                    for item in await repository.pending_jobs()
                    if item.job_id == retried.job_id
                )
                assert await repository.mark_job_dispatched(
                    job_id=retried.job_id, dispatched_at=datetime.now(UTC)
                )
                payload = pending
            stale = await release(first)
            assert stale.get("status") != "succeeded"
            if case == "purge":
                # Leave purge interrupted after its tombstone, prove the old
                # process cannot recreate a checkpoint, then finish cleanup.
                assert not (
                    await repository.get_checkpoint(_lookup(request.session_id, datetime.now(UTC)))
                ).found
                purge_request = await mutation()
                purge_request = purge_request.model_copy(
                    update={
                        "context": purge_request.context.model_copy(
                            update={"requested_at": datetime.now(UTC) + timedelta(hours=24)}
                        )
                    }
                )
                await repository.purge_deleted(purge_request)
                assert not (await repository.get_checkpoint(_lookup(request.session_id, now))).found
                return
            after = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
            assert after is not None and after.narrative_candidate == before.narrative_candidate
            assert after.support_pending_ids == before.support_pending_ids
            if case == "late_write":
                assert (
                    await repository.get(_lookup(request.session_id, now))
                ).session.status is ResearchSessionStatus.CANCELLED
                return
        else:
            first.kill()
            await first.wait()
        if case == "serialization":
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE research_sessions SET checkpoint_payload="
                        "jsonb_set(checkpoint_payload,'{support_rounds_reserved}','3'::jsonb) "
                        "WHERE session_id=:id"
                    ),
                    {"id": request.session_id},
                )
            rejected = await finish(await launch(payload))
            assert rejected["tasks"] == [] and rejected.get("status") != "succeeded"
            return
        resumed = await finish(await launch(payload))
        assert resumed["tasks"] == []
        if case == "saved_output":
            assert resumed["status"] == "succeeded"
        else:
            assert resumed["status"] == "failed" and resumed["reason"] == "MODEL_UNAVAILABLE"
        after = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert after is not None and after.narrative_candidate == before.narrative_candidate
        assert after.score_candidate == before.score_candidate
        assert after.support_rounds_reserved == before.support_rounds_reserved
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await engine.dispose()
