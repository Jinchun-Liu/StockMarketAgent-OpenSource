"""Persistent robots observations preserve query identity, budgets and audited work."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration import test_research_repository as repository_helpers
from tests.integration.test_active_target_repository import _database_url, _upgrade
from tests.integration.test_research_repository import (
    _create,
    _dispatched_payload,
    _lookup,
    _mutation,
)
from tests.unit.qmr import test_research_fallback as helpers

from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.research import (
    ResearchCheckpoint,
    ResearchCheckpointWriteRequest,
    ResearchCutoffMode,
    ResearchRealSourceMode,
    ResearchSessionMutationRequest,
    ResearchSessionStatus,
    ResearchStage,
    ResearchStageTransitionRequest,
)
from stock_assistant.modules.research.reporting import GENERATOR_VERSION, VALIDATOR_VERSION
from stock_assistant.platform.db.research import (
    PostgresResearchRepository,
    ResearchTransitionConflictError,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mismatch", ["schema", "generator", "validator"])
async def test_worker_refuses_old_checkpoint_without_restarting_budget(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=1)
    create = _create(now, f"next-stale-{uuid4()}")
    provider = repository_helpers._CoordinatedResearchLLMProvider(expected_narratives=1)
    store = repository_helpers._ResearchObjectStore()
    repository_helpers._configure_worker(monkeypatch, provider=provider, object_store=store)
    try:
        await repository.create(create)
        task = await _dispatched_payload(repository, create, dispatched_at=now)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(now, f"next-stale-start-{uuid4()}"),
                session_id=create.session_id,
                job_id=create.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.COLLECTING_HISTORY,
                message="旧版本已保存进度。",
                checkpoint="history-collected",
            )
        )
        session = (await repository.get(_lookup(create.session_id, now))).session
        assert session is not None
        checkpoint = ResearchCheckpoint(
            schema_version="1.8" if mismatch == "schema" else "1.9",
            generator_version="previous-generator"
            if mismatch == "generator"
            else GENERATOR_VERSION,
            validator_version="previous-validator"
            if mismatch == "validator"
            else VALIDATOR_VERSION,
            stage=ResearchStage.COLLECTING_HISTORY,
            packet=helpers._packet(session),
            provider_calls=7,
            acquisition=ResearchAcquisitionState(
                schema_version="1.1",
                rule_version="research-real-acquisition.v30",
                instrument_id=session.instrument_id,
                requested_as_of=session.as_of,
                deadline_at=task.context.deadline_at,
                input_sha256="a" * 64,
                attempts={"identity.company": 3},
                provider_calls=7,
            ),
        )
        await repository.save_checkpoint(
            ResearchCheckpointWriteRequest(
                schema_version="1.0",
                context=task.context,
                session_id=create.session_id,
                job_id=create.job_id,
                checkpoint=checkpoint,
            )
        )
        result = await asyncio.wait_for(
            repository_helpers.worker_tasks._execute_research_generate(task, engine=engine),
            timeout=10,
        )
        assert result.status is ResearchSessionStatus.FAILED
        assert result.reason_code == "RESEARCH_CHECKPOINT_INCOMPATIBLE"
        assert provider.task_types == [] and store.writes == []
        current = (await repository.get(_lookup(create.session_id, now))).session
        assert current is not None and not current.retryable
        assert current.completed_at == current.updated_at
        assert current.progress.stage is ResearchStage.COLLECTING_HISTORY
        assert current.progress.checkpoint == "history-collected"
        assert (
            await repository.get_checkpoint(_lookup(create.session_id, now))
        ).checkpoint == checkpoint
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "case",
    [
        "stop_saved",
        "unknown_send",
        "facts_saved",
        "cancel",
        "new_job",
        "formal_stop_saved",
        "formal_unknown_send",
        "formal_facts_saved",
        "formal_cancel",
        "formal_new_job",
        "formal_document_saved",
    ],
)
async def test_source_observation_survives_process_death(case: str) -> None:
    formal_attachment = case.startswith("formal_")
    case = case.removeprefix("formal_")
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=1)
    create = _create(
        now, f"next-source-{uuid4()}", cutoff_mode=ResearchCutoffMode.CURRENT_RUN
    ).model_copy(
        update={
            "source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY,
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
        }
    )
    try:
        await repository.create(create)
        task = await _dispatched_payload(repository, create, dispatched_at=now)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(now, f"next-start-{uuid4()}"),
                session_id=create.session_id,
                job_id=create.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.COLLECTING_HISTORY,
                message="固定来源恢复。",
            )
        )
        session = (await repository.get(_lookup(create.session_id, now))).session
        assert session is not None
        write = ResearchCheckpointWriteRequest(
            schema_version="1.0",
            context=task.context,
            session_id=create.session_id,
            job_id=create.job_id,
            checkpoint=ResearchCheckpoint(
                schema_version="1.9",
                generator_version=GENERATOR_VERSION,
                validator_version=VALIDATOR_VERSION,
                stage=ResearchStage.COLLECTING_HISTORY,
                packet=helpers.empty_real_evidence_packet(session),
            ),
        )

        async def child(phase):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tests.integration.next_source_checkpoint_child",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                output, error = await asyncio.wait_for(
                    process.communicate(
                        json.dumps(
                            {
                                "database_url": database_url,
                                "write": write.model_dump(mode="json"),
                                "phase": phase,
                                "case": case,
                                "formal_attachment": formal_attachment,
                            }
                        ).encode()
                    ),
                    timeout=45,
                )
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            return process.returncode, output, error

        code, output, error = await child("crash")
        assert code == 17, error.decode()
        before = (await repository.get_checkpoint(_lookup(create.session_id, now))).checkpoint
        assert before is not None and before.acquisition is not None
        acquisition = before.acquisition
        assert len(acquisition.robots_origin_stops) == 2
        if case == "cancel":
            await repository.cancel(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(datetime.now(UTC), f"next-cancel-{uuid4()}"),
                    session_id=create.session_id,
                )
            )
            code, _, error = await child("resume")
            assert code != 0 and b"ResearchTransitionConflictError" in error
            after = (await repository.get_checkpoint(_lookup(create.session_id, now))).checkpoint
            assert after == before
            return
        if case == "new_job":
            await repository.transition(
                ResearchStageTransitionRequest(
                    schema_version="1.0",
                    context=_mutation(datetime.now(UTC), f"next-fail-{uuid4()}"),
                    session_id=create.session_id,
                    job_id=create.job_id,
                    expected_statuses=[ResearchSessionStatus.RUNNING],
                    status=ResearchSessionStatus.FAILED,
                    stage=ResearchStage.COLLECTING_HISTORY,
                    message="固定可恢复中断。",
                    reason_code="MODEL_UNAVAILABLE",
                    retryable=True,
                )
            )
            retried = await repository.retry(
                ResearchSessionMutationRequest(
                    schema_version="1.0",
                    context=_mutation(datetime.now(UTC), f"next-retry-{uuid4()}"),
                    session_id=create.session_id,
                )
            )
            assert retried.job_id is not None and retried.job_id != create.job_id
            with pytest.raises(ResearchTransitionConflictError):
                await repository.save_checkpoint(write.model_copy(update={"checkpoint": before}))
            replacement = create.model_copy(update={"job_id": retried.job_id})
            task = await _dispatched_payload(
                repository, replacement, dispatched_at=datetime.now(UTC)
            )
            await repository.transition(
                ResearchStageTransitionRequest(
                    schema_version="1.0",
                    context=_mutation(datetime.now(UTC), f"next-restart-{uuid4()}"),
                    session_id=create.session_id,
                    job_id=retried.job_id,
                    expected_statuses=[ResearchSessionStatus.QUEUED],
                    status=ResearchSessionStatus.RUNNING,
                    stage=ResearchStage.COLLECTING_HISTORY,
                    message="原研究合法恢复。",
                )
            )
            write = write.model_copy(update={"job_id": retried.job_id, "context": task.context})
        code, output, error = await child("resume")
        assert code == 0, error.decode()
        metrics = json.loads(output)
        after = (await repository.get_checkpoint(_lookup(create.session_id, now))).checkpoint
        assert after is not None and after.acquisition is not None
        assert after.acquisition.robots_origin_stops == acquisition.robots_origin_stops
        assert all(n <= 4 for n in after.acquisition.attempts.values())
        assert all(host == "www.zmax-opt.com" for host, _ in metrics["requests"])
        if acquisition.pending_query is not None:
            assert metrics["original_pending"] == acquisition.pending_query.model_dump_json()
        if case in {"stop_saved", "new_job"} and not formal_attachment:
            first = metrics["first_saved"]
            assert first["pending_query"] == acquisition.pending_query.model_dump(mode="json")
            assert first["pending_discovery"] == acquisition.pending_discovery.model_dump(
                mode="json"
            )
            assert first["round_sources"] == acquisition.round_sources
            assert first["attempted_queries"] == acquisition.attempted_queries
        if formal_attachment:
            assert acquisition.verification_outcomes
            assert (
                after.acquisition.verification_outcomes[: len(acquisition.verification_outcomes)]
                == acquisition.verification_outcomes
            )
            if acquisition.documents:
                assert ["www.zmax-opt.com", "/files/annual.pdf"] not in metrics["requests"]
                assert after.acquisition.documents == acquisition.documents
            assert all(d.formal_document is not None for d in after.acquisition.documents)
        if case == "unknown_send":
            assert 2 not in metrics["ordinals"]
            assert (
                after.acquisition.candidates[: len(acquisition.candidates)]
                == acquisition.candidates
            )
        else:
            assert {"identity.company", "business.core_products"} <= set(metrics["fact_keys"])
        if case == "facts_saved":
            assert "identity.company" not in metrics["audit_keys"]
            assert all(f in after.packet.facts for f in before.packet.facts)
            assert (
                after.acquisition.documents[: len(acquisition.documents)] == acquisition.documents
            )
    finally:
        await engine.dispose()
