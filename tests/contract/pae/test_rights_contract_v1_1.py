import json
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.ingestion import (
    RightsCheck,
    RightsCheckV1_1,
    RightsContentCategory,
    RightsDecision,
    RightsDecisionV1_1,
    RightsOperation,
    RightsPermittedUse,
)

NOW = datetime(2026, 7, 29, 0, 55, tzinfo=UTC)
RIGHTS_HASH = "a" * 64
R1_SOURCES = ("cninfo_data_service", "choice_quant_api")
R1_OPERATIONS = tuple(RightsOperation)
R1_PERMITTED_USES = (
    RightsPermittedUse.DISPLAY,
    RightsPermittedUse.SUMMARY,
    RightsPermittedUse.NON_DISPLAY,
    RightsPermittedUse.AI_USE,
)
PACK_PATH = Path("tests/contract/packs/rights/r1-evaluation-matrix.v1.1.json")
CHECK_SCHEMA_PATH = Path("schemas/ingestion/rights-check.v1.1.schema.json")
DECISION_SCHEMA_PATH = Path("schemas/ingestion/rights-decision.v1.1.schema.json")


def _context() -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        request_id=UUID("00000000-0000-4000-8000-000000000011"),
        correlation_id=UUID("00000000-0000-4000-8000-000000000012"),
        idempotency_key="rights-contract-v1-1",
        actor_id="contract-test",
        as_of=NOW,
        requested_at=NOW,
    )


@pytest.mark.contract
def test_r1_rights_matrix_covers_every_source_category_operation_and_use() -> None:
    document = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    dimensions = set(
        product(
            document["sources"],
            map(RightsContentCategory, document["content_categories"]),
            map(RightsOperation, document["operations"]),
            map(RightsPermittedUse, document["permitted_uses"]),
        )
    )
    expected = set(product(R1_SOURCES, RightsContentCategory, R1_OPERATIONS, R1_PERMITTED_USES))
    checks = {
        (
            check.source_id,
            check.content_category,
            check.operation,
            check.permitted_use,
        )
        for source_id, category, operation, permitted_use in dimensions
        if (
            check := RightsCheckV1_1.model_validate(
                {
                    **document["check_template"],
                    "source_id": source_id,
                    "content_category": category,
                    "operation": operation,
                    "permitted_use": permitted_use,
                }
            )
        )
    }

    assert dimensions == expected
    assert checks == expected
    assert RightsOperation.DERIVE in R1_OPERATIONS


@pytest.mark.contract
def test_versioned_rights_check_requires_explicit_permitted_use() -> None:
    values = {
        "schema_version": "1.1",
        "context": _context(),
        "source_id": "licensed_source",
        "operation": RightsOperation.FETCH,
        "rights_snapshot_hash": RIGHTS_HASH,
        "content_category": RightsContentCategory.STATUTORY_ANNOUNCEMENTS,
    }

    with pytest.raises(ValidationError):
        RightsCheckV1_1.model_validate(values)

    check = RightsCheckV1_1(
        **values,
        permitted_use=RightsPermittedUse.NON_DISPLAY,
    )
    assert check.permitted_use is RightsPermittedUse.NON_DISPLAY


@pytest.mark.contract
def test_rights_check_rejects_unknown_operations_and_uses() -> None:
    base = RightsCheckV1_1(
        schema_version="1.1",
        context=_context(),
        source_id="licensed_source",
        operation=RightsOperation.DERIVE,
        rights_snapshot_hash=RIGHTS_HASH,
        content_category=RightsContentCategory.MARKET_SNAPSHOT,
        permitted_use=RightsPermittedUse.SUMMARY,
    ).model_dump(mode="json")

    for field, value in (
        ("operation", "ai_use"),
        ("permitted_use", "internal_research"),
        ("permitted_use", "llm_input"),
        ("permitted_use", "display_source"),
        ("permitted_use", "display_derived"),
    ):
        invalid = {**base, field: value}
        with pytest.raises(ValidationError):
            RightsCheckV1_1.model_validate(invalid)


@pytest.mark.contract
def test_versioned_rights_check_rejects_arbitrary_capability_categories() -> None:
    with pytest.raises(ValidationError):
        RightsCheckV1_1(
            schema_version="1.1",
            context=_context(),
            source_id="licensed_source",
            operation=RightsOperation.DERIVE,
            rights_snapshot_hash=RIGHTS_HASH,
            content_category="vendor_specific_capability",
        )


@pytest.mark.contract
def test_in_process_rights_models_remain_source_compatible() -> None:
    checks = [
        RightsCheck(
            context=_context(),
            source_id="licensed_source",
            operation=RightsOperation.DERIVE,
            rights_snapshot_hash=RIGHTS_HASH,
            content_category=content_category,
        )
        for content_category in ("A", "x", RightsContentCategory.MARKET_SNAPSHOT)
    ]
    decision = RightsDecision(
        allowed=True,
        reason_code="RIGHTS_ALLOWED",
        decided_at=NOW,
        rights_snapshot_hash=RIGHTS_HASH,
    )

    assert [check.content_category for check in checks] == ["A", "x", "market_snapshot"]
    assert all(check.permitted_use is RightsPermittedUse.NON_DISPLAY for check in checks)
    assert decision.allowed is True
    assert "schema_version" not in RightsCheck.model_fields
    assert "schema_version" not in type(decision).model_fields


@pytest.mark.contract
@pytest.mark.parametrize("content_category", ("", "x" * 101))
def test_in_process_rights_check_preserves_legacy_category_length_bounds(
    content_category: str,
) -> None:
    with pytest.raises(ValidationError):
        RightsCheck(
            context=_context(),
            source_id="licensed_source",
            operation=RightsOperation.FETCH,
            rights_snapshot_hash=RIGHTS_HASH,
            content_category=content_category,
        )


@pytest.mark.contract
@pytest.mark.parametrize("model", (RightsCheckV1_1, RightsDecisionV1_1))
def test_versioned_wire_models_require_explicit_schema_version(
    model: type[RightsCheckV1_1] | type[RightsDecisionV1_1],
) -> None:
    values = (
        {
            "context": _context(),
            "source_id": "licensed_source",
            "operation": RightsOperation.DERIVE,
            "rights_snapshot_hash": RIGHTS_HASH,
            "content_category": RightsContentCategory.MARKET_SNAPSHOT,
            "permitted_use": RightsPermittedUse.NON_DISPLAY,
        }
        if model is RightsCheckV1_1
        else {
            "allowed": True,
            "reason_code": "RIGHTS_ALLOWED",
            "decided_at": NOW,
            "rights_snapshot_hash": RIGHTS_HASH,
        }
    )

    with pytest.raises(ValidationError, match="schema_version is required"):
        model.model_validate(values)


@pytest.mark.contract
@pytest.mark.parametrize(
    ("path", "model"),
    (
        (CHECK_SCHEMA_PATH, RightsCheckV1_1),
        (DECISION_SCHEMA_PATH, RightsDecisionV1_1),
    ),
)
def test_published_rights_schemas_match_pydantic(
    path: Path,
    model: type[RightsCheckV1_1] | type[RightsDecisionV1_1],
) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    assert schema["x-schema-version"] == "1.1"
    assert schema["x-contract-model"] == f"{model.__module__}.{model.__qualname__}"
    assert schema == _published_schema(model, path)


@pytest.mark.contract
def test_fixture_checks_validate_against_the_published_schema() -> None:
    schema = json.loads(CHECK_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    document = json.loads(PACK_PATH.read_text(encoding="utf-8"))

    for source_id, category, operation, permitted_use in product(
        document["sources"],
        document["content_categories"],
        document["operations"],
        document["permitted_uses"],
    ):
        validator.validate(
            {
                **document["check_template"],
                "source_id": source_id,
                "content_category": category,
                "operation": operation,
                "permitted_use": permitted_use,
            }
        )


def _published_schema(
    model: type[RightsCheckV1_1] | type[RightsDecisionV1_1],
    path: Path,
) -> dict[str, object]:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        f"https://stock-market-agent.local/schemas/{path.relative_to('schemas').as_posix()}"
    )
    schema["x-contract-model"] = f"{model.__module__}.{model.__qualname__}"
    schema["x-schema-version"] = "1.1"
    return schema
