"""Shared strict behavior for versioned wire contracts."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    GetJsonSchemaHandler,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema


def _validate_decimal_string(value: object) -> Decimal:
    """Accept exact Decimals in Python and decimal strings on external boundaries."""

    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("value must be a valid decimal string") from exc
    raise ValueError("decimal values must be supplied as strings, never JSON numbers")


DecimalString = Annotated[
    Decimal,
    BeforeValidator(_validate_decimal_string),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$",
        }
    ),
]


class ContractModel(BaseModel):
    """Reject silent field loss and validate mutations in process."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        validate_assignment=True,
    )

    @model_validator(mode="before")
    @classmethod
    def versioned_contracts_require_an_explicit_version(cls, value: object) -> object:
        """Reject missing wire versions instead of silently applying a default."""

        if "schema_version" not in cls.model_fields or isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or "schema_version" not in value:
            raise ValueError("schema_version is required for versioned contracts")
        return value

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """Keep generated JSON Schema aligned with runtime version enforcement."""

        schema = handler(core_schema)
        properties = schema.get("properties")
        if isinstance(properties, dict) and "schema_version" in properties:
            required = schema.setdefault("required", [])
            if isinstance(required, list) and "schema_version" not in required:
                required.insert(0, "schema_version")
        return schema
