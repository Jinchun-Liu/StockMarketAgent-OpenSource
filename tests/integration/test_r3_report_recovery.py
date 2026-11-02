"""Report dependency crash traces using the existing PostgreSQL repository."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.test_active_target_repository import _database_url, _upgrade
from tests.integration.test_research_repository import (
    _create,
    _dispatched_payload,
    _lookup,
    _mutation,
)

from stock_assistant.contracts.research import (
    ResearchCheckpoint,
    ResearchCheckpointWriteRequest,
    ResearchSessionStatus,
    ResearchStage,
    ResearchStageTransitionRequest,
)
from stock_assistant.modules.research.reporting import GENERATOR_VERSION, VALIDATOR_VERSION
from stock_assistant.platform.db.research import PostgresResearchRepository
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("dependent", [True, False])
@pytest.mark.parametrize(
    "boundary", ["first_reject", "invalidation", "before_second", "second_saved"]
)
@pytest.mark.parametrize("seed", [11, 23, 47])
async def test_summary_dependency_crash_and_new_process_resume(
    dependent: bool, boundary: str, seed: int
) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=1)
    request = _create(now, f"r3-summary-{uuid4()}")
    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(now, f"r3-running-{uuid4()}"),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.VALIDATING_REPORT,
                message="固定合成恢复测试。",
            )
        )
        session = (await repository.get(_lookup(request.session_id, now))).session
        assert session is not None
        checkpoint = ResearchCheckpoint(
            schema_version="1.9",
            generator_version=GENERATOR_VERSION,
            validator_version=VALIDATOR_VERSION,
            stage=ResearchStage.VALIDATING_REPORT,
            packet=synthetic_evidence_packet(
                instrument_id=session.instrument_id,
                instrument_name=session.instrument_name,
                as_of=session.as_of,
            ),
        )
        write = ResearchCheckpointWriteRequest(
            schema_version="1.0",
            context=payload.context,
            session_id=request.session_id,
            job_id=request.job_id,
            checkpoint=checkpoint,
        )

        async def child(phase: str) -> tuple[int, bytes]:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tests.integration.r3_report_checkpoint_child",
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
                                "seed": seed,
                                "write": write.model_dump(mode="json"),
                                "phase": phase,
                                "boundary": boundary,
                                "dependent": dependent,
                            }
                        ).encode()
                    ),
                    timeout=40,
                )
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert process.returncode in {0, 17}, error.decode()
            return process.returncode, output

        code, _ = await child("crash")
        assert code == 17
        persisted = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert persisted is not None and persisted.support_assessment is not None
        verdicts = {v.item_id: v for v in persisted.support_assessment.verdicts}
        assert not verdicts["hypothesis:2"].supported
        assert verdicts["hypothesis:1"].supported
        assert verdicts["section:5"].supported
        assert verdicts["section:5"].summary_context_hypothesis_ids == ["hypothesis:1"]
        if boundary != "second_saved":
            assert "section:4" not in verdicts and "section:6" not in verdicts
        else:
            assert verdicts["section:4"].summary_context_hypothesis_ids == ["hypothesis:0"]
        before = persisted.model_copy(deep=True)
        code, output = await child("resume")
        assert code == 0
        result = json.loads(output)
        assert not result["contains_rejected_premise"]
        assert result["complete"] is (not dependent and boundary != "before_second")
        expected_audits = 1 if boundary in {"first_reject", "invalidation"} else 0
        assert result["request_types"] == ["research_support_assessment"] * expected_audits
        assert result["audit_ids"] == [["section:4", "section:6"]] * expected_audits
        after = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert after is not None and after.support_rounds_reserved == 2
        assert after.narrative_candidate == before.narrative_candidate
        assert after.score_candidate == before.score_candidate
        assert after.support_assessment is not None
        final_verdicts = {v.item_id: v for v in after.support_assessment.verdicts}
        for item_id in ("hypothesis:1", "section:5", *(f"score:{i}" for i in range(8))):
            if item_id in verdicts:
                assert final_verdicts[item_id] == verdicts[item_id]
    finally:
        await engine.dispose()
