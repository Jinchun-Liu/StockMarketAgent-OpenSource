"""Kill/restart the original source path with real PostgreSQL writes and fake I/O."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.next_source_fixture import official_run
from tests.integration.test_research_repository import _lookup

from stock_assistant.contracts.research import ResearchCheckpointWriteRequest
from stock_assistant.platform.db.research import PostgresResearchRepository


async def main():
    data = json.loads(sys.stdin.read())
    engine = create_async_engine(data["database_url"])
    repository = PostgresResearchRepository(engine)
    template = ResearchCheckpointWriteRequest.model_validate(data["write"])
    lookup = _lookup(template.session_id, datetime.now(UTC))
    session = (await repository.get(lookup)).session
    assert session is not None
    checkpoint = (await repository.get_checkpoint(lookup)).checkpoint or template.checkpoint
    state, packet = checkpoint.acquisition, checkpoint.packet
    original_pending = (
        state.pending_query.model_dump_json() if state and state.pending_query else None
    )
    first_saved = None
    ordinals = []
    formal_attachment = data.get("formal_attachment", False)

    async def save(value, current):
        nonlocal first_saved
        snapshot = checkpoint.model_copy(update={"acquisition": value, "packet": current})
        await repository.save_checkpoint(
            template.model_copy(
                update={
                    "context": template.context.model_copy(
                        update={"requested_at": datetime.now(UTC)}
                    ),
                    "checkpoint": snapshot,
                }
            )
        )
        if first_saved is None:
            first_saved = value.model_dump(mode="json")
        if data["phase"] == "crash":
            if (
                formal_attachment
                and data["case"] in {"stop_saved", "cancel", "new_job"}
                and value.attachment_queue
                and not value.documents
            ):
                os._exit(17)
            if formal_attachment and data["case"] == "document_saved" and value.documents:
                os._exit(17)
            if (
                not formal_attachment
                and data["case"] in {"stop_saved", "cancel", "new_job"}
                and any(
                    s.origin == "https://static.cninfo.com.cn" for s in value.robots_origin_stops
                )
            ):
                assert value.pending_query is not None and value.pending_discovery is not None
                assert value.attempts["identity.company"] == 2
                assert any(
                    g.reason_code == "WEB_SOURCE_ACCESS_BLOCKED" for g in current.coverage_gaps
                )
                os._exit(17)
            if data["case"] == "facts_saved" and any(
                f.key == "identity.company" for f in current.facts
            ):
                os._exit(17)

    async def before_model(request):
        if request.task_type != "research_web_evidence_normalization":
            return
        current = (await repository.get_checkpoint(lookup)).checkpoint
        assert current is not None and current.acquisition is not None
        candidate = current.acquisition.candidates[-1]
        if "identity.company" in candidate.attempt_numbers:
            ordinals.append(candidate.attempt_numbers["identity.company"])
        if data["phase"] == "crash" and data["case"] == "unknown_send":
            print(json.dumps({"unknown_ordinal": ordinals[-1]}), flush=True)
            os._exit(17)

    try:
        result, metrics = await official_run(
            session, state, packet, save, before_model, formal_attachment=formal_attachment
        )
        assert data["phase"] == "resume"
        print(
            json.dumps(
                {
                    **metrics,
                    "fact_keys": [f.key for f in result.facts],
                    "ordinals": ordinals,
                    "original_pending": original_pending,
                    "first_saved": first_saved,
                }
            ),
            flush=True,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
