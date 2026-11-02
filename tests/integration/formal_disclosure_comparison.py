"""One fixed offline case, executable against either isolated source revision."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID

from tests.integration.next_source_fixture import official_run
from tests.unit.qmr import test_research_fallback as helpers

NOW = datetime(2026, 9, 19, tzinfo=UTC)
MATERIAL_SHA256 = hashlib.sha256(
    Path("tests/fixtures/research/formal-disclosure.v1.json").read_bytes()
).hexdigest()


class FixedTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


async def main():
    helpers.fallback_module.datetime = FixedTime
    session = helpers._session(NOW).model_copy(
        update={
            "instrument_id": "XSHG:688307",
            "instrument_name": "中润光学",
            "session_id": UUID(int=1),
            "job_id": UUID(int=2),
            "source_mode": helpers.ResearchRealSourceMode.DEVELOPMENT_ONLY,
        }
    )
    saved = []

    async def save(state, packet):
        saved.append((state.model_copy(deep=True), packet.model_copy(deep=True)))

    started = monotonic()
    result, metrics = await official_run(
        session,
        None,
        helpers.empty_real_evidence_packet(session),
        save,
        formal_attachment=True,
    )
    state = saved[-1][0]
    print(
        json.dumps(
            {
                "material_sha256": MATERIAL_SHA256,
                "material_kind": "synthetic",
                "as_of": NOW.isoformat(),
                "source_module": helpers.fallback_module.__file__,
                "rule": helpers.fallback_module.WEB_FALLBACK_RULE_VERSION,
                "fact_keys": [fact.key for fact in result.facts],
                "audited_fact_keys": [
                    fact.key for fact in result.facts if fact.data_audit_status == "passed"
                ],
                "locations": {fact.key: fact.source_location for fact in result.facts},
                "citation_urls": [citation.locator for citation in result.citations],
                "attempts": state.attempts,
                "round_sources": state.round_sources,
                "mock_logical_calls": state.provider_calls,
                "mock_physical_calls": len(metrics["model_tasks"]) + len(metrics["audit_requests"]),
                "fake_input_tokens": state.input_tokens,
                "fake_output_tokens": state.output_tokens,
                "offline_wall_seconds": monotonic() - started,
                "real_model_requests": 0,
                "real_http_requests": 0,
                "real_benefit": "UNKNOWN",
                "cost": "UNKNOWN",
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
