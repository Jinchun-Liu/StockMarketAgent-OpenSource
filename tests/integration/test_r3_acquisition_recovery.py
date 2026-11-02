"""B/C fixed original-window traces across killed processes and PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
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
from tests.unit.qmr import test_research_fallback as t

from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.research import (
    CoverageGap,
    ResearchCheckpoint,
    ResearchCheckpointWriteRequest,
    ResearchCutoffMode,
    ResearchRealSourceMode,
    ResearchSessionStatus,
    ResearchStage,
    ResearchStageTransitionRequest,
)
from stock_assistant.modules.research.reporting import GENERATOR_VERSION, VALIDATOR_VERSION
from stock_assistant.platform.db.research import PostgresResearchRepository

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("case", ["B", "C"])
@pytest.mark.parametrize("seed", [11, 23, 47])
async def test_original_window_budget_survives_process_death(case: str, seed: int) -> None:
    database_url = _database_url()
    await _upgrade(database_url)
    engine = create_async_engine(database_url)
    repository = PostgresResearchRepository(engine)
    now = datetime.now(UTC) - timedelta(seconds=1)
    request = _create(
        now, f"r3-window-{uuid4()}", cutoff_mode=ResearchCutoffMode.CURRENT_RUN
    ).model_copy(update={"source_mode": ResearchRealSourceMode.DEVELOPMENT_ONLY})
    try:
        await repository.create(request)
        payload = await _dispatched_payload(repository, request, dispatched_at=now)
        await repository.transition(
            ResearchStageTransitionRequest(
                schema_version="1.0",
                context=_mutation(now, f"r3-window-start-{uuid4()}"),
                session_id=request.session_id,
                job_id=request.job_id,
                expected_statuses=[ResearchSessionStatus.QUEUED],
                status=ResearchSessionStatus.RUNNING,
                stage=ResearchStage.COLLECTING_HISTORY,
                message="固定合成窗口恢复。",
            )
        )
        session = (await repository.get(_lookup(request.session_id, now))).session
        assert session is not None and payload.context.deadline_at is not None
        key = "business.customer_revenue_chain"
        headings = ["经营模式", "主营业务分析", "合并利润表", "核心竞争力分析", "利润分配方案"]
        headings += [h for _ in range(3) for h in ("经营模式", "利润分配方案")]
        if case == "C":
            headings = ["经营模式"] * 5
        body = "603986 兆易创新\n\n" + "\n\n".join(h + "\n" + "原始说明" * 2000 for h in headings)
        document, _ = t._document_fixture(body, now)
        packet = t._packet(session)
        passed = (
            [k for k in t.fallback_module.REAL_FACT_GAPS if k != key]
            if case == "C"
            else ["identity.company", "business.core_products"]
        )
        packet = packet.model_copy(
            update={
                "facts": [packet.facts[0].model_copy(update={"key": k}) for k in passed],
                "coverage_gaps": [
                    CoverageGap(area=key, reason_code="REQUIRED_FIELD_MISSING", detail="固定缺口")
                ],
            }
        )
        input_hash = hashlib.sha256(
            t.fallback_module._canonical(
                {
                    "instrument_id": session.instrument_id,
                    "instrument_name": session.instrument_name,
                    "as_of": session.as_of.isoformat(),
                    "analysis_config": session.analysis_config.model_dump(mode="json"),
                    "cutoff_mode": ResearchCutoffMode.CURRENT_RUN.value,
                    "source_registrations": t.fallback_module.enabled_web_rights_snapshots(),
                }
            )
        ).hexdigest()
        acquisition = ResearchAcquisitionState(
            schema_version="1.3",
            rule_version=t.fallback_module.WEB_FALLBACK_RULE_VERSION,
            instrument_id=session.instrument_id,
            requested_as_of=now,
            deadline_at=payload.context.deadline_at,
            input_sha256=input_hash,
            documents=[document],
            discovery_exhausted=case == "C",
        )
        checkpoint = ResearchCheckpoint(
            schema_version="1.9",
            generator_version=GENERATOR_VERSION,
            validator_version=VALIDATOR_VERSION,
            stage=ResearchStage.COLLECTING_HISTORY,
            acquisition=acquisition,
            packet=packet,
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
                "tests.integration.r3_acquisition_checkpoint_child",
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
                                "case": case,
                                "phase": phase,
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

        code, output = await child("crash")
        assert code == 17
        if case == "C":
            assert json.loads(output)["unknown_ordinal"] == 1
        before = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert before is not None and before.acquisition is not None
        code, output = await child("resume")
        assert code == 0
        result = json.loads(output)
        assert result["attempts"][key] == 4
        assert result["candidate_count"] == (5 if case == "B" else 4)
        assert result["provider_calls"] == (5 if case == "B" else 4)
        if case == "C":
            assert result["ordinals"] == [2, 3, 4]
        after = (await repository.get_checkpoint(_lookup(request.session_id, now))).checkpoint
        assert after is not None and after.acquisition is not None
        assert (
            after.acquisition.candidates[: len(before.acquisition.candidates)]
            == before.acquisition.candidates
        )
        assert after.acquisition.documents == before.acquisition.documents
    finally:
        await engine.dispose()
