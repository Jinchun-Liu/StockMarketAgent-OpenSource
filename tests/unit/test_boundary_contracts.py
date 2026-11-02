from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.common import InstrumentRef, MoneyValue, OperationContext
from stock_assistant.contracts.forecasting import ForecastRequest, MarketBar
from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    GenerationResponse,
    ModelOperation,
    ModelProviderCapability,
    PrivacyLevel,
)
from stock_assistant.contracts.jobs import JobExecutionSummary, JobResult, JobStatus
from stock_assistant.contracts.messaging import MessageEnvelope
from stock_assistant.contracts.system import RuntimeCapabilities
from stock_assistant.ports.intelligence import LLMProviderPort


def _instrument() -> InstrumentRef:
    return InstrumentRef(
        instrument_id="SSE:600000",
        symbol="600000",
        exchange="SSE",
        market="CN",
    )


def _context(as_of: datetime) -> OperationContext:
    return OperationContext(schema_version="1.0", as_of=as_of, requested_at=as_of)


def test_boundary_time_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        OperationContext(schema_version="1.0", as_of=datetime(2026, 7, 22, 9, 30))


def test_versioned_contract_rejects_missing_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version is required"):
        OperationContext(as_of=datetime.now(UTC))


def test_context_defaults_correlation_to_request_id() -> None:
    context = _context(datetime.now(UTC))
    assert context.correlation_id == context.request_id


def test_money_serializes_decimal_as_string() -> None:
    value = MoneyValue(amount=Decimal("12.3400"), currency="CNY")
    assert '"amount":"12.3400"' in value.model_dump_json()


def test_financial_contracts_reject_non_finite_values() -> None:
    with pytest.raises(ValidationError):
        MoneyValue(amount=Decimal("NaN"), currency="CNY")


def test_decimal_boundary_rejects_json_numbers() -> None:
    with pytest.raises(ValidationError, match="decimal values must be supplied as strings"):
        MoneyValue.model_validate({"amount": 0.1, "currency": "CNY"})


def test_versioned_event_requires_monotonic_time_and_idempotency() -> None:
    now = datetime.now(UTC)
    payload = JobExecutionSummary(
        operation="test_event",
        reason_code="SYNTHETIC_TEST",
        safe_message="synthetic payload",
        observed_at=now,
    )
    with pytest.raises(ValidationError):
        MessageEnvelope[JobExecutionSummary](
            schema_version="1.0",
            event_name="com.stockassistant.tests.completed.v1",
            producer="unit-tests",
            occurred_at=now,
            produced_at=now - timedelta(seconds=1),
            correlation_id=uuid4(),
            partition_key="SSE:600000",
            idempotency_key="test-idempotency-key-0001",
            payload=payload,
        )


def test_job_result_supports_explicit_skipped_outcome() -> None:
    now = datetime.now(UTC)
    result = JobResult[JobExecutionSummary](
        schema_version="1.0",
        job_id=uuid4(),
        status=JobStatus.SKIPPED,
        started_at=now,
        completed_at=now,
        result=JobExecutionSummary(
            operation="premarket_ingest",
            reason_code="PROVIDER_NOT_CONFIGURED",
            safe_message="synthetic provider is disabled",
            observed_at=now,
        ),
        attempt=1,
    )
    assert result.status is JobStatus.SKIPPED


def test_forecast_request_rejects_future_market_data() -> None:
    now = datetime.now(UTC)
    instrument = _instrument()
    future_bar = MarketBar(
        instrument=instrument,
        interval="1min",
        opened_at=now,
        closed_at=now + timedelta(minutes=1),
        received_at=now + timedelta(minutes=1, seconds=1),
        open_price=Decimal("10.00"),
        high_price=Decimal("10.10"),
        low_price=Decimal("9.90"),
        close_price=Decimal("10.05"),
        volume=Decimal("1000"),
        adjusted=False,
        source_id="synthetic_quotes",
        data_version="test-v1",
        adjustment_version="raw-v1",
        rights_snapshot_hash="a" * 64,
    )
    with pytest.raises(ValidationError):
        ForecastRequest(
            schema_version="1.0",
            context=_context(now),
            instrument=instrument,
            horizon_minutes=10,
            bars=[future_bar],
            data_snapshot_id=uuid4(),
            data_manifest_hash="b" * 64,
            feature_set_version="test-features-v1",
        )


def test_runtime_capabilities_cannot_enable_real_trading() -> None:
    with pytest.raises(ValidationError):
        RuntimeCapabilities.model_validate(
            {
                "local_model": "synthetic-model",
                "external_llm_enabled": False,
                "social_source_enabled": False,
                "directional_forecast_enabled": False,
                "trading_enabled": True,
            }
        )


class _StructurallyValidLLM:
    @property
    def provider_name(self) -> str:
        return "synthetic"

    @property
    def capability(self) -> ModelProviderCapability:
        return ModelProviderCapability(
            schema_version="1.0",
            provider_name="synthetic",
            model="synthetic-model",
            adapter_version="1.0.0",
            operations=[ModelOperation.GENERATION],
            supported_privacy_levels=[PrivacyLevel.LOCAL_ONLY],
            external_processing=False,
            structured_output=True,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        raise NotImplementedError(request)


def test_runtime_checkable_port_accepts_structural_adapter() -> None:
    assert isinstance(_StructurallyValidLLM(), LLMProviderPort)
