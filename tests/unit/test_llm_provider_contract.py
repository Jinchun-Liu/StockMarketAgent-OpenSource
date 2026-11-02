import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.common import OperationContext
from stock_assistant.platform.llm.provider import GenerationRequest, GenerationResponse


def _schema_and_hash() -> tuple[dict[str, object], str]:
    schema: dict[str, object] = {"type": "object"}
    canonical = json.dumps(schema, separators=(",", ":"), sort_keys=True).encode()
    return schema, hashlib.sha256(canonical).hexdigest()


def _context() -> OperationContext:
    now = datetime.now(UTC)
    return OperationContext(schema_version="1.0", as_of=now, requested_at=now)


def test_generation_contract_preserves_audit_metadata() -> None:
    schema, schema_hash = _schema_and_hash()
    request = GenerationRequest(
        schema_version="1.0",
        task_type="event_extraction",
        messages=[{"role": "user", "content": "synthetic evidence"}],
        response_schema=schema,
        response_schema_hash=schema_hash,
        response_schema_namespace="events.market_event",
        response_schema_owner="events",
        evidence_ids=[uuid4()],
        context=_context(),
    )
    response = GenerationResponse(
        schema_version="1.0",
        payload={"direction": "neutral"},
        payload_namespace="events.market_event",
        payload_owner="events",
        response_schema_hash=schema_hash,
        provider="local-vllm",
        model="test-model",
        adapter_version="1.0.0",
        prompt_hash="a" * 64,
        raw_response_hash="b" * 64,
        input_tokens=10,
        output_tokens=3,
        latency_ms=25,
        schema_validated=True,
    )

    assert request.privacy_level == "local_only"
    assert response.provider == "local-vllm"

    unvalidated = response.model_dump()
    unvalidated.pop("schema_validated")
    with pytest.raises(ValidationError):
        GenerationResponse.model_validate(unvalidated)


def test_generation_request_rejects_empty_messages() -> None:
    schema, schema_hash = _schema_and_hash()
    with pytest.raises(ValidationError):
        GenerationRequest(
            schema_version="1.0",
            task_type="event_extraction",
            messages=[],
            response_schema=schema,
            response_schema_hash=schema_hash,
            response_schema_namespace="events.market_event",
            response_schema_owner="events",
            context=_context(),
        )


def test_generation_request_rejects_unknown_fields() -> None:
    schema, schema_hash = _schema_and_hash()
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate(
            {
                "schema_version": "1.0",
                "task_type": "event_extraction",
                "messages": [{"role": "user", "content": "evidence"}],
                "response_schema": schema,
                "response_schema_hash": schema_hash,
                "response_schema_namespace": "events.market_event",
                "response_schema_owner": "events",
                "context": _context(),
                "unexpected": "silent contract drift",
            }
        )
