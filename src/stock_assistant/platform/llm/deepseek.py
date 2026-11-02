"""DeepSeek's OpenAI-compatible generation adapter with fail-closed validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Final, Literal, Protocol, cast
from uuid import UUID

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params.response_format_json_object import ResponseFormatJSONObject
from pydantic import JsonValue, SecretStr, ValidationError

from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    GenerationResponse,
    ModelOperation,
    ModelProviderCapability,
    PrivacyLevel,
)
from stock_assistant.contracts.research import (
    ResearchSupportAssessment,
    ResearchSupportLeafAssessment,
)
from stock_assistant.contracts.research_reader import (
    ResearchScoreAssessmentDraft,
    ResearchScoreCandidate,
    ResearchScoreDimensionId,
    contains_forbidden_investment_advice,
    contains_numeric_expression,
    is_han_dominant_user_prose,
)
from stock_assistant.ports.intelligence import LLMProviderError, LLMProviderFailureKind

DEEPSEEK_BASE_URL: Final = "https://api.deepseek.com"
DEEPSEEK_FLASH_MODEL: Final = "deepseek-v4-flash"
DEEPSEEK_PRO_MODEL: Final = "deepseek-v4-pro"
DEEPSEEK_ADAPTER_VERSION: Final = "deepseek-openai-compatible.v2.1"
DEEPSEEK_MAX_PROVIDER_CALLS: Final = 4
DEEPSEEK_MAX_TRANSPORT_RETRIES: Final = 2
DEEPSEEK_MAX_SCHEMA_REPAIRS: Final = 1
DEEPSEEK_REPAIR_CONTEXT_MAX_BYTES: Final = 48_000
_NUMERIC_PROSE_FALLBACK: Final = "量化内容未通过证据校验, 已隐藏并列为未知项。"
_DIRECT_STRUCTURED_TASKS: Final = frozenset(
    {
        "research_report_narrative",
        "research_score_assessment",
        "research_support_assessment",
        "research_reader_rewrite",
    }
)
_REPAIRABLE_RESPONSE_CODES: Final = frozenset(
    {
        "DEEPSEEK_RESPONSE_INCOMPLETE",
        "DEEPSEEK_RESPONSE_EMPTY",
        "DEEPSEEK_RESPONSE_JSON_INVALID",
        "DEEPSEEK_RESPONSE_SCHEMA_INVALID",
    }
)
_AUTH_CONFIG_CODES: Final = frozenset(
    {
        "DEEPSEEK_AUTHENTICATION_FAILED",
        "DEEPSEEK_BASE_URL_FORBIDDEN",
        "DEEPSEEK_MODEL_FORBIDDEN",
        "DEEPSEEK_PRIVACY_LEVEL_FORBIDDEN",
        "DEEPSEEK_REQUEST_REJECTED",
        "DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED",
    }
)
# Only product field names enter diagnostic paths. Unknown schema keys are masked.
_DIAGNOSTIC_FIELDS: Final = frozenset(
    "schema_version sections kind title summary hypotheses section statement "
    "supporting_evidence_ids counter_evidence_ids falsification_conditions uncertainty "
    "supporting_basis counter_basis evidence_id fact_key excerpt verdicts item_id supported "
    "reason items text points business_quality growth_quality risk_resilience "
    "valuation_attractiveness score evidence_ids dimension_id dimensions explanation "
    "takeaway facts bull_case bear_case synthesis risks unknowns sources term definition "
    "grade reasons supporting_fact_keys counter_fact_keys reason_code "
    "missing_fact_key source_excerpt value unit source_location period".split()
) | frozenset(dimension.value for dimension in ResearchScoreDimensionId)
_SchemaRule = Literal[
    "JSON_TYPE",
    "CONST_VALUE",
    "ENUM_VALUE",
    "STRING_MIN_LENGTH",
    "STRING_MAX_LENGTH",
    "STRING_PATTERN",
    "NUMERIC_EXPRESSION",
    "LANGUAGE_PROFILE",
    "INVESTMENT_ADVICE",
    "UUID_FORMAT",
    "NUMBER_MINIMUM",
    "NUMBER_MAXIMUM",
    "ARRAY_MIN_ITEMS",
    "ARRAY_MAX_ITEMS",
    "EXACT_ENUM",
    "UNIQUE_FIELD",
    "REQUIRED_PROPERTIES",
    "ADDITIONAL_PROPERTIES",
    "SCORE_SUPPORT_REQUIRED",
    "SUPPORT_VERDICT_CONFLICT",
    "EVIDENCE_SELECTION",
    "EVIDENCE_INDEPENDENCE",
    "INCOMPLETE_RESPONSE",
    "EMPTY_RESPONSE",
    "INVALID_JSON",
    "JSON_ROOT_TYPE",
]


@dataclass(frozen=True, slots=True)
class _SchemaViolation:
    path: str
    rule: _SchemaRule


DeepSeekModel = Literal["deepseek-v4-flash", "deepseek-v4-pro"]


class DeepSeekProviderError(LLMProviderError):
    """A stable, safe provider failure without upstream response text."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        repair_hint: str | None = None,
        failure_kind: LLMProviderFailureKind | None = None,
        retry_after_seconds: float | None = None,
        schema_violations: tuple[_SchemaViolation, ...] = (),
        previous_validation_code: str | None = None,
    ) -> None:
        if failure_kind is None:
            if code in _AUTH_CONFIG_CODES:
                failure_kind = LLMProviderFailureKind.AUTH_CONFIG
            elif code.startswith("DEEPSEEK_RESPONSE_"):
                failure_kind = LLMProviderFailureKind.INVALID_OUTPUT
            elif retryable:
                failure_kind = LLMProviderFailureKind.TRANSPORT
            else:
                failure_kind = LLMProviderFailureKind.AUTH_CONFIG
        super().__init__(code, failure_kind=failure_kind, retryable=retryable)
        self.repair_hint = repair_hint
        self.retry_after_seconds = retry_after_seconds
        self.schema_violations = schema_violations
        self.previous_validation_code = previous_validation_code


class _ValidatedResponseError(DeepSeekProviderError):
    """Marks repair feedback produced exclusively by the local response validator."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        repair_hint: str | None = None,
        numeric_sanitized_fields: int = 0,
        schema_violations: tuple[_SchemaViolation, ...] = (),
    ) -> None:
        super().__init__(
            code, retryable=retryable, repair_hint=repair_hint, schema_violations=schema_violations
        )
        self.numeric_sanitized_fields = numeric_sanitized_fields


@dataclass(frozen=True, slots=True)
class DeepSeekTransportRequest:
    model: DeepSeekModel
    messages: tuple[tuple[str, str], ...]
    maximum_output_tokens: int
    timeout_seconds: float
    thinking_enabled: bool


@dataclass(frozen=True, slots=True)
class DeepSeekTransportResponse:
    content: str | None
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    raw_response_hash: str


class DeepSeekTransport(Protocol):
    async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse: ...


class OpenAICompatibleDeepSeekTransport:
    """The only module that knows the OpenAI SDK response surface."""

    def __init__(self, *, api_key: SecretStr, base_url: str = DEEPSEEK_BASE_URL) -> None:
        normalized = base_url.rstrip("/")
        if normalized != DEEPSEEK_BASE_URL:
            raise DeepSeekProviderError("DEEPSEEK_BASE_URL_FORBIDDEN")
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=normalized,
            max_retries=0,
        )

    async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
        messages = [
            cast(ChatCompletionMessageParam, {"role": role, "content": content})
            for role, content in request.messages
        ]
        response_format: ResponseFormatJSONObject = {"type": "json_object"}
        try:
            response = await self._client.chat.completions.create(
                model=request.model,
                messages=messages,
                response_format=response_format,
                temperature=0.0,
                max_tokens=request.maximum_output_tokens,
                timeout=request.timeout_seconds,
                extra_body={
                    "thinking": {"type": "enabled" if request.thinking_enabled else "disabled"}
                },
            )
        except (RateLimitError, InternalServerError) as exc:
            raise DeepSeekProviderError(
                "DEEPSEEK_RATE_LIMITED"
                if isinstance(exc, RateLimitError)
                else "DEEPSEEK_TEMPORARILY_UNAVAILABLE",
                retryable=True,
                retry_after_seconds=_retry_after_seconds(exc.response.headers.get("retry-after")),
            ) from exc
        except APITimeoutError as exc:
            raise DeepSeekProviderError("DEEPSEEK_TIMEOUT", retryable=True) from exc
        except APIConnectionError as exc:
            raise DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True) from exc
        except AuthenticationError as exc:
            raise DeepSeekProviderError("DEEPSEEK_AUTHENTICATION_FAILED") from exc
        except BadRequestError as exc:
            raise DeepSeekProviderError("DEEPSEEK_REQUEST_REJECTED") from exc

        if len(response.choices) != 1:
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_CHOICE_INVALID")
        choice = response.choices[0]
        usage = response.usage
        raw_hash = hashlib.sha256(
            response.model_dump_json(exclude_none=False).encode("utf-8")
        ).hexdigest()
        return DeepSeekTransportResponse(
            content=choice.message.content,
            finish_reason=choice.finish_reason,
            input_tokens=0 if usage is None else usage.prompt_tokens,
            output_tokens=0 if usage is None else usage.completion_tokens,
            raw_response_hash=raw_hash,
        )


def _retry_after_seconds(value: str | None) -> float | None:
    """Honor both RFC delay-seconds and HTTP-date without ever shortening the wait."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _prompt_hash(request: GenerationRequest) -> str:
    payload = [
        {"role": item.role.value, "content": item.content, "name": item.name}
        for item in request.messages
    ]
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _schema_instruction(schema: Mapping[str, object]) -> str:
    return (
        "Return one JSON object only. The object must exactly match this JSON Schema, "
        "including every required property and no additional properties: "
        f"{_canonical(schema).decode('utf-8')}"
    )


def _repair_instruction(
    code: str,
    *,
    repair_hint: str | None,
    repair_round: int,
    direct_structured_task: bool,
    prior_candidate_included: bool,
) -> str:
    constraint = (
        " Re-check the complete schema before returning."
        if repair_hint is None
        else f" Deterministic validator feedback: {repair_hint}"
    )
    whole_object_repair = (
        " Correct every invalid field in the whole object during this round, not only the "
        "first reported field. For arrays marked with an exact-enum field, include each "
        "schema-declared value exactly once and use no other enum value."
        if direct_structured_task and code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
        else ""
    )
    candidate_instruction = (
        "Revise the immediately preceding assistant JSON. " if prior_candidate_included else ""
    )
    status = (
        "The previous draft has incomplete counter-evidence grounding with code "
        if code == "DEEPSEEK_RESPONSE_COUNTER_BASIS_INCOMPLETE"
        else "The previous response failed deterministic validation with code "
    )
    return (
        f"{candidate_instruction}{status}"
        f"{code}. This is repair round {repair_round}."
        f"{constraint}{whole_object_repair} Return one complete, concise JSON object that "
        "exactly matches the "
        "supplied schema. Do not include Markdown, commentary, or additional properties."
    )


def _json_pointer(path: tuple[str | int, ...]) -> str:
    if not path:
        return ""
    escaped = [str(item).replace("~", "~0").replace("/", "~1") for item in path]
    return "/" + "/".join(escaped)


def _json_location(path: tuple[str | int, ...]) -> str:
    pointer = _json_pointer(path)
    return "At the document root" if not pointer else f"At JSON pointer {pointer}"


def _schema_invalid(
    path: tuple[str | int, ...],
    requirement: str,
    rule: _SchemaRule,
) -> DeepSeekProviderError:
    """Build model-safe repair feedback without echoing invalid response content."""

    return DeepSeekProviderError(
        "DEEPSEEK_RESPONSE_SCHEMA_INVALID",
        repair_hint=f"{_json_location(path)}, {requirement}.",
        schema_violations=(
            _SchemaViolation(
                _json_pointer(
                    tuple(
                        part if isinstance(part, int) or part in _DIAGNOSTIC_FIELDS else "*"
                        for part in path[:12]
                    )
                ),
                rule,
            ),
        ),
    )


def _sanitize_numeric_expression_fields(
    value: object,
    schema: Mapping[str, object],
    root_schema: Mapping[str, object],
) -> tuple[object, int]:
    """Replace persistently unsafe model prose with one explicit unknown marker."""

    schema = _resolve_local_ref(schema, root_schema)
    expected = schema.get("type")
    if expected == "string":
        if (
            isinstance(value, str)
            and schema.get("x-stockassistant-no-numeric-expression") is True
            and contains_numeric_expression(value)
        ):
            return _NUMERIC_PROSE_FALLBACK, 1
        return value, 0
    if expected == "array" and isinstance(value, list):
        items = schema.get("items")
        if not isinstance(items, Mapping):
            return value, 0
        sanitized_items: list[object] = []
        sanitized_count = 0
        for item in value:
            sanitized, count = _sanitize_numeric_expression_fields(item, items, root_schema)
            sanitized_items.append(sanitized)
            sanitized_count += count
        return sanitized_items, sanitized_count
    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return value, 0
        sanitized_object: dict[str, object] = {}
        sanitized_count = 0
        for key, item in value.items():
            item_schema = properties.get(key)
            if isinstance(item_schema, Mapping):
                sanitized, count = _sanitize_numeric_expression_fields(
                    item,
                    item_schema,
                    root_schema,
                )
                sanitized_object[key] = sanitized
                sanitized_count += count
            else:
                sanitized_object[key] = item
        return sanitized_object, sanitized_count
    return value, 0


def _schema_value_summary(value: object) -> str:
    """Render only project-owned schema values, never invalid model content."""

    encoded = _canonical(value)
    return encoded.decode("utf-8") if len(encoded) <= 800 else "the value declared in the schema"


def _transport_retry_delay_seconds(request: GenerationRequest, attempt: int) -> float:
    """Return bounded exponential backoff with stable per-request jitter."""

    seed = hashlib.sha256(request.context.request_id.bytes + bytes([attempt])).digest()[0]
    jitter = 0.75 + (seed / 255.0) * 0.5
    return min(1.0, 0.2 * (2.0**attempt) * jitter)


def _attempt_timeout_seconds(
    *,
    remaining_seconds: float,
    profile_cap_seconds: float,
) -> float:
    """Use the request's one absolute deadline without shortening its first attempt."""

    return max(0.001, min(remaining_seconds, profile_cap_seconds))


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _resolve_local_ref(
    schema: Mapping[str, object], root_schema: Mapping[str, object]
) -> Mapping[str, object]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    if set(schema) != {"$ref"} or not isinstance(reference, str):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    prefix = "#/$defs/"
    if not reference.startswith(prefix) or "/" in reference[len(prefix) :]:
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    definitions = root_schema.get("$defs")
    if not isinstance(definitions, Mapping):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    resolved = definitions.get(reference[len(prefix) :])
    if not isinstance(resolved, Mapping):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    return resolved


def _validate_schema_node(
    value: object,
    schema: Mapping[str, object],
    root_schema: Mapping[str, object],
    repair_errors: list[DeepSeekProviderError],
    path: tuple[str | int, ...] = (),
) -> bool:
    """Collect sibling failures for the existing single repair, without hiding schema errors."""

    try:
        return _validate_schema_value(value, schema, root_schema, repair_errors, path)
    except DeepSeekProviderError as error:
        if error.code != "DEEPSEEK_RESPONSE_SCHEMA_INVALID":
            raise
        if len(repair_errors) < 16:
            repair_errors.append(error)
        return False


def _validate_schema_value(
    value: object,
    schema: Mapping[str, object],
    root_schema: Mapping[str, object],
    repair_errors: list[DeepSeekProviderError],
    path: tuple[str | int, ...],
) -> bool:
    """Validate the bounded JSON-Schema subset used by project interpretation outputs."""

    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "pattern",
        "description",
        "title",
        "$schema",
        "$defs",
        "$ref",
        "default",
        "format",
        "x-stockassistant-unique-field",
        "x-stockassistant-exact-enum-field",
        "x-stockassistant-exact-enum-values",
        "x-stockassistant-evidence-independence",
        "x-stockassistant-normalization",
        "x-stockassistant-no-numeric-expression",
        "x-stockassistant-language-profile",
        "x-stockassistant-no-investment-advice",
    }
    schema = _resolve_local_ref(schema, root_schema)
    if set(schema) - allowed:
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    expected = schema.get("type")
    valid_type = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": _is_number(value),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if not isinstance(expected, str) or expected not in valid_type:
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    normalization_present = "x-stockassistant-normalization" in schema
    numeric_expression_guard = schema.get("x-stockassistant-no-numeric-expression")
    language_profile = schema.get("x-stockassistant-language-profile")
    investment_advice_guard = schema.get("x-stockassistant-no-investment-advice")
    unique_field_present = "x-stockassistant-unique-field" in schema
    exact_field_present = "x-stockassistant-exact-enum-field" in schema
    exact_values_present = "x-stockassistant-exact-enum-values" in schema
    if normalization_present and (
        expected != "string" or schema["x-stockassistant-normalization"] != "NFKC"
    ):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if numeric_expression_guard is not None and (
        expected != "string" or numeric_expression_guard is not True
    ):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if language_profile is not None and (
        expected != "string" or language_profile != "han-dominant-user-prose"
    ):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if investment_advice_guard is not None and (
        expected != "string" or investment_advice_guard is not True
    ):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if unique_field_present and expected != "array":
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if exact_field_present != exact_values_present or (exact_field_present and expected != "array"):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if not valid_type[expected]:
        raise _schema_invalid(path, f"use JSON type {expected}", "JSON_TYPE")
    if "const" in schema and value != schema["const"]:
        raise _schema_invalid(
            path,
            f"use the schema const value {_schema_value_summary(schema['const'])}",
            "CONST_VALUE",
        )
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, Sequence) or isinstance(enum, str) or value not in enum
    ):
        raise _schema_invalid(
            path,
            f"use one of these schema enum values: {_schema_value_summary(enum)}",
            "ENUM_VALUE",
        )
    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise _schema_invalid(
                path, f"use a string with at least {minimum_length} characters", "STRING_MIN_LENGTH"
            )
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            raise _schema_invalid(
                path, f"use a string with at most {maximum_length} characters", "STRING_MAX_LENGTH"
            )
        pattern_value = unicodedata.normalize("NFKC", value) if normalization_present else value
        if isinstance(pattern, str) and re.search(pattern, pattern_value) is None:
            normalization_note = " after NFKC normalization" if normalization_present else ""
            raise _schema_invalid(
                path,
                f"make the string satisfy its schema pattern{normalization_note}",
                "STRING_PATTERN",
            )
        if numeric_expression_guard is True and contains_numeric_expression(value):
            raise _schema_invalid(
                path,
                "remove quantity-bearing numeric expressions from this model-authored prose field; "
                "仅将本字段改为有证据支持的定性理由\uff0c具体数值保留在引用事实中。"
                "不要只把阿拉伯数字改成汉字\uff1a五年、三个月、百分之四、两倍等仍属于数量表达。"
                "保留原有因果、风险及判断方向\uff0c不得编造或改变事实",
                "NUMERIC_EXPRESSION",
            )
        if language_profile == "han-dominant-user-prose" and not is_han_dominant_user_prose(value):
            raise _schema_invalid(
                path,
                "use Simplified Chinese prose and keep Latin abbreviations secondary; "
                "仅改写此处的正文字符串。校验按字段计算字符\uff1a"
                "忽略链接、UUID和已认可的财务缩写后\uff0c必须含汉字\uff0c"
                "汉字数量不得少于英文字母数量。用自然中文改写英文解释\uff0c"
                "将正文中的字段名、来源域名及状态代码改为其中文含义\uff1b"
                "不要修改 JSON 属性名、枚举值或事实引用键。"
                "保留事实、数值、时间、判断方向和不确定性\uff0c"
                "不要新增事实或靠重复中文凑字符\uff1b其余合格字段保持不变",
                "LANGUAGE_PROFILE",
            )
        if investment_advice_guard is True and contains_forbidden_investment_advice(value):
            raise _schema_invalid(
                path,
                "remove investment-advice language from this model-authored prose field",
                "INVESTMENT_ADVICE",
            )
        schema_format = schema.get("format")
        if schema_format == "uuid":
            try:
                UUID(value)
            except ValueError as exc:
                raise _schema_invalid(path, "use a valid UUID string", "UUID_FORMAT") from exc
        elif schema_format not in {None, "date-time"}:
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    if _is_number(value):
        numeric = cast(int | float, value)
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if _is_number(minimum) and numeric < cast(int | float, minimum):
            raise _schema_invalid(
                path, f"use a number greater than or equal to {minimum}", "NUMBER_MINIMUM"
            )
        if _is_number(maximum) and numeric > cast(int | float, maximum):
            raise _schema_invalid(
                path, f"use a number less than or equal to {maximum}", "NUMBER_MAXIMUM"
            )
    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise _schema_invalid(
                path, f"return at least {minimum_items} array items", "ARRAY_MIN_ITEMS"
            )
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise _schema_invalid(
                path, f"return at most {maximum_items} array items", "ARRAY_MAX_ITEMS"
            )
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        children_valid = True
        for index, item in enumerate(value):
            if not _validate_schema_node(item, items, root_schema, repair_errors, (*path, index)):
                children_valid = False
        if unique_field_present or exact_field_present:
            field = schema[
                "x-stockassistant-exact-enum-field"
                if exact_field_present
                else "x-stockassistant-unique-field"
            ]
            exact_values = schema.get("x-stockassistant-exact-enum-values")
            if (
                not isinstance(field, str)
                or not field
                or (
                    unique_field_present
                    and exact_field_present
                    and schema["x-stockassistant-unique-field"] != field
                )
            ):
                raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
            if exact_field_present and (
                not isinstance(exact_values, list)
                or not exact_values
                or not all(isinstance(item, str) for item in exact_values)
                or len(set(exact_values)) != len(exact_values)
            ):
                raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
            selection_item_schema = _resolve_local_ref(items, root_schema)
            item_properties = selection_item_schema.get("properties")
            if selection_item_schema.get("type") != "object" or not isinstance(
                item_properties, Mapping
            ):
                raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
            field_schema = item_properties.get(field)
            if (
                not isinstance(field_schema, Mapping)
                or _resolve_local_ref(field_schema, root_schema).get("type") != "string"
            ):
                raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
            actual_values = [item.get(field) if isinstance(item, dict) else None for item in value]
            rule: _SchemaRule = "EXACT_ENUM" if exact_field_present else "UNIQUE_FIELD"
            if not all(isinstance(item, str) for item in actual_values):
                raise _schema_invalid(
                    path,
                    f"give every array item a string property named {_schema_value_summary(field)}",
                    rule,
                )
            if exact_field_present and isinstance(exact_values, list):
                if (
                    len(set(actual_values)) != len(actual_values)
                    or len(actual_values) != len(exact_values)
                    or set(actual_values) != set(exact_values)
                ):
                    raise _schema_invalid(
                        path,
                        f"include each {_schema_value_summary(field)} exactly once using: "
                        f"{_schema_value_summary(exact_values)}",
                        rule,
                    )
            elif len(set(actual_values)) != len(actual_values):
                raise _schema_invalid(
                    path,
                    f"remove duplicate {_schema_value_summary(field)} values from present items",
                    rule,
                )
        if not children_valid:
            return False
    if isinstance(value, dict):
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        if not all(isinstance(item, str) for item in required):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        if not set(required).issubset(value):
            missing = [item for item in required if item not in value]
            raise _schema_invalid(
                path,
                f"add required properties: {_schema_value_summary(missing)}",
                "REQUIRED_PROPERTIES",
            )
        if schema.get("additionalProperties") is not False:
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        if set(value) - set(properties):
            raise _schema_invalid(
                path, "remove properties not declared in the schema", "ADDITIONAL_PROPERTIES"
            )
        children_valid = True
        for key, item in value.items():
            item_schema = properties.get(key)
            if not isinstance(item_schema, Mapping):
                raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
            if not _validate_schema_node(
                item, item_schema, root_schema, repair_errors, (*path, key)
            ):
                children_valid = False
        if not children_valid:
            return False
        evidence_independence = schema.get("x-stockassistant-evidence-independence")
        if evidence_independence is not None:
            _validate_evidence_independence(
                value,
                properties=properties,
                constraint=evidence_independence,
                root_schema=root_schema,
                path=path,
            )

    return True


def _validate_evidence_independence(
    value: Mapping[str, object],
    *,
    properties: Mapping[str, object],
    constraint: object,
    root_schema: Mapping[str, object],
    path: tuple[str | int, ...],
) -> None:
    """Validate known evidence IDs and two source-independence dimensions."""

    expected_keys = {"supporting_field", "counter_field", "evidence_classes"}
    if not isinstance(constraint, Mapping) or set(constraint) != expected_keys:
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    supporting_field = constraint.get("supporting_field")
    counter_field = constraint.get("counter_field")
    evidence_classes = constraint.get("evidence_classes")
    if (
        not isinstance(supporting_field, str)
        or not isinstance(counter_field, str)
        or supporting_field == counter_field
        or not isinstance(evidence_classes, Mapping)
        or not evidence_classes
    ):
        raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
    for field in (supporting_field, counter_field):
        field_schema = properties.get(field)
        if not isinstance(field_schema, Mapping):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        resolved_field = _resolve_local_ref(field_schema, root_schema)
        item_schema = resolved_field.get("items")
        if (
            resolved_field.get("type") != "array"
            or not isinstance(item_schema, Mapping)
            or _resolve_local_ref(item_schema, root_schema).get("type") != "string"
        ):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")

    class_by_evidence_id: dict[str, tuple[int, int]] = {}
    for evidence_id, raw_classes in evidence_classes.items():
        try:
            valid_evidence_id = (
                isinstance(evidence_id, str) and str(UUID(evidence_id)) == evidence_id
            )
        except ValueError:
            valid_evidence_id = False
        if (
            not valid_evidence_id
            or not isinstance(raw_classes, list)
            or len(raw_classes) != 2
            or not all(
                isinstance(item, int) and not isinstance(item, bool) and item >= 0
                for item in raw_classes
            )
        ):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        class_by_evidence_id[evidence_id] = (raw_classes[0], raw_classes[1])

    selections: list[set[tuple[int, int]]] = []
    for field in (supporting_field, counter_field):
        raw_selection = value.get(field)
        if not isinstance(raw_selection, list) or not all(
            isinstance(item, str) for item in raw_selection
        ):
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED")
        if any(item not in class_by_evidence_id for item in raw_selection):
            raise _schema_invalid(
                path, "use only evidence IDs declared by the schema", "EVIDENCE_SELECTION"
            ) from None
        selections.append({class_by_evidence_id[item] for item in raw_selection})

    supporting, counter = selections
    if {classes[0] for classes in supporting} & {classes[0] for classes in counter} or {
        classes[1] for classes in supporting
    } & {classes[1] for classes in counter}:
        raise _schema_invalid(
            path,
            "choose supporting and counter evidence with no overlapping declared source classes",
            "EVIDENCE_INDEPENDENCE",
        )


def validate_json_schema(payload: dict[str, JsonValue], schema: dict[str, JsonValue]) -> None:
    repair_errors: list[DeepSeekProviderError] = []
    _validate_schema_node(payload, schema, schema, repair_errors)
    if repair_errors:
        raise DeepSeekProviderError(
            "DEEPSEEK_RESPONSE_SCHEMA_INVALID",
            repair_hint="\n".join(
                error.repair_hint or "Re-check the complete schema." for error in repair_errors
            ),
            schema_violations=tuple(
                violation for error in repair_errors for violation in error.schema_violations
            ),
        )


def _validated_payload(
    response: DeepSeekTransportResponse,
    schema: dict[str, JsonValue],
    *,
    task_type: str,
    sanitize_numeric_expressions: bool = False,
) -> tuple[dict[str, JsonValue], int]:
    if response.finish_reason != "stop":
        raise _ValidatedResponseError(
            "DEEPSEEK_RESPONSE_INCOMPLETE",
            retryable=True,
            repair_hint="At the document root, return the complete object before the token limit",
            schema_violations=(_SchemaViolation("", "INCOMPLETE_RESPONSE"),),
        )
    if response.content is None or not response.content.strip():
        raise _ValidatedResponseError(
            "DEEPSEEK_RESPONSE_EMPTY",
            retryable=True,
            repair_hint="At the document root, return one non-empty JSON object",
            schema_violations=(_SchemaViolation("", "EMPTY_RESPONSE"),),
        )
    try:
        decoded = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise _ValidatedResponseError(
            "DEEPSEEK_RESPONSE_JSON_INVALID",
            retryable=True,
            repair_hint="At the document root, return syntactically valid JSON",
            schema_violations=(_SchemaViolation("", "INVALID_JSON"),),
        ) from exc
    if not isinstance(decoded, dict):
        raise _ValidatedResponseError(
            "DEEPSEEK_RESPONSE_JSON_INVALID",
            retryable=True,
            repair_hint="At the document root, use one JSON object as the root",
            schema_violations=(_SchemaViolation("", "JSON_ROOT_TYPE"),),
        )
    payload = cast(dict[str, JsonValue], decoded)
    sanitized_count = 0
    if sanitize_numeric_expressions:
        sanitized, sanitized_count = _sanitize_numeric_expression_fields(payload, schema, schema)
        if not isinstance(sanitized, dict):  # pragma: no cover - root type checked above
            raise DeepSeekProviderError("DEEPSEEK_RESPONSE_SCHEMA_INVALID")
        payload = cast(dict[str, JsonValue], sanitized)
    try:
        validate_json_schema(payload, schema)
    except DeepSeekProviderError as error:
        if error.code in _REPAIRABLE_RESPONSE_CODES:
            raise _ValidatedResponseError(
                error.code,
                retryable=True,
                repair_hint=error.repair_hint,
                numeric_sanitized_fields=sanitized_count,
                schema_violations=error.schema_violations,
            ) from error
        raise
    if task_type in {"research_score_assessment", "research_support_assessment"}:
        try:
            (
                (
                    ResearchSupportLeafAssessment
                    if payload.get("schema_version") == "4.0"
                    else ResearchSupportAssessment
                )
                if task_type == "research_support_assessment"
                else ResearchScoreCandidate
                if payload.get("schema_version") == "2.0"
                else ResearchScoreAssessmentDraft
            ).model_validate(payload)
        except ValidationError as error:
            # Pydantic's existing model validator expresses selection consistency
            # beyond JSON Schema. Keep it inside the same bounded repair attempt.
            selection_errors = [
                _schema_invalid(
                    tuple(
                        part if isinstance(part, int) or part in _DIAGNOSTIC_FIELDS else "*"
                        for part in item["loc"][:12]
                    ),
                    "return consistent supported and reason_code for every submitted item"
                    if task_type == "research_support_assessment"
                    else "provide supporting facts within this dimension unless grade is abstain",
                    "SUPPORT_VERDICT_CONFLICT"
                    if task_type == "research_support_assessment"
                    else "SCORE_SUPPORT_REQUIRED",
                )
                for item in error.errors(
                    include_input=False, include_context=False, include_url=False
                )
            ]
            raise _ValidatedResponseError(
                "DEEPSEEK_RESPONSE_SCHEMA_INVALID",
                retryable=True,
                repair_hint="\n".join(item.repair_hint or "" for item in selection_errors),
                numeric_sanitized_fields=sanitized_count,
                schema_violations=tuple(
                    violation for item in selection_errors for violation in item.schema_violations
                ),
            ) from None
    return payload, sanitized_count


class DeepSeekLLMProvider:
    """Fixed-model DeepSeek implementation of ``LLMProviderPort``."""

    def __init__(
        self,
        *,
        model: DeepSeekModel,
        transport: DeepSeekTransport,
        logger: logging.Logger | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
        permit: Callable[[float], AbstractAsyncContextManager[None]] | None = None,
    ) -> None:
        if model not in {DEEPSEEK_FLASH_MODEL, DEEPSEEK_PRO_MODEL}:
            raise DeepSeekProviderError("DEEPSEEK_MODEL_FORBIDDEN")
        self._model = model
        self._transport = transport
        self._logger = logger or logging.getLogger(__name__)
        self._sleeper = sleeper
        self._clock = clock
        self._permit = permit

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def capability(self) -> ModelProviderCapability:
        return ModelProviderCapability(
            schema_version="1.0",
            provider_name=self.provider_name,
            model=self._model,
            adapter_version=DEEPSEEK_ADAPTER_VERSION,
            operations=[ModelOperation.GENERATION],
            supported_privacy_levels=[PrivacyLevel.PUBLIC, PrivacyLevel.REDACTED_EXTERNAL],
            external_processing=True,
            structured_output=True,
            maximum_output_tokens=4096 if self._model == DEEPSEEK_FLASH_MODEL else 8192,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if request.privacy_level not in {
            PrivacyLevel.PUBLIC,
            PrivacyLevel.REDACTED_EXTERNAL,
        }:
            raise DeepSeekProviderError("DEEPSEEK_PRIVACY_LEVEL_FORBIDDEN")
        is_pro = self._model == DEEPSEEK_PRO_MODEL
        base_messages = (
            ("system", _schema_instruction(request.response_schema)),
            *((item.role.value, item.content) for item in request.messages),
        )
        direct_structured_task = request.task_type in _DIRECT_STRUCTURED_TASKS
        started = self._clock()
        profile_cap_seconds = (
            180.0
            if request.task_type == "research_report_narrative"
            else (90.0 if direct_structured_task else (120.0 if is_pro else 60.0))
        )
        budget_seconds = min(request.timeout_seconds, profile_cap_seconds)
        if request.context.deadline_at is not None:
            budget_seconds = min(
                budget_seconds,
                (request.context.deadline_at - datetime.now(UTC)).total_seconds(),
            )
        deadline = started + budget_seconds
        response: DeepSeekTransportResponse | None = None
        payload: dict[str, JsonValue] | None = None
        repair_code: str | None = None
        repair_hint: str | None = None
        repair_candidate: str | None = None
        numeric_sanitized_fields = 0
        provider_calls = 0
        transport_retries = 0
        schema_repairs = 0
        validation_attempts = 0
        input_tokens = 0
        output_tokens = 0
        previous_validation: _ValidatedResponseError | None = None

        def log_event(
            code: str,
            failure_kind: LLMProviderFailureKind | str,
            phase: Literal["transport", "validation", "budget", "cancelled", "completed"],
            numeric_count: int = 0,
        ) -> None:
            self._safe_log(
                request,
                provider_call=provider_calls,
                transport_retry=transport_retries,
                schema_repair=schema_repairs,
                failure_kind=failure_kind,
                code=code,
                numeric_sanitized_fields=numeric_count,
                phase=phase,
                validation_attempt=validation_attempts,
                previous_validation=previous_validation,
                remaining_seconds=deadline - self._clock(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        try:
            while provider_calls < DEEPSEEK_MAX_PROVIDER_CALLS:
                remaining_seconds = deadline - self._clock()
                if remaining_seconds <= 0:
                    log_event("DEEPSEEK_TIMEOUT", LLMProviderFailureKind.TRANSPORT, "budget")
                    raise DeepSeekProviderError(
                        "DEEPSEEK_TIMEOUT",
                        retryable=True,
                        previous_validation_code=previous_validation.code
                        if previous_validation
                        else None,
                        schema_violations=previous_validation.schema_violations
                        if previous_validation
                        else (),
                    )
                provider_calls += 1
                repair_context = (
                    (("assistant", repair_candidate),) if repair_candidate is not None else ()
                )
                messages = (
                    base_messages
                    if repair_code is None
                    else (
                        *base_messages,
                        *repair_context,
                        (
                            "user",
                            _repair_instruction(
                                repair_code,
                                repair_hint=repair_hint,
                                repair_round=schema_repairs,
                                direct_structured_task=direct_structured_task,
                                prior_candidate_included=repair_candidate is not None,
                            ),
                        ),
                    )
                )
                transport_request = DeepSeekTransportRequest(
                    model=self._model,
                    messages=messages,
                    maximum_output_tokens=8192 if is_pro else 4096,
                    timeout_seconds=_attempt_timeout_seconds(
                        remaining_seconds=remaining_seconds,
                        profile_cap_seconds=90.0 if direct_structured_task else profile_cap_seconds,
                    ),
                    thinking_enabled=is_pro and request.task_type not in _DIRECT_STRUCTURED_TASKS,
                )
                try:
                    async with asyncio.timeout(remaining_seconds):
                        async with (
                            self._permit(remaining_seconds)
                            if self._permit is not None
                            else nullcontext()
                        ):
                            remaining_after_queue = deadline - self._clock()
                            if remaining_after_queue <= 0:
                                raise TimeoutError("provider queue deadline exceeded")
                            transport_request = dataclass_replace(
                                transport_request,
                                timeout_seconds=min(
                                    transport_request.timeout_seconds, remaining_after_queue
                                ),
                            )
                            response = await self._transport.complete(transport_request)
                            input_tokens += response.input_tokens
                            output_tokens += response.output_tokens
                except TimeoutError as exc:
                    error = DeepSeekProviderError(
                        "DEEPSEEK_TIMEOUT",
                        retryable=True,
                        failure_kind=LLMProviderFailureKind.TRANSPORT,
                        previous_validation_code=previous_validation.code
                        if previous_validation
                        else None,
                        schema_violations=previous_validation.schema_violations
                        if previous_validation
                        else (),
                    )
                    log_event(error.code, error.failure_kind, "transport")
                    if (
                        transport_retries >= DEEPSEEK_MAX_TRANSPORT_RETRIES
                        or provider_calls >= DEEPSEEK_MAX_PROVIDER_CALLS
                    ):
                        raise error from exc
                    delay_seconds = _transport_retry_delay_seconds(request, transport_retries)
                    if deadline - self._clock() <= delay_seconds:
                        raise error from exc
                    transport_retries += 1
                    await self._sleeper(delay_seconds)
                    continue
                except DeepSeekProviderError as error:
                    if previous_validation is not None:
                        error.previous_validation_code = previous_validation.code
                        error.schema_violations = previous_validation.schema_violations
                    log_event(error.code, error.failure_kind, "transport")
                    if (
                        error.failure_kind is not LLMProviderFailureKind.TRANSPORT
                        or not error.retryable
                        or transport_retries >= DEEPSEEK_MAX_TRANSPORT_RETRIES
                        or provider_calls >= DEEPSEEK_MAX_PROVIDER_CALLS
                    ):
                        raise
                    delay_seconds = max(
                        _transport_retry_delay_seconds(request, transport_retries),
                        error.retry_after_seconds or 0.0,
                    )
                    if deadline - self._clock() <= delay_seconds:
                        raise
                    transport_retries += 1
                    await self._sleeper(delay_seconds)
                    continue
                try:
                    validation_attempts += 1
                    payload, numeric_sanitized_fields = _validated_payload(
                        response,
                        request.response_schema,
                        task_type=request.task_type,
                        # Repair required research content before returning it to consumers
                        # that reject sanitized/degraded narrative or scoring output.
                        sanitize_numeric_expressions=(
                            direct_structured_task
                            and request.task_type
                            not in {"research_report_narrative", "research_score_assessment"}
                        ),
                    )
                    break
                except _ValidatedResponseError as error:
                    previous_validation = error
                    log_event(
                        error.code, error.failure_kind, "validation", error.numeric_sanitized_fields
                    )
                    if (
                        not error.retryable
                        or schema_repairs >= DEEPSEEK_MAX_SCHEMA_REPAIRS
                        or provider_calls >= DEEPSEEK_MAX_PROVIDER_CALLS
                    ):
                        raise
                    schema_repairs += 1
                    repair_code = error.code
                    repair_hint = error.repair_hint
                    repair_candidate = (
                        response.content
                        if (
                            (
                                direct_structured_task
                                or request.task_type == "research_web_evidence_normalization"
                            )
                            and response is not None
                            and response.content is not None
                            and response.content.strip()
                            and len(response.content.encode("utf-8"))
                            <= DEEPSEEK_REPAIR_CONTEXT_MAX_BYTES
                        )
                        else None
                    )
                except DeepSeekProviderError as error:
                    log_event(error.code, error.failure_kind, "validation")
                    raise
        except asyncio.CancelledError:
            # The caller owns cancellation vs. outer-deadline classification. Preserve both
            # the cancellation fact and any earlier validation cause without retrying.
            log_event("DEEPSEEK_GENERATION_CANCELLED", "cancelled", "cancelled")
            raise
        if response is None or payload is None:  # pragma: no cover
            raise DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE")
        log_event("DEEPSEEK_GENERATION_SUCCEEDED", "none", "completed", numeric_sanitized_fields)
        return GenerationResponse(
            schema_version="1.0",
            payload=payload,
            payload_namespace=request.response_schema_namespace,
            payload_owner=request.response_schema_owner,
            response_schema_hash=request.response_schema_hash,
            provider=self.provider_name,
            model=self._model,
            adapter_version=DEEPSEEK_ADAPTER_VERSION,
            prompt_hash=_prompt_hash(request),
            raw_response_hash=response.raw_response_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=max(0, round((self._clock() - started) * 1000)),
            finish_reason="stop",
            schema_validated=True,
            degraded=numeric_sanitized_fields > 0,
        )

    def _safe_log(
        self,
        request: GenerationRequest,
        *,
        provider_call: int,
        transport_retry: int,
        schema_repair: int,
        failure_kind: LLMProviderFailureKind | str,
        code: str,
        numeric_sanitized_fields: int,
        phase: str,
        validation_attempt: int,
        previous_validation: _ValidatedResponseError | None,
        remaining_seconds: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        correlation_id = str(request.context.correlation_id)
        violations = (
            [
                {"path": violation.path, "rule": violation.rule}
                for violation in previous_validation.schema_violations
            ]
            if previous_validation is not None
            else []
        )
        previous_code = previous_validation.code if previous_validation is not None else "none"
        validation_stage = (
            "none"
            if validation_attempt == 0
            else ("initial" if validation_attempt == 1 else "repair")
        )
        remaining_budget_ms = max(0, round(remaining_seconds * 1000))
        self._logger.info(
            "deepseek_generation task_type=%s correlation_id=%s provider_call=%d "
            "transport_retry=%d schema_repair=%d failure_kind=%s code=%s "
            "numeric_sanitized_fields=%d phase=%s validation_stage=%s validation_attempt=%d "
            "remaining_budget_ms=%d previous_validation_code=%s "
            "input_tokens=%d output_tokens=%d schema_violations=%s",
            request.task_type,
            correlation_id,
            provider_call,
            transport_retry,
            schema_repair,
            str(failure_kind),
            code,
            numeric_sanitized_fields,
            phase,
            validation_stage,
            validation_attempt,
            remaining_budget_ms,
            previous_code,
            input_tokens,
            output_tokens,
            json.dumps(violations, separators=(",", ":")),
            extra={
                "event": "deepseek_generation",
                "provider": self.provider_name,
                "model": self._model,
                "task_type": request.task_type,
                "provider_call": provider_call,
                "transport_retry": transport_retry,
                "schema_repair": schema_repair,
                "failure_kind": str(failure_kind),
                "correlation_id": correlation_id,
                "error_code": code,
                "numeric_sanitized_fields": numeric_sanitized_fields,
                "phase": phase,
                "validation_stage": validation_stage,
                "validation_attempt": validation_attempt,
                "remaining_budget_ms": remaining_budget_ms,
                "previous_validation_code": previous_code,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "schema_violations": violations,
            },
        )


def build_deepseek_provider(
    *,
    api_key: SecretStr,
    model: DeepSeekModel,
    base_url: str = DEEPSEEK_BASE_URL,
    permit: Callable[[float], AbstractAsyncContextManager[None]] | None = None,
) -> DeepSeekLLMProvider:
    return DeepSeekLLMProvider(
        model=model,
        transport=OpenAICompatibleDeepSeekTransport(api_key=api_key, base_url=base_url),
        permit=permit,
    )


__all__ = [
    "DEEPSEEK_ADAPTER_VERSION",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_FLASH_MODEL",
    "DEEPSEEK_PRO_MODEL",
    "DeepSeekLLMProvider",
    "DeepSeekProviderError",
    "DeepSeekTransport",
    "DeepSeekTransportRequest",
    "DeepSeekTransportResponse",
    "OpenAICompatibleDeepSeekTransport",
    "build_deepseek_provider",
    "validate_json_schema",
]
