"""Crash only a synthetic acquisition process; persist through the real repository."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from itertools import cycle

from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.test_research_repository import _lookup
from tests.unit.qmr import test_research_fallback as t
from tests.unit.qmr.test_r3_acquisition_regressions import EmptyFlash

from stock_assistant.contracts.discovery import ResearchAcquisitionState
from stock_assistant.contracts.intelligence import GenerationRequest, GenerationResponse
from stock_assistant.contracts.research import (
    ResearchCheckpointWriteRequest,
    ResearchCutoffMode,
    ResearchEvidencePacket,
    ResearchStage,
)
from stock_assistant.modules.research.web_fallback import ResearchWebFallbackCoordinator
from stock_assistant.platform.db.research import PostgresResearchRepository


async def main() -> None:
    data = json.loads(sys.stdin.read())
    schedule = cycle(((data["seed"] * step) % 3001) / 1_000_000 for step in (137, 503, 997))
    engine = create_async_engine(data["database_url"])
    repository = PostgresResearchRepository(engine)
    template = ResearchCheckpointWriteRequest.model_validate(data["write"])
    lookup = _lookup(template.session_id, datetime.now(UTC))
    session = (await repository.get(lookup)).session
    assert session is not None
    checkpoint = (await repository.get_checkpoint(lookup)).checkpoint or template.checkpoint
    state, packet = checkpoint.acquisition, checkpoint.packet
    assert state is not None
    ordinals: list[int] = []
    key = "business.customer_revenue_chain"

    async def save(value: ResearchAcquisitionState, current: ResearchEvidencePacket) -> None:
        await asyncio.sleep(next(schedule))
        await repository.save_checkpoint(
            template.model_copy(
                update={
                    "context": template.context.model_copy(
                        update={"requested_at": datetime.now(UTC)}
                    ),
                    "checkpoint": checkpoint.model_copy(
                        update={"acquisition": value, "packet": current}
                    ),
                }
            )
        )
        if (
            data["phase"] == "crash"
            and data["case"] == "B"
            and len(value.candidates) == 2
            and value.candidates[-1].extraction_complete
        ):
            os._exit(17)

    async def progress(stage: ResearchStage, message: str) -> None:
        return None

    class Flash(EmptyFlash):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            await asyncio.sleep(next(schedule))
            assert request.task_type == "research_web_evidence_normalization"
            current = (await repository.get_checkpoint(lookup)).checkpoint
            assert current is not None and current.acquisition is not None
            ordinal = current.acquisition.candidates[-1].attempt_numbers.get(key, 0)
            ordinals.append(ordinal)
            if data["phase"] == "crash" and data["case"] == "C":
                print(json.dumps({"unknown_ordinal": ordinal}), flush=True)
                os._exit(17)
            return await super().generate(request)

    flash, pro = Flash(), t._AuditProvider()
    coordinator = ResearchWebFallbackCoordinator(
        provider=flash,
        audit_provider=pro,
        discovery=t._AcquisitionDiscovery(session.as_of),
        connector=t._FailingConnector(),
        rights=t._rights(),
        receipts=t._Receipts(),
    )
    try:
        if data["case"] == "C":
            result = await coordinator.collect_real(
                session=session,
                packet=packet,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=state.deadline_at,
                state=state,
                save=save,
                progress=progress,
            )
        else:
            coordinator._acquisition, coordinator._packet = state, packet
            coordinator._save_acquisition = save
            result = await coordinator.complete(
                session=session,
                packet=packet,
                cutoff_mode=ResearchCutoffMode.CURRENT_RUN,
                task_deadline_at=state.deadline_at,
            )
            await save(state, result)
        assert data["phase"] == "resume"
        assert pro.calls == 0
        print(
            json.dumps(
                {
                    "ordinals": ordinals,
                    "candidate_count": len(state.candidates),
                    "attempts": state.attempts,
                    "provider_calls": state.provider_calls,
                    "gaps": [gap.area for gap in result.coverage_gaps],
                }
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
