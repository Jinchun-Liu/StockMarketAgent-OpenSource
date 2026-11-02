import pytest

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.contracts.ingestion import SourceRuntimeMode, SourceRuntimeModeV1_2
from stock_assistant.platform.db.r1_premarket import _resolve_briefing_schema_version


@pytest.mark.parametrize("schema_version", ["1.1", "1.2"])
def test_demo_briefing_schema_is_resolved_from_persisted_provenance(
    schema_version: str,
) -> None:
    payloads = tuple({"schema_version": schema_version} for _ in range(5))

    assert (
        _resolve_briefing_schema_version(
            payloads,
            runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        )
        == schema_version
    )


def test_explicit_v1_2_mode_rejects_legacy_or_mixed_provenance() -> None:
    with pytest.raises(ApplicationError) as legacy_error:
        _resolve_briefing_schema_version(
            ({"schema_version": "1.1"},),
            runtime_mode=SourceRuntimeModeV1_2.DEV_LIVE,
        )
    with pytest.raises(ApplicationError) as mixed_error:
        _resolve_briefing_schema_version(
            ({"schema_version": "1.1"}, {"schema_version": "1.2"}),
            runtime_mode=SourceRuntimeMode.DEMO_REPLAY,
        )

    assert legacy_error.value.code is ErrorCode.PRECONDITION_FAILED
    assert legacy_error.value.safe_message == "R1_SOURCE_SCHEMA_MISMATCH"
    assert mixed_error.value.code is ErrorCode.PRECONDITION_FAILED
    assert mixed_error.value.safe_message == "R1_SOURCE_SCHEMA_MISMATCH"
