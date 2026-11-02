"""Isolated fake-model child for real PostgreSQL crash/recovery tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from itertools import cycle

from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.test_research_repository import _lookup
from tests.unit.qmr.test_r3_report_regressions import PREMISE, ExtraPremiseOracle

from stock_assistant.contracts.research import ResearchCheckpoint, ResearchCheckpointWriteRequest
from stock_assistant.modules.research.reporting import (
    ResearchGenerationError,
    ResearchReportGenerator,
)
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
    saved = (await repository.get_checkpoint(lookup)).checkpoint
    packet = template.checkpoint.packet
    provider = ExtraPremiseOracle(data["dependent"])

    async def save(value: ResearchCheckpoint) -> None:
        await asyncio.sleep(next(schedule))
        await repository.save_checkpoint(
            template.model_copy(
                update={
                    "context": template.context.model_copy(
                        update={"requested_at": datetime.now(UTC)}
                    ),
                    "checkpoint": value,
                }
            )
        )
        if data["phase"] != "crash":
            return
        boundary = data["boundary"]
        stop = (
            (
                boundary in {"first_reject", "invalidation"}
                and value.support_assessment is not None
                and value.support_rounds_reserved == 1
                and not value.support_pending_ids
            )
            or (
                boundary == "before_second"
                and value.support_rounds_reserved == 2
                and bool(value.support_pending_ids)
            )
            or (
                boundary == "second_saved"
                and value.support_rounds_reserved == 2
                and not value.support_pending_ids
            )
        )
        if stop:
            # Terminate the writer process after the transaction commits, without
            # unwinding the generator or manually modifying its durable state.
            os._exit(17)

    try:
        reason = None
        try:
            result = await ResearchReportGenerator(provider).generate(
                session=session,
                packet=packet,
                checkpoint=saved,
                save_checkpoint=save,
                deadline_at=template.context.deadline_at,
            )
            complete = True
        except ResearchGenerationError as error:
            result = error.available_result
            complete = False
            reason = str(error)
        assert data["phase"] == "resume", "crash boundary was not reached"
        assert result is not None
        print(
            json.dumps(
                {
                    "complete": complete,
                    "reason": reason,
                    "contains_rejected_premise": PREMISE in result.model_dump_json(),
                    "request_types": [r.task_type for r in provider.requests],
                    "audit_ids": [
                        [item["item_id"] for item in json.loads(r.messages[-1].content)["items"]]
                        for r in provider.requests
                        if r.task_type == "research_support_assessment"
                    ],
                }
            )
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
