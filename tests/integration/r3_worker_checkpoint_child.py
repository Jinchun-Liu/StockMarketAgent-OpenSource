"""Run the original worker in a disposable process with explicit fake adapters."""

from __future__ import annotations

import asyncio
import json
import sys
from itertools import cycle

import pytest
from apps.worker import tasks
from sqlalchemy.ext.asyncio import create_async_engine
from tests.integration.test_research_repository import (
    _configure_worker,
    _CoordinatedResearchLLMProvider,
    _ResearchObjectStore,
)

from stock_assistant.contracts.intelligence import GenerationRequest, GenerationResponse
from stock_assistant.contracts.research import parse_research_generate_request


async def main() -> None:
    data = json.loads(sys.stdin.readline())
    schedule = cycle(((data["seed"] * step) % 3001) / 1_000_000 for step in (137, 503, 997))
    engine = create_async_engine(data["database_url"])
    monkeypatch = pytest.MonkeyPatch()

    async def barrier(event: str) -> None:
        print(json.dumps({"event": event}), flush=True)
        command = await asyncio.to_thread(sys.stdin.readline)
        assert command.strip() == "release"

    class Provider(_CoordinatedResearchLLMProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            await asyncio.sleep(next(schedule))
            if data["hold"] == "support" and request.task_type == "research_support_assessment":
                await barrier("support-send")
            return await super().generate(request)

    class Store(_ResearchObjectStore):
        async def put(self, request, chunks):
            await asyncio.sleep(next(schedule))
            if data["hold"] == "artifact":
                await barrier("artifact-write")
            return await super().put(request, chunks)

    provider = Provider()
    _configure_worker(monkeypatch, provider=provider, object_store=Store())
    try:
        try:
            result = await tasks._execute_research_generate(
                parse_research_generate_request(data["task"]),
                engine=engine,
            )
            print(
                json.dumps(
                    {
                        "status": result.status.value,
                        "reason": result.reason_code,
                        "tasks": provider.task_types,
                    }
                ),
                flush=True,
            )
        except Exception as error:
            print(
                json.dumps({"error_type": type(error).__name__, "tasks": provider.task_types}),
                flush=True,
            )
    finally:
        monkeypatch.undo()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
