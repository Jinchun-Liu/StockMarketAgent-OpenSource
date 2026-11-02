from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import httpx
import pytest
from openai import RateLimitError
from pydantic import JsonValue, SecretStr, TypeAdapter, ValidationError

from stock_assistant.config.settings import Settings
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.discovery import ResearchWebNormalizationDraft
from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    MessageRole,
    PrivacyLevel,
    PromptMessage,
)
from stock_assistant.contracts.research import (
    ResearchNarrativeCandidate,
    ResearchNarrativeDraft,
    ResearchSectionKind,
)
from stock_assistant.contracts.research_reader import (
    ModelAuthoredProse,
    ModelAuthoredScoreReason,
    ModelAuthoredTerm,
    ResearchReaderDraft,
    ResearchScoreAssessmentDraft,
    ResearchScoreDimensionId,
    contains_numeric_expression,
)
from stock_assistant.modules.research import reporting as reporting_module
from stock_assistant.platform.llm import deepseek as deepseek_module
from stock_assistant.platform.llm.deepseek import (
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_PRO_MODEL,
    DeepSeekLLMProvider,
    DeepSeekProviderError,
    DeepSeekTransportRequest,
    DeepSeekTransportResponse,
    OpenAICompatibleDeepSeekTransport,
    validate_json_schema,
)
from stock_assistant.platform.llm.synthetic_research import SyntheticResearchLLMProvider
from stock_assistant.platform.research.synthetic_evidence import synthetic_evidence_packet

NOW = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "points": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 2,
        },
    },
    "required": ["title", "points"],
    "additionalProperties": False,
}
SCORE_DIMENSION_IDS = tuple(dimension.value for dimension in ResearchScoreDimensionId)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _request(
    *,
    privacy: PrivacyLevel = PrivacyLevel.PUBLIC,
    timeout: float = 120,
    task_type: str = "r1_public_research_interpretation",
    response_schema: dict[str, JsonValue] | None = None,
) -> GenerationRequest:
    selected_schema = SCHEMA if response_schema is None else response_schema
    return GenerationRequest(
        schema_version="1.0",
        task_type=task_type,
        messages=[PromptMessage(role=MessageRole.USER, content="只解释公开指数")],
        response_schema=selected_schema,
        response_schema_hash=_hash(selected_schema),
        response_schema_namespace="r1.public_interpretation",
        response_schema_owner="qir",
        privacy_level=privacy,
        context=OperationContext(
            schema_version="1.0",
            request_id=UUID("9712aec6-ce75-55be-ae18-13ee648f85dd"),
            correlation_id=UUID("127d9d65-8356-5368-b22a-b85dabf12ea8"),
            as_of=NOW,
            requested_at=NOW,
        ),
        timeout_seconds=timeout,
    )


class _FakeTransport:
    def __init__(self, responses: list[DeepSeekTransportResponse | DeepSeekProviderError]) -> None:
        self.responses = responses
        self.requests: list[DeepSeekTransportRequest] = []

    async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, DeepSeekProviderError):
            raise response
        return response


def _raw_response(
    content: str | None, *, finish_reason: str | None = "stop"
) -> DeepSeekTransportResponse:
    return DeepSeekTransportResponse(
        content=content,
        finish_reason=finish_reason,
        input_tokens=12,
        output_tokens=8,
        raw_response_hash="a" * 64,
    )


def _response(payload: object) -> DeepSeekTransportResponse:
    return _raw_response(json.dumps(payload, ensure_ascii=False))


def _model_authored_prose_object_schema() -> dict[str, JsonValue]:
    prose_schema = TypeAdapter(ModelAuthoredProse).json_schema(mode="validation")
    return {
        "type": "object",
        "properties": {"text": prose_schema},
        "required": ["text"],
        "additionalProperties": False,
    }


def _narrative_payload(
    *,
    numeric_prose: bool,
    first_hypothesis_section: str = "bull_case",
    section_title: str | None = None,
) -> dict[str, object]:
    section_ids = [item.value for item in ResearchSectionKind]
    title = section_title or ("近五年研究主题" if numeric_prose else "近期研究主题")
    summary = "二十季度证据摘要。" if numeric_prose else "多期证据摘要。"
    return {
        "schema_version": "1.0",
        "sections": [
            {"kind": section, "title": title, "summary": summary} for section in section_ids
        ],
        "hypotheses": [
            {
                "section": first_hypothesis_section,
                "statement": "积极情景仍需后续证据支持。",
                "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
                "counter_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
                "falsification_conditions": ["核心业务持续恶化。"],
                "uncertainty": "现有资料覆盖有限。",
            },
            {
                "section": "bear_case",
                "statement": "风险情景仍需后续证据核验。",
                "supporting_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
                "counter_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
                "falsification_conditions": ["关键风险持续缓解。"],
                "uncertainty": "现有资料覆盖有限。",
            },
        ],
    }


def _score_assessment_payload(
    *,
    reason: str = "证据与反证大致平衡。",
    included_dimensions: tuple[str, ...] = SCORE_DIMENSION_IDS,
    evidence_ids_by_dimension: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    default_evidence_id = "9712aec6-ce75-55be-ae18-13ee648f85dd"
    return {
        "schema_version": "1.0",
        **{
            dimension: {
                "grade": "balanced",
                "reasons": [reason],
                "evidence_ids": (
                    evidence_ids_by_dimension[dimension]
                    if evidence_ids_by_dimension is not None
                    else [default_evidence_id]
                ),
                "counter_evidence_ids": [],
            }
            for dimension in included_dimensions
        },
    }


async def _grounded_narrative_request(
    *, source_disclosure_absence: bool = True
) -> tuple[GenerationRequest, ResearchNarrativeCandidate]:
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986", instrument_name="兆易创新", as_of=NOW
    )
    if not source_disclosure_absence:
        packet = packet.model_copy(
            update={
                "facts": [
                    fact.model_copy(
                        update={"statement": fact.statement.replace("期间逐年收入数据未提供。", "")}
                    )
                    for fact in packet.facts
                ]
            }
        )
    schema, schema_hash = reporting_module._narrative_schema(packet)
    request = _request(
        task_type="research_report_narrative", timeout=90, response_schema=schema
    ).model_copy(
        update={
            "response_schema_hash": schema_hash,
            "response_schema_namespace": "research.narrative_draft",
            "messages": [
                PromptMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        {"facts": [fact.model_dump(mode="json") for fact in packet.facts]},
                        ensure_ascii=False,
                    ),
                )
            ],
        }
    )
    response = await SyntheticResearchLLMProvider().generate(request)
    draft = ResearchNarrativeCandidate.model_validate(response.payload)
    draft.sections[0].title = "近五年研究主题"
    draft.hypotheses[0].supporting_fact_keys.append("financial.revenue")
    validate_json_schema(draft.model_dump(mode="json"), schema)
    return request, draft


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_succeeds", [True, False])
async def test_narrative_language_repair_preserves_grounding_and_stays_bounded(
    repair_succeeds: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request, valid = await _grounded_narrative_request()
    section_index = len(valid.sections) - 1
    valid.sections[section_index].summary = "综合判断仍须逐项核验公开材料的适用期间。"
    invalid = valid.model_dump(mode="json")
    sections = cast(list[dict[str, JsonValue]], invalid["sections"])
    # Analytical prose still requires the same bounded Chinese-language repair.
    invalid_summary = "综合 public disclosure metadata requires period verification"
    sections[section_index]["summary"] = invalid_summary
    with pytest.raises(ValidationError):
        ResearchNarrativeCandidate.model_validate(invalid)
    transport = _FakeTransport(
        [
            _response(invalid),
            _response(valid.model_dump(mode="json") if repair_succeeds else invalid),
        ]
    )
    provider = DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport)
    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.llm.deepseek"):
        if repair_succeeds:
            result = await provider.generate(request)
            assert ResearchNarrativeCandidate.model_validate(result.payload) == valid
            assert result.degraded is False
        else:
            with pytest.raises(DeepSeekProviderError) as exc_info:
                await provider.generate(request)
            assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    first, repair = transport.requests
    assert repair.messages[: len(first.messages)] == first.messages
    assert repair.messages[-2] == ("assistant", json.dumps(invalid, ensure_ascii=False))
    hint = repair.messages[-1][1]
    assert f"At JSON pointer /sections/{section_index}/summary" in hint
    assert "汉字数量不得少于英文字母数量" in hint
    assert "用自然中文改写英文解释" in hint
    assert "不要修改 JSON 属性名、枚举值或事实引用键" in hint
    assert "保留事实、数值、时间、判断方向和不确定性" in hint
    assert invalid_summary not in hint
    records = [r for r in caplog.records if r.__dict__.get("event") == "deepseek_generation"]
    assert records[0].__dict__["schema_violations"] == [
        {"path": f"/sections/{section_index}/summary", "rule": "LANGUAGE_PROFILE"}
    ]
    assert records[-1].__dict__["schema_repair"] == 1
    assert invalid_summary not in "\n".join(r.getMessage() for r in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["summary", "statement", "uncertainty", "falsification_conditions"]
)
@pytest.mark.parametrize("scoped", [True, False])
async def test_narrative_disclosure_scope_reaches_existing_content_audit(
    field: str, scoped: bool, caplog: pytest.LogCaptureFixture
) -> None:
    request, candidate = await _grounded_narrative_request(source_disclosure_absence=False)
    prose = "本次证据未提供财务趋势数据。" if scoped else "公司未提供财务趋势数据。"
    if field == "summary":
        candidate.sections[-1].summary = prose
        item_id, field_path = f"section:{len(candidate.sections) - 1}", "summary"
    elif field == "falsification_conditions":
        candidate.hypotheses[0].falsification_conditions = [prose]
        item_id, field_path = "hypothesis:0", "falsification_conditions[0]"
    else:
        setattr(candidate.hypotheses[0], field, prose)
        item_id, field_path = "hypothesis:0", field
    transport = _FakeTransport([_response(candidate.model_dump(mode="json"))])
    provider = DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport)
    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.llm.deepseek"):
        result = await provider.generate(request)
    typed = ResearchNarrativeCandidate.model_validate(result.payload)
    assert typed == candidate
    assert result.degraded is False
    assert len(transport.requests) == 1
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986", instrument_name="兆易创新", as_of=NOW
    )
    rejected = reporting_module._scope_rejected_fields(
        packet, reporting_module._bind_narrative(packet, typed), None
    )
    assert (field_path not in rejected.get(item_id, {})) is scoped
    records = [r for r in caplog.records if r.__dict__.get("event") == "deepseek_generation"]
    assert prose not in "\n".join(r.getMessage() for r in records)


def _reader_payload(*, takeaway: str = "公司业务仍需结合公开披露核验。") -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "one_sentence_takeaway": takeaway,
        "how_the_company_makes_money": [
            "核心产品或服务: 公司通过主营业务向客户提供产品。",
            "客户到收入链: 客户采购产品后形成营业收入。",
            "产品结构调整: 当前证据尚未说明具体调整方向。",
        ],
        "why_this_score": [
            "总体结论: 现有证据同时呈现业务基础与风险约束。",
            "最强支撑: 主营业务证据为当前判断提供支持。",
            "主要拖累或未知: 部分经营情况仍待后续核验。",
        ],
        "main_strengths": ["业务结构表现出部分韧性。"],
        "main_risks": ["数据覆盖不能替代公司完整披露。"],
        "unknowns": ["部分经营情况仍待后续核验。"],
        "glossary": [
            {
                "term": "证据可信度",
                "explanation": "衡量来源覆盖和可追溯性, 不代表公司质量。",
            }
        ],
    }


def test_capability_is_a_valid_versioned_contract() -> None:
    provider = DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=_FakeTransport([]),
    )

    capability = provider.capability

    assert capability.schema_version == "1.0"
    assert capability.model == DEEPSEEK_PRO_MODEL
    assert capability.external_processing is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "maximum", "timeout_cap", "thinking"),
    [
        (DEEPSEEK_FLASH_MODEL, 4096, 60.0, False),
        (DEEPSEEK_PRO_MODEL, 8192, 120.0, True),
    ],
)
async def test_fixed_profiles_validate_json_and_add_json_instruction(
    model: str, maximum: int, timeout_cap: float, thinking: bool
) -> None:
    transport = _FakeTransport([_response({"title": "简要说明", "points": ["仅供研究"]})])
    provider = DeepSeekLLMProvider(model=model, transport=transport)  # type: ignore[arg-type]

    result = await provider.generate(_request())

    assert result.schema_validated is True
    assert result.model == model
    assert result.payload == {"title": "简要说明", "points": ["仅供研究"]}
    sent = transport.requests[0]
    assert sent.maximum_output_tokens == maximum
    assert sent.timeout_seconds == pytest.approx(timeout_cap, abs=0.05)
    assert sent.thinking_enabled is thinking
    assert sent.messages[0] == (
        "system",
        "Return one JSON object only. The object must exactly match this JSON Schema, "
        "including every required property and no additional properties: "
        f"{json.dumps(SCHEMA, ensure_ascii=False, separators=(',', ':'), sort_keys=True)}",
    )
    assert sent.messages[1] == ("user", "只解释公开指数")


@pytest.mark.asyncio
async def test_retries_one_retryable_failure_only() -> None:
    transport = _FakeTransport(
        [
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            _response({"title": "恢复", "points": []}),
        ]
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_FLASH_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request())

    assert result.payload["title"] == "恢复"
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_two_transport_retries_then_one_schema_repair_share_four_call_budget() -> None:
    transport = _FakeTransport(
        [
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            _response({"title": "缺字段"}),
            _response({"title": "修复完成", "points": ["仅供研究"]}),
        ]
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request(task_type="research_report_narrative", timeout=90))

    assert result.payload == {"title": "修复完成", "points": ["仅供研究"]}
    assert len(transport.requests) == 4
    assert "DEEPSEEK_RESPONSE_SCHEMA_INVALID" in transport.requests[-1].messages[-1][1]


def _evidence_independence_schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": {
            "supporting_evidence_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "minItems": 1,
                "maxItems": 2,
            },
            "counter_evidence_ids": {
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "minItems": 1,
                "maxItems": 2,
            },
        },
        "required": ["supporting_evidence_ids", "counter_evidence_ids"],
        "additionalProperties": False,
        "x-stockassistant-evidence-independence": {
            "supporting_field": "supporting_evidence_ids",
            "counter_field": "counter_evidence_ids",
            "evidence_classes": {
                "9712aec6-ce75-55be-ae18-13ee648f85dd": [0, 0],
                "127d9d65-8356-5368-b22a-b85dabf12ea8": [0, 1],
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": [1, 2],
            },
        },
    }


def _full_narrative_schema_with_independence() -> dict[str, JsonValue]:
    schema = cast(
        dict[str, JsonValue],
        ResearchNarrativeDraft.model_json_schema(mode="validation"),
    )
    properties = cast(dict[str, JsonValue], schema["properties"])
    hypotheses = cast(dict[str, JsonValue], properties["hypotheses"])
    hypotheses.update(
        {
            "minItems": 2,
            "maxItems": 2,
            "x-stockassistant-exact-enum-field": "section",
            "x-stockassistant-exact-enum-values": ["bull_case", "bear_case"],
        }
    )
    definitions = cast(dict[str, JsonValue], schema["$defs"])
    hypothesis = cast(dict[str, JsonValue], definitions["ResearchNarrativeHypothesis"])
    hypothesis_properties = cast(dict[str, JsonValue], hypothesis["properties"])
    for field in ("supporting_evidence_ids", "counter_evidence_ids"):
        evidence_array = cast(dict[str, JsonValue], hypothesis_properties[field])
        evidence_array["maxItems"] = 1
    hypothesis["x-stockassistant-evidence-independence"] = {
        "supporting_field": "supporting_evidence_ids",
        "counter_field": "counter_evidence_ids",
        "evidence_classes": {
            "9712aec6-ce75-55be-ae18-13ee648f85dd": [0, 0],
            "127d9d65-8356-5368-b22a-b85dabf12ea8": [1, 1],
        },
    }
    return schema


@pytest.mark.asyncio
async def test_evidence_independence_uses_existing_schema_repair_budget() -> None:
    overlapping = {
        "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
        "counter_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
    }
    repaired = {
        "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
        "counter_evidence_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
    }
    transport = _FakeTransport([_response(overlapping), _response(repaired)])

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(
        _request(
            task_type="research_report_narrative",
            timeout=90,
            response_schema=_evidence_independence_schema(),
        )
    )

    assert result.payload == repaired
    assert len(transport.requests) == 2
    repair_instruction = transport.requests[1].messages[-1][1]
    assert "DEEPSEEK_RESPONSE_SCHEMA_INVALID" in repair_instruction
    assert "declared source classes" in repair_instruction
    assert "9712aec6-ce75-55be-ae18-13ee648f85dd" not in repair_instruction


@pytest.mark.asyncio
async def test_evidence_independence_exhausts_the_only_schema_repair() -> None:
    overlapping = {
        "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
        "counter_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
    }
    transport = _FakeTransport(
        [_response(overlapping), _response(overlapping), _response(overlapping)]
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(
            _request(
                task_type="research_report_narrative",
                timeout=90,
                response_schema=_evidence_independence_schema(),
            )
        )

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duplicated_field",
    ["supporting_evidence_ids", "counter_evidence_ids"],
)
async def test_evidence_independence_repairs_duplicate_ids_within_each_side(
    duplicated_field: str,
) -> None:
    repaired = _narrative_payload(numeric_prose=False)
    duplicated = json.loads(json.dumps(repaired))
    hypotheses = duplicated["hypotheses"]
    assert isinstance(hypotheses, list)
    first_hypothesis = hypotheses[0]
    assert isinstance(first_hypothesis, dict)
    original_ids = first_hypothesis[duplicated_field]
    assert isinstance(original_ids, list) and len(original_ids) == 1
    duplicated_id = original_ids[0]
    first_hypothesis[duplicated_field] = [duplicated_id, duplicated_id]
    transport = _FakeTransport([_response(duplicated), _response(repaired)])

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(
        _request(
            task_type="research_report_narrative",
            timeout=90,
            response_schema=_full_narrative_schema_with_independence(),
        )
    )

    assert result.payload == repaired
    assert len(transport.requests) == 2
    repair_message = transport.requests[1].messages[-1][1]
    assert "DEEPSEEK_RESPONSE_SCHEMA_INVALID" in repair_message
    assert "return at most 1 array items" in repair_message
    assert duplicated_id not in repair_message


@pytest.mark.asyncio
async def test_unknown_evidence_id_never_leaks_through_exception_chain() -> None:
    unknown_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    unknown = {
        "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
        "counter_evidence_ids": [unknown_id],
    }
    transport = _FakeTransport([_response(unknown), _response(unknown)])

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(
            _request(
                task_type="research_report_narrative",
                timeout=90,
                response_schema=_evidence_independence_schema(),
            )
        )

    rendered = "".join(traceback.format_exception(exc_info.value))
    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert unknown_id not in rendered


@pytest.mark.asyncio
async def test_third_transport_failure_exhausts_transport_retry_budget() -> None:
    transport = _FakeTransport(
        [
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            _response({"title": "不应到达", "points": []}),
        ]
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(_request(task_type="research_report_narrative", timeout=90))

    assert exc_info.value.code == "DEEPSEEK_TEMPORARILY_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert len(transport.requests) == 3


@pytest.mark.asyncio
async def test_retries_one_schema_invalid_response_with_safe_field_repair_prompt() -> None:
    invalid_marker = "upstream-raw-content-must-not-be-replayed"
    transport = _FakeTransport(
        [
            _response({"title": "格式错误", "points": invalid_marker}),
            _response({"title": "修复完成", "points": ["仅供研究"]}),
        ]
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request())

    assert result.payload == {"title": "修复完成", "points": ["仅供研究"]}
    assert len(transport.requests) == 2
    repair_message = transport.requests[1].messages[-1]
    assert repair_message[0] == "user"
    assert "DEEPSEEK_RESPONSE_SCHEMA_INVALID" in repair_message[1]
    assert "At JSON pointer /points, use JSON type array" in repair_message[1]
    assert invalid_marker not in repair_message[1]
    assert all(role != "assistant" for role, _content in transport.requests[1].messages)


@pytest.mark.asyncio
async def test_structured_repair_edits_previous_candidate_instead_of_regenerating() -> None:
    invalid_payload = {"title": "格式错误", "points": "应为数组"}

    class CandidateAwareTransport:
        def __init__(self) -> None:
            self.requests: list[DeepSeekTransportRequest] = []

        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            self.requests.append(request)
            if len(request.messages) >= 2 and request.messages[-2] == (
                "assistant",
                json.dumps(invalid_payload, ensure_ascii=False),
            ):
                return _response({"title": "修复完成", "points": ["仅供研究"]})
            return _response(invalid_payload)

    transport = CandidateAwareTransport()
    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request(task_type="research_report_narrative", timeout=90))

    assert result.payload == {"title": "修复完成", "points": ["仅供研究"]}
    assert len(transport.requests) == 2
    assert (
        "Revise the immediately preceding assistant JSON" in transport.requests[1].messages[-1][1]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_bytes", "candidate_is_replayed"),
    [(47_999, True), (48_000, True), (48_001, False)],
)
@pytest.mark.parametrize(
    "task_type", ["research_report_narrative", "research_web_evidence_normalization"]
)
async def test_structured_repair_candidate_uses_exact_byte_boundary(
    candidate_bytes: int,
    candidate_is_replayed: bool,
    task_type: str,
) -> None:
    prefix = '{"title":"invalid","points":"'
    suffix = '"}'
    padding_bytes = candidate_bytes - len(prefix.encode()) - len(suffix.encode())
    invalid_content = f"{prefix}{'x' * padding_bytes}{suffix}"
    assert len(invalid_content.encode()) == candidate_bytes
    transport = _FakeTransport(
        [
            _raw_response(invalid_content),
            _response({"title": "修复完成", "points": ["仅供研究"]}),
        ]
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request(task_type=task_type, timeout=90))

    assert result.payload == {"title": "修复完成", "points": ["仅供研究"]}
    replayed_candidates = [
        content for role, content in transport.requests[1].messages if role == "assistant"
    ]
    assert replayed_candidates == ([invalid_content] if candidate_is_replayed else [])
    assert (
        "immediately preceding assistant JSON" in transport.requests[1].messages[-1][1]
    ) is candidate_is_replayed


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [True, False])
@pytest.mark.parametrize("field,limit", [("source_excerpt", 2000), ("value", 200)])
async def test_normalization_length_failure_repairs_original_without_truncation_or_body_logs(
    repaired: bool, field: str, limit: int, caplog: pytest.LogCaptureFixture
) -> None:
    schema = ResearchWebNormalizationDraft.model_json_schema(mode="validation")
    valid_fact = {"missing_fact_key": "valuation.context", "source_excerpt": "原文估值说明"}
    invalid_fact = {**valid_fact, field: "界" * (limit + 1)}
    invalid = {"schema_version": "1.0", "facts": [invalid_fact]}
    valid = {"schema_version": "1.0", "facts": [valid_fact]}
    first = _response(invalid)
    transport = _FakeTransport([first, _response(valid if repaired else invalid)])
    provider = DeepSeekLLMProvider(model=DEEPSEEK_FLASH_MODEL, transport=transport)
    request = _request(
        task_type="research_web_evidence_normalization", timeout=30, response_schema=schema
    )
    caplog.set_level("INFO", logger="stock_assistant.platform.llm.deepseek")
    if repaired:
        result = await provider.generate(request)
        assert result.payload == valid and result.degraded is False
    else:
        with pytest.raises(DeepSeekProviderError) as raised:
            await provider.generate(request)
        assert raised.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert [
        (role, content) for role, content in transport.requests[1].messages if role == "assistant"
    ] == [("assistant", first.content)]
    assert f"/facts/0/{field}" in transport.requests[1].messages[-1][1]
    assert (
        "Revise the immediately preceding assistant JSON" in transport.requests[1].messages[-1][1]
    )
    records = [r for r in caplog.records if getattr(r, "event", None) == "deepseek_generation"]
    assert records[0].__dict__["schema_violations"] == [
        {"path": f"/facts/0/{field}", "rule": "STRING_MAX_LENGTH"}
    ]
    assert records[-1].__dict__["schema_repair"] == 1
    assert (
        invalid_fact[field] not in caplog.text and valid_fact["source_excerpt"] not in caplog.text
    )
    assert all(0 < r.timeout_seconds <= 30 for r in transport.requests)


@pytest.mark.asyncio
async def test_transport_cannot_inject_arbitrary_repair_feedback() -> None:
    untrusted_marker = "transport-supplied-content-must-not-enter-prompt"
    transport = _FakeTransport(
        [
            DeepSeekProviderError(
                "DEEPSEEK_RESPONSE_SCHEMA_INVALID",
                retryable=True,
                repair_hint=untrusted_marker,
            ),
            _response({"title": "修复完成", "points": ["仅供研究"]}),
        ]
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(_request())

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 1
    assert untrusted_marker not in transport.requests[0].messages[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "task_type",
    [
        "research_report_narrative",
        "research_score_assessment",
        "research_reader_rewrite",
    ],
)
async def test_pro_disables_thinking_for_direct_structured_research_tasks(
    task_type: str,
) -> None:
    score_task = task_type == "research_score_assessment"
    transport = _FakeTransport(
        [
            _response(
                _score_assessment_payload()
                if score_task
                else {"title": "简要说明", "points": ["仅供研究"]}
            )
        ]
    )

    await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
    ).generate(
        _request(
            task_type=task_type,
            response_schema=(
                ResearchScoreAssessmentDraft.model_json_schema(mode="validation")
                if score_task
                else None
            ),
        )
    )

    assert transport.requests[0].thinking_enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [True, False])
@pytest.mark.parametrize("reason", ["建议买入该公司。", "本报告不构成建议\uff0c但可以建仓。"])
async def test_score_advice_uses_bounded_repair_without_degraded_success(
    repaired: bool,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema = ResearchScoreAssessmentDraft.model_json_schema(mode="validation")
    invalid = _score_assessment_payload(reason=reason)
    transport = _FakeTransport(
        [_response(invalid), _response(_score_assessment_payload() if repaired else invalid)]
    )
    request = _request(task_type="research_score_assessment").model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.score_assessment",
        }
    )

    provider = DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    )
    caplog.set_level("INFO", logger="stock_assistant.platform.llm.deepseek")
    if repaired:
        result = await provider.generate(request)
        assessment = ResearchScoreAssessmentDraft.model_validate(result.payload)
        assert assessment.model_dump(mode="json") == _score_assessment_payload()
        assert result.degraded is False
        assert result.input_tokens == 24 and result.output_tokens == 16
    else:
        with pytest.raises(DeepSeekProviderError) as raised:
            await provider.generate(request)
        assert raised.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
        assert any(item.rule == "INVESTMENT_ADVICE" for item in raised.value.schema_violations)
    assert len(transport.requests) == 2
    assert "investment" in transport.requests[1].messages[-1][1]
    records = [r for r in caplog.records if getattr(r, "event", None) == "deepseek_generation"]
    assert records[-1].__dict__["input_tokens"] == 24
    assert records[-1].__dict__["output_tokens"] == 16
    assert reason not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["normal", "repair", "persistent_invalid"])
@pytest.mark.parametrize("numeric_summary", ["收入指数为 126.40。", "五年收入指数仅为合成证据。"])
async def test_narrative_keeps_evidence_numbers_with_one_bounded_advice_repair(
    scenario: str,
    numeric_summary: str,
) -> None:
    request, valid = await _grounded_narrative_request()
    valid = valid.model_copy(
        update={
            "sections": [
                valid.sections[0].model_copy(update={"summary": numeric_summary}),
                *valid.sections[1:],
            ]
        }
    )
    invalid = valid.model_copy(
        update={
            "sections": [
                valid.sections[0].model_copy(update={"summary": numeric_summary + "建议买入。"}),
                *valid.sections[1:],
            ]
        }
    )
    invalid_response = _response(invalid.model_dump(mode="json"))
    responses: list[DeepSeekTransportResponse | DeepSeekProviderError] = [
        _response(valid.model_dump(mode="json"))
    ]
    if scenario == "normal":
        responses.append(_response(valid.model_dump(mode="json")))
    if scenario != "normal":
        responses.insert(0, invalid_response)
    if scenario == "persistent_invalid":
        responses.insert(0, invalid_response)
    transport = _FakeTransport(responses)
    provider = DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport)

    if scenario == "persistent_invalid":
        with pytest.raises(DeepSeekProviderError) as captured:
            await provider.generate(request)
        assert captured.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
        assert captured.value.failure_kind.value == "invalid_output"
        assert (
            reporting_module._core_model_error(captured.value).reason_code == "MODEL_OUTPUT_INVALID"
        )
        assert len(transport.requests) == 2
        assert len(transport.responses) == 1
        return

    result = await provider.generate(request)

    narrative = ResearchNarrativeCandidate.model_validate(result.payload)
    assert len(transport.requests) == (1 if scenario == "normal" else 2)
    assert result.degraded is False
    assert result.payload == valid.model_dump(mode="json")
    assert narrative.sections[0].title == "近五年研究主题"
    assert narrative.sections[0].summary == numeric_summary
    source_fact = next(
        fact
        for fact in json.loads(request.messages[-1].content)["facts"]
        if fact["key"] == "financial.revenue"
    )
    assert narrative.hypotheses[0].supporting_fact_keys[-1] == source_fact["key"]
    assert "100.00" in source_fact["statement"]
    assert "126.40" in source_fact["statement"]
    if scenario == "repair":
        repair_prompt = transport.requests[1].messages[-1][1]
        assert "repair round 1" in repair_prompt
        assert "At JSON pointer /sections/0/summary" in repair_prompt
        assert "收入指数为 126.40。" not in repair_prompt


@pytest.mark.asyncio
async def test_company_name_with_numeric_character_is_kept_without_a_model_repair() -> None:
    schema = ResearchNarrativeDraft.model_json_schema(mode="validation")
    transport = _FakeTransport(
        [
            _response(
                _narrative_payload(
                    numeric_prose=False,
                    section_title="兆易创新研究主题",
                )
            )
        ]
    )
    request = _request(task_type="research_report_narrative", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.narrative_draft",
        }
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(request)

    narrative = ResearchNarrativeDraft.model_validate(result.payload)
    assert {section.title for section in narrative.sections} == {"兆易创新研究主题"}
    assert len(transport.requests) == 1
    assert result.degraded is False


@pytest.mark.asyncio
async def test_safe_logs_expose_attempt_state_without_prompt_or_candidate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prompt_marker = "prompt-private-marker-must-not-be-logged"
    candidate_marker = "candidate-private-marker-must-not-be-logged"
    schema = ResearchNarrativeDraft.model_json_schema(mode="validation")
    invalid_payload = _narrative_payload(numeric_prose=True)
    invalid_payload["unexpected"] = candidate_marker
    transport = _FakeTransport(
        [_response(invalid_payload), _response(_narrative_payload(numeric_prose=False))]
    )
    request = _request(task_type="research_report_narrative", timeout=90).model_copy(
        update={
            "messages": [PromptMessage(role=MessageRole.USER, content=prompt_marker)],
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.narrative_draft",
        }
    )

    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.llm.deepseek"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(request)

    records = [
        record for record in caplog.records if record.__dict__.get("event") == "deepseek_generation"
    ]
    assert len(records) == 2
    rendered = "\n".join(record.getMessage() for record in records)
    assert "task_type=research_report_narrative" in rendered
    assert "correlation_id=127d9d65-8356-5368-b22a-b85dabf12ea8" in rendered
    assert "provider_call=2" in rendered
    assert "transport_retry=0" in rendered
    assert "schema_repair=1" in rendered
    assert "failure_kind=none" in rendered
    assert "code=DEEPSEEK_GENERATION_SUCCEEDED" in rendered
    assert "numeric_sanitized_fields=" in rendered
    assert records[0].__dict__["failure_kind"] == "invalid_output"
    assert records[0].__dict__["error_code"] == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert records[0].__dict__["numeric_sanitized_fields"] == 0
    assert prompt_marker not in rendered
    assert candidate_marker not in rendered
    assert all(record.__dict__["task_type"] == "research_report_narrative" for record in records)
    assert all("provider_call" in record.__dict__ for record in records)
    assert all("transport_retry" in record.__dict__ for record in records)
    assert all("schema_repair" in record.__dict__ for record in records)
    assert all("failure_kind" in record.__dict__ for record in records)
    assert all("error_code" in record.__dict__ for record in records)
    assert all("numeric_sanitized_fields" in record.__dict__ for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_candidate",
    [
        "本报告不提供目标价\uff0c但建议买入该公司。",
        "建议买入并给出目标价。",
        "公司公开披露材料仅供研究参考却仍出现ＰＲＩＣＥ　ＴＡＲＧＥＴ字样。",
    ],
)
async def test_model_authored_advice_metadata_repairs_once_without_logging_candidate(
    forbidden_candidate: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema = _model_authored_prose_object_schema()
    properties = cast(dict[str, JsonValue], schema["properties"])
    prose_schema = cast(dict[str, JsonValue], properties["text"])
    assert prose_schema["x-stockassistant-no-investment-advice"] is True
    transport = _FakeTransport(
        [
            _response({"text": forbidden_candidate}),
            _response({"text": "仅说明证据边界, 不形成交易结论。"}),
        ]
    )
    request = _request(
        task_type="research_reader_rewrite",
        timeout=90,
        response_schema=schema,
    )

    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.llm.deepseek"):
        result = await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(request)

    assert result.payload == {"text": "仅说明证据边界, 不形成交易结论。"}
    assert len(transport.requests) == 2
    repair_prompt = transport.requests[1].messages[-1][1]
    assert "DEEPSEEK_RESPONSE_SCHEMA_INVALID" in repair_prompt
    assert "At JSON pointer /text" in repair_prompt
    assert forbidden_candidate not in repair_prompt
    records = [
        record for record in caplog.records if record.__dict__.get("event") == "deepseek_generation"
    ]
    assert len(records) == 2
    assert records[0].__dict__["error_code"] == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert records[-1].__dict__["schema_repair"] == 1
    assert forbidden_candidate not in "\n".join(record.getMessage() for record in records)


@pytest.mark.asyncio
async def test_model_authored_advice_exhausts_the_single_schema_repair() -> None:
    forbidden_candidate = "建议买入并给出目标价。"
    transport = _FakeTransport(
        [
            _response({"text": forbidden_candidate}),
            _response({"text": forbidden_candidate}),
            _response({"text": "不应到达。"}),
        ]
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(
            _request(
                task_type="research_reader_rewrite",
                timeout=90,
                response_schema=_model_authored_prose_object_schema(),
            )
        )

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2


@pytest.mark.parametrize(
    "adapter",
    [
        TypeAdapter(ModelAuthoredProse),
        TypeAdapter(ModelAuthoredScoreReason),
        TypeAdapter(ModelAuthoredTerm),
    ],
)
def test_model_authored_contract_fields_reject_investment_advice(
    adapter: TypeAdapter[str],
) -> None:
    assert adapter.json_schema(mode="validation")["x-stockassistant-no-investment-advice"] is True
    assert adapter.validate_python("本说明不提供目标价。") == "本说明不提供目标价。"
    for candidate in (
        "本报告不提供目标价\uff0c但建议买入该公司。",
        "本报告不提供目标价。建议卖出该公司。",
        "本报告不是不提供目标价。",
        "不建议买入该公司。",
        "公司公开披露材料仅供研究参考却仍出现ＰＲＩＣＥ　ＴＡＲＧＥＴ字样。",
    ):
        with pytest.raises(ValidationError, match="investment-advice language"):
            adapter.validate_python(candidate)


@pytest.mark.parametrize(
    "candidate",
    ["本报告不提供目标价\uff1a\uff11\uff10\uff10元。", "本报告不提供目标价\uff08100元\uff09。"],
)
def test_target_price_cannot_hide_in_a_disclaimer(candidate: str) -> None:
    with pytest.raises(ValidationError, match="investment-advice language"):
        TypeAdapter(ModelAuthoredProse).validate_python(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disclaimer",
    [
        "本报告不提供目标价\uff0c也不构成投资建议。",
        "本说明不构成建议买入, 也不提供目标价。",
        "资料仍待核验。本报告不提供目标价。",
        "本研究不提供买入建议或卖出建议\uff1b并不作出收益承诺。",
        "本报告不提供任何目标价。",
        "本报告仅供研究\uff0c不提供任何 buy rating。",
        "本报告不提供任何形式的 sell rating。",
        "本说明仅供研究\uff0c不提供具体的 position size。",
        "本报告仅供研究\uff0c不提供任何 "
        "\uff30\uff32\uff29\uff23\uff25\u3000\uff34\uff21\uff32\uff27\uff25\uff34。",
    ],
)
async def test_explicit_negative_disclaimer_passes_without_using_repair(disclaimer: str) -> None:
    assert TypeAdapter(ModelAuthoredProse).validate_python(disclaimer) == disclaimer
    transport = _FakeTransport([_response({"text": disclaimer})])
    result = await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
        _request(
            task_type="research_report_narrative",
            timeout=90,
            response_schema=_model_authored_prose_object_schema(),
        )
    )
    assert result.payload == {"text": disclaimer}
    assert result.degraded is False
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_direct_research_allows_only_one_structural_repair() -> None:
    invalid = _response({"title": "缺字段"})
    transport = _FakeTransport([invalid, invalid, _response({"title": "不应到达", "points": []})])

    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_INVALID"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(_request(task_type="research_report_narrative", timeout=90))

    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_schema_repair_then_two_transport_retries_share_four_call_budget() -> None:
    invalid_payload = {"title": "格式错误", "points": "应为数组"}
    transport = _FakeTransport(
        [
            _response(invalid_payload),
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            _response({"title": "修复完成", "points": ["仅供研究"]}),
        ]
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request(task_type="research_report_narrative", timeout=90))

    assert result.payload["title"] == "修复完成"
    assert len(transport.requests) == 4
    assert all(
        "DEEPSEEK_RESPONSE_SCHEMA_INVALID" in request.messages[-1][1]
        for request in transport.requests[1:]
    )


@pytest.mark.asyncio
async def test_real_asyncio_timeout_cancels_hanging_transport() -> None:
    entered = asyncio.Event()
    transport_cancelled = asyncio.Event()
    never_finish = asyncio.Event()

    class HangingTransport:
        def __init__(self) -> None:
            self.requests: list[DeepSeekTransportRequest] = []

        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            self.requests.append(request)
            entered.set()
            try:
                await never_finish.wait()
            finally:
                transport_cancelled.set()
            return _response({"title": "不应返回", "points": []})

    transport = HangingTransport()
    with pytest.raises(DeepSeekProviderError) as exc_info:
        await asyncio.wait_for(
            DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
                _request(task_type="research_report_narrative", timeout=0.01)
            ),
            timeout=0.5,
        )

    assert exc_info.value.code == "DEEPSEEK_TIMEOUT"
    assert exc_info.value.retryable is True
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    await asyncio.wait_for(transport_cancelled.wait(), timeout=0.5)
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_cancellation_during_transport_is_not_retried() -> None:
    entered = asyncio.Event()
    transport_cancelled = asyncio.Event()
    never_finish = asyncio.Event()

    class BlockingTransport:
        def __init__(self) -> None:
            self.requests: list[DeepSeekTransportRequest] = []

        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            self.requests.append(request)
            entered.set()
            try:
                await never_finish.wait()
            finally:
                transport_cancelled.set()
            return _response({"title": "不应返回", "points": []})

    transport = BlockingTransport()
    task = asyncio.create_task(
        DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
            _request(task_type="research_report_narrative", timeout=90)
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)
    await asyncio.wait_for(transport_cancelled.wait(), timeout=0.5)
    assert len(transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "takeaway", ["合成指数为126.40。", "合成期间为近五年。", "合成数据覆盖二十季度。"]
)
async def test_reader_schema_preserves_numeric_prose_before_domain_validation(
    takeaway: str,
) -> None:
    schema = ResearchReaderDraft.model_json_schema(mode="validation")
    transport = _FakeTransport([_response(_reader_payload(takeaway=takeaway))])
    request = _request(task_type="research_reader_rewrite").model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.reader_draft",
        }
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(request)

    reader = ResearchReaderDraft.model_validate(result.payload)
    assert reader.one_sentence_takeaway == takeaway
    assert len(transport.requests) == 1
    assert result.degraded is False


def test_reader_schema_requires_three_business_and_score_explanations() -> None:
    schema = ResearchReaderDraft.model_json_schema(mode="validation")
    properties = cast(dict[str, JsonValue], schema["properties"])
    assert properties["schema_version"]["const"] == "2.0"
    assert "claim_ids" not in properties

    for field in ("how_the_company_makes_money", "why_this_score"):
        field_schema = cast(dict[str, JsonValue], properties[field])
        assert field_schema["minItems"] == 3
        assert field_schema["maxItems"] == 3

        invalid = _reader_payload()
        invalid[field] = cast(list[object], invalid[field])[:2]
        with pytest.raises(DeepSeekProviderError) as exc_info:
            validate_json_schema(cast(dict[str, JsonValue], invalid), schema)

        assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
        assert exc_info.value.repair_hint is not None
        assert f"/{field}" in exc_info.value.repair_hint


@pytest.mark.asyncio
async def test_reader_repair_replaces_english_first_response_with_chinese() -> None:
    schema = ResearchReaderDraft.model_json_schema(mode="validation")
    transport = _FakeTransport(
        [
            _response(_reader_payload(takeaway="This report remains written in English.")),
            _response(_reader_payload(takeaway="公司结论仍需结合公开披露核验。")),
        ]
    )
    request = _request(
        task_type="research_reader_rewrite",
        timeout=90,
        response_schema=schema,
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(request)

    reader = ResearchReaderDraft.model_validate(result.payload)
    assert reader.one_sentence_takeaway == "公司结论仍需结合公开披露核验。"
    assert len(transport.requests) == 2
    assert "use Simplified Chinese prose" in transport.requests[1].messages[-1][1]


@pytest.mark.asyncio
async def test_reader_repair_exhausts_after_two_english_responses() -> None:
    schema = ResearchReaderDraft.model_json_schema(mode="validation")
    english = _response(_reader_payload(takeaway="This report remains written in English."))
    transport = _FakeTransport([english, english])

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(
            _request(
                task_type="research_reader_rewrite",
                timeout=90,
                response_schema=schema,
            )
        )

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert "use Simplified Chinese prose" in transport.requests[1].messages[-1][1]


@pytest.mark.asyncio
async def test_reader_schema_accepts_chinese_with_dram_without_repair() -> None:
    schema = ResearchReaderDraft.model_json_schema(mode="validation")
    takeaway = "公司 DRAM 业务仍需结合公开披露核验。"
    transport = _FakeTransport([_response(_reader_payload(takeaway=takeaway))])

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(
        _request(
            task_type="research_reader_rewrite",
            timeout=90,
            response_schema=schema,
        )
    )

    reader = ResearchReaderDraft.model_validate(result.payload)
    assert reader.one_sentence_takeaway == takeaway
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_second_structural_error_exhausts_score_repair_budget() -> None:
    schema = ResearchScoreAssessmentDraft.model_json_schema(mode="validation")
    missing_final_dimension = SCORE_DIMENSION_IDS[:-1]
    transport = _FakeTransport(
        [
            _raw_response("{", finish_reason="length"),
            _response(_score_assessment_payload(included_dimensions=missing_final_dimension)),
            _response(_score_assessment_payload()),
        ]
    )
    request = _request(task_type="research_score_assessment", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.score_assessment",
        }
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(request)

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert "DEEPSEEK_RESPONSE_INCOMPLETE" in transport.requests[1].messages[-1][1]


@pytest.mark.asyncio
async def test_score_repair_converges_only_after_field_level_validator_feedback() -> None:
    schema = ResearchScoreAssessmentDraft.model_json_schema(mode="validation")

    class FeedbackAwareTransport:
        def __init__(self) -> None:
            self.requests: list[DeepSeekTransportRequest] = []

        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            self.requests.append(request)
            feedback = request.messages[-1][1]
            if 'add required properties: ["risk_resilience"]' in feedback:
                return _response(_score_assessment_payload())
            return _response(
                _score_assessment_payload(included_dimensions=SCORE_DIMENSION_IDS[:-1])
            )

    transport = FeedbackAwareTransport()
    request = _request(task_type="research_score_assessment", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.score_assessment",
        }
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(request)

    assessment = ResearchScoreAssessmentDraft.model_validate(result.payload)
    assert [dimension.value for dimension, _ in assessment.dimension_items()] == list(
        SCORE_DIMENSION_IDS
    )
    assert len(transport.requests) == 2
    repair_prompt = transport.requests[1].messages[-1][1]
    assert "repair round 1" in repair_prompt
    assert "risk_resilience" in repair_prompt


def _score_candidate_payload() -> dict[str, JsonValue]:
    return {
        "schema_version": "2.0",
        **{
            dimension.value: {
                "grade": "balanced",
                "reasons": ["同一份资料说明了缓冲条件和限制。"],
                "supporting_fact_keys": [dimension.value + ".fact"],
                "counter_fact_keys": [],
            }
            for dimension in ResearchScoreDimensionId
        },
    }


def test_dynamic_score_schema_enforces_dimension_fact_keys() -> None:
    allowlist = {
        dimension: frozenset({dimension.value + ".fact"}) for dimension in ResearchScoreDimensionId
    }
    schema, schema_hash = reporting_module._score_assessment_schema(allowlist)
    assert schema_hash == _hash(schema)
    payload = _score_candidate_payload()
    validate_json_schema(payload, schema)
    payload["business_quality"]["supporting_fact_keys"] = ["financial_quality.fact"]
    with pytest.raises(DeepSeekProviderError) as caught:
        validate_json_schema(payload, schema)
    assert caught.value.repair_hint is not None
    assert "/business_quality/supporting_fact_keys/0" in caught.value.repair_hint
    assert "financial_quality.fact" not in caught.value.repair_hint


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["duplicate_support", "duplicate_counter", "overlap"])
async def test_score_selection_duplicates_and_overlap_do_not_spend_a_structure_repair(
    selection: str,
) -> None:
    schema, _ = reporting_module._score_assessment_schema(
        {
            dimension: frozenset({dimension.value + ".fact"})
            for dimension in ResearchScoreDimensionId
        }
    )
    payload = _score_candidate_payload()
    business = payload["business_quality"]
    if selection == "duplicate_support":
        business["supporting_fact_keys"] *= 2
    elif selection == "duplicate_counter":
        business["counter_fact_keys"] = business["supporting_fact_keys"] * 2
    else:
        business["counter_fact_keys"] = business["supporting_fact_keys"][:]
    transport = _FakeTransport([_response(payload)])
    result = await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
        _request(task_type="research_score_assessment", response_schema=schema)
    )
    assert len(transport.requests) == 1
    assert result.payload == payload
    # This is only a candidate; binding and semantic dual-use checks are covered by the chain tests.


@pytest.mark.asyncio
async def test_score_consistency_failure_survives_exhausted_budget_without_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _FakeClock()
    payload = _score_assessment_payload()
    business = cast(dict[str, object], payload["business_quality"])
    business["evidence_ids"] = []
    requests: list[DeepSeekTransportRequest] = []

    class InvalidAtDeadlineTransport:
        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            requests.append(request)
            clock.advance(90)
            return _response(payload)

    provider = DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL, transport=InvalidAtDeadlineTransport(), clock=clock
    )
    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.llm.deepseek"):
        with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TIMEOUT") as error:
            await provider.generate(
                _request(
                    task_type="research_score_assessment",
                    timeout=90,
                    response_schema=ResearchScoreAssessmentDraft.model_json_schema(
                        mode="validation"
                    ),
                )
            )
    assert len(requests) == 1 and requests[0].timeout_seconds == 90
    assert error.value.previous_validation_code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    records = [r for r in caplog.records if r.__dict__.get("event") == "deepseek_generation"]
    terminal = records[-1].__dict__
    assert terminal["phase"] == "budget"
    assert terminal["schema_violations"] == [
        {"path": "/business_quality", "rule": "SCORE_SUPPORT_REQUIRED"}
    ]
    assert terminal["remaining_budget_ms"] == 0
    assert terminal["previous_validation_code"] == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    logged = repr([r.__dict__ for r in records])
    assert "9712aec6-ce75-55be-ae18-13ee648f85dd" not in logged
    assert "证据与反证大致平衡。" not in logged


@pytest.mark.asyncio
async def test_second_structural_error_exhausts_narrative_repair_budget() -> None:
    schema = ResearchNarrativeDraft.model_json_schema(mode="validation")
    section_ids = [item.value for item in ResearchSectionKind]

    def payload(sections: list[str]) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "sections": [
                {"kind": section, "title": "研究主题", "summary": "证据摘要。"}
                for section in sections
            ],
            "hypotheses": [
                {
                    "section": "bull_case",
                    "statement": "积极情景仍需后续证据支持。",
                    "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
                    "counter_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
                    "falsification_conditions": ["核心业务持续恶化。"],
                    "uncertainty": "现有资料覆盖有限。",
                }
            ],
        }

    transport = _FakeTransport(
        [
            _raw_response("{", finish_reason="length"),
            _response(
                {
                    "schema_version": "1.0",
                    "sections": [{"summary": "缺少章节标识。"}],
                    "hypotheses": [],
                }
            ),
            _response(payload(section_ids)),
        ]
    )
    request = _request(task_type="research_report_narrative", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.narrative_draft",
        }
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(request)

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert "DEEPSEEK_RESPONSE_INCOMPLETE" in transport.requests[1].messages[-1][1]


@pytest.mark.asyncio
async def test_second_hypothesis_error_exhausts_narrative_repair_budget() -> None:
    schema = ResearchNarrativeDraft.model_json_schema(mode="validation")
    section_ids = [item.value for item in ResearchSectionKind]

    def payload(hypothesis_section: str) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "sections": [
                {"kind": section, "title": "研究主题", "summary": "证据摘要。"}
                for section in section_ids
            ],
            "hypotheses": [
                {
                    "section": hypothesis_section,
                    "statement": "积极情景仍需后续证据支持。",
                    "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
                    "counter_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
                    "falsification_conditions": ["核心业务持续恶化。"],
                    "uncertainty": "现有资料覆盖有限。",
                }
            ],
        }

    transport = _FakeTransport(
        [
            _raw_response("{", finish_reason="length"),
            _response(payload("business_model")),
            _response(payload("bull_case")),
        ]
    )
    request = _request(task_type="research_report_narrative", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.narrative_draft",
        }
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(request)

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert "DEEPSEEK_RESPONSE_INCOMPLETE" in transport.requests[1].messages[-1][1]


@pytest.mark.asyncio
async def test_structured_second_attempt_keeps_structural_errors_fail_closed() -> None:
    schema = ResearchNarrativeDraft.model_json_schema(mode="validation")
    invalid = _response(
        _narrative_payload(
            numeric_prose=True,
            first_hypothesis_section="business_model",
        )
    )
    transport = _FakeTransport([invalid, invalid])
    request = _request(task_type="research_report_narrative", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.numeric_fallback",
        }
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(request)

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2


def test_schema_validator_batches_safe_numeric_term_paths() -> None:
    prose_schema = TypeAdapter(ModelAuthoredTerm).json_schema(mode="validation")
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "first": prose_schema,
            "nested": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": prose_schema},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["first", "nested"],
        "additionalProperties": False,
    }

    with pytest.raises(DeepSeekProviderError) as exc_info:
        validate_json_schema(
            {"first": "近五年", "nested": [{"text": "二十季度"}]},
            schema,
        )

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert exc_info.value.repair_hint is not None
    assert "At JSON pointer /first" in exc_info.value.repair_hint
    assert "At JSON pointer /nested/0/text" in exc_info.value.repair_hint
    assert "近五年" not in exc_info.value.repair_hint
    assert "二十季度" not in exc_info.value.repair_hint


def test_schema_repair_feedback_is_bounded_without_accepting_remaining_errors() -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
        "required": ["items"],
        "additionalProperties": False,
    }
    with pytest.raises(DeepSeekProviderError) as captured:
        validate_json_schema({"items": [False] * 20}, schema)
    assert captured.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert captured.value.repair_hint is not None
    assert len(captured.value.repair_hint.splitlines()) == 16


def test_schema_error_after_invalid_sibling_remains_nonrepairable() -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"first": {"type": "string"}, "last": {"type": "string", "unknown": True}},
        "required": ["first", "last"],
        "additionalProperties": False,
    }
    with pytest.raises(DeepSeekProviderError) as captured:
        validate_json_schema({"first": False, "last": "有效文本"}, schema)
    assert captured.value.code == "DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED"


@pytest.mark.asyncio
async def test_narrative_schema_accepts_non_numeric_chinese_words_without_repair() -> None:
    schema = ResearchNarrativeDraft.model_json_schema(mode="validation")
    section_ids = [item.value for item in ResearchSectionKind]
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "sections": [
            {
                "kind": section,
                "title": "芯片行业采用标准研究口径",
                "summary": "公司供应地位仍需核验, 行业格局持续分化。",
            }
            for section in section_ids
        ],
        "hypotheses": [
            {
                "section": "bull_case",
                "statement": "芯片业务可能形成支持, 但来源独立性不足。",
                "supporting_evidence_ids": ["9712aec6-ce75-55be-ae18-13ee648f85dd"],
                "counter_evidence_ids": ["127d9d65-8356-5368-b22a-b85dabf12ea8"],
                "falsification_conditions": ["核心业务持续恶化。"],
                "uncertainty": "经营信息仍待核验。",
            }
        ],
    }
    transport = _FakeTransport([_response(payload)])
    request = _request(task_type="research_report_narrative", timeout=90).model_copy(
        update={
            "response_schema": schema,
            "response_schema_hash": _hash(schema),
            "response_schema_namespace": "research.narrative_draft",
        }
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(request)

    narrative = ResearchNarrativeDraft.model_validate(result.payload)
    assert narrative.sections[0].title == "芯片行业采用标准研究口径"
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "numeric_text",
    [
        "结论为1。",
        "结论为１。",
        "结论为Ⅻ。",
        "结论为①。",
        "结论为⁴。",
        "结论为𝟜。",
        "结论为½。",
        "排名十。",
        "金额为壹。",
        "金额为贰。",
        "金额为貳。",
        "金额为万亿元。",
        "金额为萬億元。",
        "连续三年增长。",
        "百分之十来自新产品。",
        "二〇二六年八月披露。",
        "连续三期改善。",
        "排名第三。",
        "前三大客户集中。",
        "综合得分为六十八。",
        "新增一座工厂。",
        "合计一家客户。",
        "公司仅有一家客户。",
        "新增了一个项目。",
        "公司有一家客户。",
        "新增一个项目。",
        "仅一种产品。",
        "增加两家客户。",
        "一次性增加两家客户。",
        "一次性损益为20万元。",
        "仅一次分红尚需核验。",
        "事件流共记录一次调整。",
        "事件只发生一次。",
        "事件共记录了一次调整。",
        "事件仅发生过一次。",
        "公司每年分红一次。",
        "事件至少发生一次。",
        "发生超过一次调整。",
        "分红不足一次。",
        "发生不到一次调整。",
        "分红一次以上。",
        "分红一次以下。",
        "分红一次以内。",
        "事件已发生十一次。",
        "采用 DDR5 产品。",
        "金额为貳億元。",
        "收入来源单一, 但利润增长三成。",
        "现金流与利润口径不一, 金额为一万元。",
        "收入不一百万元。",
        "利润为一。",
        "利润来源之一分。",
    ],
)
def test_numeric_term_contract_matches_local_schema_rejections(
    numeric_text: str,
) -> None:
    assert contains_numeric_expression(numeric_text) is True
    for adapter in (TypeAdapter(ModelAuthoredTerm),):
        schema: dict[str, JsonValue] = {
            "type": "object",
            "properties": {"text": adapter.json_schema(mode="validation")},
            "required": ["text"],
            "additionalProperties": False,
        }
        with pytest.raises(ValidationError):
            adapter.validate_python(numeric_text)
        with pytest.raises(DeepSeekProviderError) as exc_info:
            validate_json_schema({"text": numeric_text}, schema)

        assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "ordinary_text",
    [
        "公司业务保持韧性。",
        "多年经营记录仍待核验。",
        "双方证据存在分歧。",
        "少数公司出现波动。",
        "行业排名仍然靠后。",
        "芯片行业采用标准研究口径。",
        "公司为兆易创新。",
        "万科的业务仍需核验。",
        "一汽的客户结构仍待确认。",
        "三一重工的产品信息仍待核验。",
        "第一创业的公开说明仍待核验。",
        "一方面需要核对来源。",
        "需要剔除一次性损益后判断盈利质量。",
        "公司是一家以研发为核心的企业。",
        "这是一个仍待验证的风险。",
        "这是一种仍待验证的解释。",
        "合成事件流记录了一次重大产能调整。",
        "一次分红尚需核验。",
    ],
)
def test_numeric_contract_and_local_schema_accept_the_same_ordinary_text(
    ordinary_text: str,
) -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"text": TypeAdapter(ModelAuthoredProse).json_schema(mode="validation")},
        "required": ["text"],
        "additionalProperties": False,
    }

    assert contains_numeric_expression(ordinary_text) is False
    assert TypeAdapter(ModelAuthoredProse).validate_python(ordinary_text) == ordinary_text
    validate_json_schema({"text": ordinary_text}, schema)


@pytest.mark.asyncio
async def test_direct_structured_repair_budget_is_bounded_at_two_attempts() -> None:
    invalid = _response({"title": "缺字段"})
    transport = _FakeTransport([invalid, invalid, _response({"title": "不应到达", "points": []})])

    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_INVALID"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(_request(task_type="research_report_narrative", timeout=90))

    assert len(transport.requests) == 2
    assert "repair round 1" in transport.requests[-1].messages[-1][1]


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [90, 180])
async def test_narrative_repair_has_time_after_slow_initial_output(budget: int) -> None:
    clock = _FakeClock()
    requests: list[DeepSeekTransportRequest] = []

    class SlowRepairTransport:
        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            requests.append(request)
            duration = 65 if len(requests) == 1 else 50
            clock.advance(min(duration, request.timeout_seconds))
            if duration > request.timeout_seconds:
                raise TimeoutError("bounded synthetic replay")
            return _response(
                {"title": "研究", "points": "错误字段" if len(requests) == 1 else ["仅供研究"]}
            )

    provider = DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL, transport=SlowRepairTransport(), clock=clock
    )
    request = _request(task_type="research_report_narrative", timeout=budget)
    if budget == 90:
        with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TIMEOUT"):
            await provider.generate(request)
    else:
        result = await provider.generate(request)
        assert result.payload["points"] == ["仅供研究"]
        assert result.latency_ms == 115_000
    assert len(requests) == 2
    assert requests[0].timeout_seconds == 90
    assert requests[1].timeout_seconds == (25 if budget == 90 else 90)


@pytest.mark.asyncio
async def test_narrative_larger_budget_still_obeys_short_task_deadline() -> None:
    transport = _FakeTransport([_response({"title": "研究", "points": ["仅供研究"]})])
    request = _request(task_type="research_report_narrative", timeout=180)
    request = request.model_copy(
        update={
            "context": request.context.model_copy(
                update={"deadline_at": datetime.now(UTC) + timedelta(seconds=3)}
            )
        }
    )
    await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(request)
    assert 0 < transport.requests[0].timeout_seconds <= 3


class _DeadlineExhaustingTransport:
    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.requests: list[DeepSeekTransportRequest] = []

    async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
        self.requests.append(request)
        self.clock.advance(90)
        raise DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True)


class _SlowValidTransport:
    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.requests: list[DeepSeekTransportRequest] = []

    async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
        self.requests.append(request)
        self.clock.advance(75)
        return _response(_score_assessment_payload())


@pytest.mark.asyncio
async def test_retries_share_one_absolute_research_deadline() -> None:
    clock = _FakeClock()
    transport = _DeadlineExhaustingTransport(clock)

    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TEMPORARILY_UNAVAILABLE"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
            clock=clock,
        ).generate(_request(task_type="research_score_assessment", timeout=90))

    assert len(transport.requests) == 1
    assert transport.requests[0].timeout_seconds == 90


@pytest.mark.asyncio
async def test_valid_research_response_between_sixty_and_ninety_seconds_succeeds() -> None:
    clock = _FakeClock()
    transport = _SlowValidTransport(clock)

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        clock=clock,
    ).generate(
        _request(
            task_type="research_score_assessment",
            timeout=90,
            response_schema=ResearchScoreAssessmentDraft.model_json_schema(mode="validation"),
        )
    )

    ResearchScoreAssessmentDraft.model_validate(result.payload)
    assert result.latency_ms == 75_000
    assert transport.requests[0].timeout_seconds == 90


def _custom_exact_enum_schema(
    *, field_type: str = "string", expected_values: list[str] | None = None
) -> dict[str, JsonValue]:
    values = ["bull_case", "bear_case"] if expected_values is None else expected_values
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"kind": {"type": field_type}},
                    "required": ["kind"],
                    "additionalProperties": False,
                },
                "x-stockassistant-exact-enum-field": "kind",
                "x-stockassistant-exact-enum-values": values,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    "schema",
    [
        {**SCHEMA, "x-stockassistant-normalization": "NFKC"},
        {**SCHEMA, "x-stockassistant-no-investment-advice": True},
        {"type": "string", "x-stockassistant-no-investment-advice": False},
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "x-stockassistant-exact-enum-field": "kind",
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
        _custom_exact_enum_schema(expected_values=["bull_case", "bull_case"]),
        _custom_exact_enum_schema(field_type="integer"),
    ],
)
def test_malformed_custom_schema_keywords_fail_closed(schema: dict[str, JsonValue]) -> None:
    with pytest.raises(DeepSeekProviderError) as exc_info:
        validate_json_schema({"items": []}, schema)

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED"


def test_custom_exact_enum_non_string_actual_is_stably_invalid() -> None:
    schema = _custom_exact_enum_schema()

    with pytest.raises(DeepSeekProviderError) as exc_info:
        validate_json_schema(
            {"items": [{"kind": 1}, {"kind": "bear_case"}]},
            schema,
        )

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"


def test_validator_feedback_uses_real_json_pointers_and_escapes_tokens() -> None:
    with pytest.raises(DeepSeekProviderError) as root_error:
        validate_json_schema({}, SCHEMA)
    assert root_error.value.repair_hint is not None
    assert "At the document root" in root_error.value.repair_hint
    assert "JSON pointer /" not in root_error.value.repair_hint

    escaped_property_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"a~/b": {"type": "string"}},
        "required": ["a~/b"],
        "additionalProperties": False,
    }
    with pytest.raises(DeepSeekProviderError) as nested_error:
        validate_json_schema({"a~/b": 7}, escaped_property_schema)
    assert nested_error.value.repair_hint is not None
    assert "At JSON pointer /a~0~1b" in nested_error.value.repair_hint


def test_exact_enum_repair_feedback_is_bounded_by_schema_serializer() -> None:
    long_schema_value = "中" * 300
    schema = _custom_exact_enum_schema(expected_values=["bull_case", long_schema_value])

    with pytest.raises(DeepSeekProviderError) as exc_info:
        validate_json_schema(
            {"items": [{"kind": "bull_case"}, {"kind": "bull_case"}]},
            schema,
        )

    assert exc_info.value.repair_hint is not None
    assert "the value declared in the schema" in exc_info.value.repair_hint
    assert long_schema_value not in exc_info.value.repair_hint


@pytest.mark.asyncio
async def test_transport_retries_use_bounded_exponential_jitter_not_format_delay() -> None:
    delays: list[float] = []

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    transport = _FakeTransport(
        [
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            DeepSeekProviderError("DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True),
            _response({"title": "恢复", "points": ["仅供研究"]}),
        ]
    )

    await DeepSeekLLMProvider(
        model=DEEPSEEK_FLASH_MODEL,
        transport=transport,
        sleeper=record_delay,
    ).generate(_request())

    assert len(delays) == 2
    assert 0 < delays[0] < delays[1] <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_response", "error_code"),
    [
        (_raw_response("{"), "DEEPSEEK_RESPONSE_JSON_INVALID"),
        (
            _raw_response('{"title":"截断"}', finish_reason="length"),
            "DEEPSEEK_RESPONSE_INCOMPLETE",
        ),
    ],
)
async def test_retries_invalid_or_incomplete_json_once(
    first_response: DeepSeekTransportResponse,
    error_code: str,
) -> None:
    transport = _FakeTransport(
        [first_response, _response({"title": "恢复", "points": ["仅供研究"]})]
    )

    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        sleeper=lambda _seconds: _no_sleep(),
    ).generate(_request())

    assert result.payload["title"] == "恢复"
    assert error_code in transport.requests[1].messages[-1][1]


@pytest.mark.asyncio
async def test_incomplete_response_uses_the_only_schema_repair() -> None:
    transport = _FakeTransport(
        [
            _raw_response('{"title":"截断"}', finish_reason="length"),
            _response({"title": "仍不完整"}),
            _response({"title": "恢复", "points": ["仅供研究"]}),
        ]
    )

    with pytest.raises(DeepSeekProviderError) as exc_info:
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(_request())

    assert exc_info.value.code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert len(transport.requests) == 2
    assert "DEEPSEEK_RESPONSE_INCOMPLETE" in transport.requests[1].messages[-1][1]


async def _no_sleep() -> None:
    return None


@pytest.mark.asyncio
async def test_rejects_local_only_before_transport() -> None:
    transport = _FakeTransport([_response({"title": "不应调用", "points": []})])
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_PRIVACY_LEVEL_FORBIDDEN"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_FLASH_MODEL,
            transport=transport,
        ).generate(_request(privacy=PrivacyLevel.LOCAL_ONLY))
    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"title": "缺字段"},
        {"title": "多字段", "points": [], "secret": "no"},
        {"title": "类型错误", "points": "not-list"},
    ],
)
async def test_fails_closed_on_local_schema_validation(payload: object) -> None:
    transport = _FakeTransport([_response(payload), _response(payload), _response(payload)])
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_INVALID"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_FLASH_MODEL,
            transport=transport,
            sleeper=lambda _seconds: _no_sleep(),
        ).generate(_request())
    assert len(transport.requests) == 2


def test_base_url_and_models_are_exact_allowlists() -> None:
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_BASE_URL_FORBIDDEN"):
        OpenAICompatibleDeepSeekTransport(
            api_key=SecretStr("synthetic-test-key"),
            base_url="https://example.invalid/v1",
        )
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_MODEL_FORBIDDEN"):
        DeepSeekLLMProvider(model="deepseek-chat", transport=_FakeTransport([]))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deepseek_base_url="https://example.invalid")


def test_settings_requires_key_only_when_deepseek_is_enabled_in_production() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        deepseek_enabled=True,
        deepseek_api_key=None,
        r1_runtime_mode="authorized",
        database_url="postgresql+asyncpg://runtime:secret@db.internal:5432/stock_assistant",
        minio_secret_key=SecretStr("runtime-secret"),
    )
    with pytest.raises(ValueError, match="DeepSeek is enabled without an API key"):
        settings.assert_safe_production()


def test_local_schema_validator_resolves_bounded_pydantic_defs_and_uuid_formats() -> None:
    schema = {
        "type": "object",
        "properties": {"item": {"$ref": "#/$defs/Item"}},
        "required": ["item"],
        "additionalProperties": False,
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string", "format": "uuid"},
                    "grade": {"type": "integer", "minimum": 0, "maximum": 4},
                },
                "required": ["evidence_id", "grade"],
                "additionalProperties": False,
            }
        },
    }
    valid = {"item": {"evidence_id": str(UUID(int=1)), "grade": 4}}

    validate_json_schema(valid, schema)  # type: ignore[arg-type]
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_INVALID"):
        validate_json_schema(
            {"item": {"evidence_id": "not-a-uuid", "grade": 4}},  # type: ignore[arg-type]
            schema,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_retry_after_is_respected_and_permit_is_released_during_backoff() -> None:
    active = False
    entries = 0
    sleeps: list[float] = []

    @asynccontextmanager
    async def permit(_remaining: float) -> AsyncIterator[None]:
        nonlocal active, entries
        assert not active
        active = True
        entries += 1
        try:
            yield
        finally:
            active = False

    async def sleep(seconds: float) -> None:
        assert not active
        sleeps.append(seconds)

    transport = _FakeTransport(
        [
            DeepSeekProviderError(
                "DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True, retry_after_seconds=3.0
            ),
            _response({"title": "已完成", "points": []}),
        ]
    )
    result = await DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=transport,
        permit=permit,
        sleeper=sleep,
    ).generate(_request(timeout=10))
    assert result.payload["title"] == "已完成"
    assert sleeps == [3.0]
    assert entries == 2 and not active


@pytest.mark.asyncio
async def test_retry_after_longer_than_remaining_budget_does_not_retry_early() -> None:
    transport = _FakeTransport(
        [
            DeepSeekProviderError(
                "DEEPSEEK_TEMPORARILY_UNAVAILABLE", retryable=True, retry_after_seconds=30.0
            ),
        ]
    )
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TEMPORARILY_UNAVAILABLE"):
        await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
            _request(timeout=5)
        )
    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_provider_queue_wait_is_included_in_request_deadline() -> None:
    clock = _FakeClock()
    released = False

    @asynccontextmanager
    async def permit(_remaining: float) -> AsyncIterator[None]:
        nonlocal released
        clock.advance(90)
        try:
            yield
        finally:
            released = True

    transport = _FakeTransport([])
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TIMEOUT"):
        await DeepSeekLLMProvider(
            model=DEEPSEEK_PRO_MODEL,
            transport=transport,
            permit=permit,
            clock=clock,
        ).generate(_request(timeout=90))
    assert transport.requests == []
    assert released


@pytest.mark.asyncio
async def test_expired_run_deadline_does_not_call_provider() -> None:
    transport = _FakeTransport([])
    request = _request().model_copy(
        update={
            "context": _request().context.model_copy(
                update={
                    "deadline_at": datetime.now(UTC) - timedelta(seconds=1),
                }
            ),
        }
    )
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TIMEOUT"):
        await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(request)
    assert transport.requests == []


@pytest.mark.asyncio
async def test_sdk_rate_limit_keeps_safe_reason_and_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rate_limited(**_arguments: object) -> None:
        raise RateLimitError(
            "private-upstream-detail-marker",
            body=None,
            response=httpx.Response(
                429,
                headers={"retry-after": "3"},
                request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
            ),
        )

    monkeypatch.setattr(
        deepseek_module,
        "AsyncOpenAI",
        lambda **_arguments: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=rate_limited))
        ),
    )
    transport = OpenAICompatibleDeepSeekTransport(api_key=SecretStr("synthetic-unit-test-only"))
    with pytest.raises(DeepSeekProviderError) as captured:
        await transport.complete(
            DeepSeekTransportRequest(
                model=DEEPSEEK_PRO_MODEL,
                messages=(("user", "合成测试"),),
                maximum_output_tokens=8192,
                timeout_seconds=1,
                thinking_enabled=False,
            )
        )
    assert captured.value.code == "DEEPSEEK_RATE_LIMITED"
    assert captured.value.retryable
    assert captured.value.retry_after_seconds == 3
    assert "private-upstream-detail-marker" not in str(captured.value)


@pytest.mark.parametrize("value", ["NaN", "infinity", "invalid-header"])
def test_retry_after_rejects_unbounded_or_invalid_values(value: str) -> None:
    assert deepseek_module._retry_after_seconds(value) is None


def _unique_section_schema() -> dict[str, JsonValue]:
    schema = _custom_exact_enum_schema()
    properties = cast(dict[str, JsonValue], schema["properties"])
    items = cast(dict[str, JsonValue], properties["items"])
    del items["x-stockassistant-exact-enum-field"]
    del items["x-stockassistant-exact-enum-values"]
    items["x-stockassistant-unique-field"] = "kind"
    return schema


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
async def test_unique_present_sections_use_the_single_existing_repair(persistent: bool) -> None:
    duplicate = {"items": [{"kind": "bull_case"}, {"kind": "bull_case"}]}
    corrected = {"items": [{"kind": "bull_case"}]}
    transport = _FakeTransport(
        [_response(duplicate), _response(duplicate if persistent else corrected)]
    )
    provider = DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport)
    request = _request(
        task_type="research_report_narrative", response_schema=_unique_section_schema()
    )
    if persistent:
        with pytest.raises(
            DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_INVALID"
        ) as error:
            await provider.generate(request)
        assert error.value.schema_violations[0].rule == "UNIQUE_FIELD"
    else:
        result = await provider.generate(request)
        assert result.payload == corrected
        assert result.degraded is False
    assert len(transport.requests) == 2
    assert "duplicate" in transport.requests[1].messages[-1][1]
    validate_json_schema({"items": []}, _unique_section_schema())


@pytest.mark.parametrize("invalid_marker", [None, "", 1, "missing"])
def test_unique_field_metadata_rejects_unsupported_configuration(invalid_marker: JsonValue) -> None:
    schema = _unique_section_schema()
    properties = cast(dict[str, JsonValue], schema["properties"])
    items = cast(dict[str, JsonValue], properties["items"])
    items["x-stockassistant-unique-field"] = invalid_marker
    with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED"):
        validate_json_schema({"items": []}, schema)


def test_unique_field_rejects_non_array_and_non_string_fields() -> None:
    for schema in (
        {**SCHEMA, "x-stockassistant-unique-field": "title"},
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"kind": {"type": "integer"}},
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                    "x-stockassistant-unique-field": "kind",
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    ):
        with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_RESPONSE_SCHEMA_UNSUPPORTED"):
            validate_json_schema({"items": []}, cast(dict[str, JsonValue], schema))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transport_timeout", "outer_timeout", "budget"])
async def test_prior_validation_survives_timeout_or_outer_cancellation_without_body(
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _FakeClock()
    marker = "private-model-candidate-must-not-enter-logs"
    cancelled = asyncio.Event()
    calls: list[DeepSeekTransportRequest] = []

    class InvalidThenTimeoutTransport:
        async def complete(self, request: DeepSeekTransportRequest) -> DeepSeekTransportResponse:
            calls.append(request)
            if len(calls) == 1:
                if failure == "budget":
                    clock.advance(90)
                return _response({"title": "研究", "points": marker})
            if failure == "transport_timeout":
                clock.advance(90)
                raise TimeoutError(marker)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

    provider = DeepSeekLLMProvider(
        model=DEEPSEEK_PRO_MODEL,
        transport=InvalidThenTimeoutTransport(),
        clock=clock,
    )
    with caplog.at_level(logging.INFO, logger="stock_assistant.platform.llm.deepseek"):
        if failure == "outer_timeout":
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.01):
                    await provider.generate(
                        _request(task_type="research_report_narrative", timeout=90)
                    )
            assert cancelled.is_set()
        else:
            with pytest.raises(DeepSeekProviderError, match="DEEPSEEK_TIMEOUT") as error:
                await provider.generate(_request(task_type="research_report_narrative", timeout=90))
            assert error.value.previous_validation_code == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
            assert error.value.schema_violations[0].path == "/points"
    records = [r for r in caplog.records if r.__dict__.get("event") == "deepseek_generation"]
    assert len(records) == 2
    terminal = records[-1].__dict__
    assert terminal["error_code"] == (
        "DEEPSEEK_GENERATION_CANCELLED" if failure == "outer_timeout" else "DEEPSEEK_TIMEOUT"
    )
    assert terminal["previous_validation_code"] == "DEEPSEEK_RESPONSE_SCHEMA_INVALID"
    assert terminal["schema_violations"] == [{"path": "/points", "rule": "JSON_TYPE"}]
    assert terminal["validation_attempt"] == 1
    assert terminal["remaining_budget_ms"] >= 0
    assert len(calls) == (1 if failure == "budget" else 2)
    assert calls[0].timeout_seconds == 90
    assert marker not in repr([r.__dict__ for r in records])
    assert "/points" in records[-1].getMessage()


def test_schema_diagnostics_are_bounded_and_hide_schema_values_and_unknown_field_names() -> None:
    marker = "private-evidence-value"
    unknown_field = "private-field-name"
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            unknown_field: {"type": "string", "enum": [marker]},
            "sections": {"type": "array", "items": {"type": "string", "enum": [marker]}},
        },
        "required": [unknown_field, "sections"],
        "additionalProperties": False,
    }
    with pytest.raises(DeepSeekProviderError) as error:
        validate_json_schema({unknown_field: "bad", "sections": ["bad"] * 30}, schema)
    violations = error.value.schema_violations
    assert len(violations) == 16
    assert violations[0].path == "/*"
    assert violations[1].path == "/sections/0"
    assert all(item.rule == "ENUM_VALUE" for item in violations)
    assert marker not in repr(violations)
    assert unknown_field not in repr(violations)


def test_unique_field_and_child_failure_are_both_in_the_only_repair_feedback() -> None:
    schema = _unique_section_schema()
    properties = cast(dict[str, JsonValue], schema["properties"])
    items = cast(dict[str, JsonValue], properties["items"])
    child = cast(dict[str, JsonValue], items["items"])
    child_properties = cast(dict[str, JsonValue], child["properties"])
    child_properties["summary"] = {"type": "string"}
    with pytest.raises(DeepSeekProviderError) as error:
        validate_json_schema(
            {"items": [{"kind": "bull_case", "summary": 1}, {"kind": "bull_case"}]},
            schema,
        )
    assert {(item.path, item.rule) for item in error.value.schema_violations} == {
        ("/items/0/summary", "JSON_TYPE"),
        ("/items", "UNIQUE_FIELD"),
    }


@pytest.mark.asyncio
async def test_missing_counter_selection_does_not_spend_an_advisory_repair(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request, draft = await _grounded_narrative_request()
    draft.hypotheses[0].counter_fact_keys = []
    transport = _FakeTransport([_response(draft.model_dump(mode="json"))])
    result = await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
        request
    )
    assert len(transport.requests) == 1
    assert result.payload == draft.model_dump(mode="json")
    assert "COUNTER_BASIS_INCOMPLETE" not in caplog.text
    assert draft.hypotheses[0].statement not in caplog.text


@pytest.mark.asyncio
async def test_counter_basis_does_not_add_another_repair_after_schema_repair() -> None:
    request, draft = await _grounded_narrative_request()
    draft.hypotheses[0].counter_fact_keys = []
    transport = _FakeTransport([_response({}), _response(draft.model_dump(mode="json"))])
    result = await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
        request
    )
    assert len(transport.requests) == 2
    assert result.payload == draft.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "现金流与利润口径不一。",
        "公司收入来源单一。",
        "利润主要来源之一。",
        "成本结构单一。",
        "公司收入來源單一。",
    ],
)
async def test_qualitative_financial_reason_does_not_consume_score_repair(reason: str) -> None:
    assert TypeAdapter(ModelAuthoredScoreReason).validate_python(reason) == reason
    payload = _score_assessment_payload(reason=reason)
    schema = ResearchScoreAssessmentDraft.model_json_schema(mode="validation")
    transport = _FakeTransport([_response(payload)])
    result = await DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport).generate(
        _request(task_type="research_score_assessment", response_schema=schema)
    )
    assert len(transport.requests) == 1
    assert result.payload == payload
    assert not result.degraded
    assert ResearchScoreAssessmentDraft.model_validate(result.payload).financial_quality is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_succeeds", [True, False])
@pytest.mark.parametrize(
    "refusal",
    [
        "本次证据未覆盖相关风险\uff0c无法形成有事实支撑的看空论点。",
        "本次证据未覆盖毛利率及负债信息\uff1b本次证据未覆盖偿债指标\uff0c无法评估偿债能力。",
        "无法确认经营趋势\uff0c不能评估现金回收质量。",
    ],
)
async def test_missing_argument_declaration_is_rejected_by_content_audit(
    repair_succeeds: bool,
    refusal: str,
) -> None:
    request, valid = await _grounded_narrative_request()
    invalid = valid.model_dump(mode="json")
    index = next(
        i for i, h in enumerate(valid.hypotheses) if h.section is ResearchSectionKind.BEAR_CASE
    )
    cast(list[dict[str, JsonValue]], invalid["hypotheses"])[index]["statement"] = refusal
    # The same bad and corrected candidates are checked at the content boundary.
    payload = valid.model_dump(mode="json") if repair_succeeds else invalid
    transport = _FakeTransport([_response(payload)])
    provider = DeepSeekLLMProvider(model=DEEPSEEK_PRO_MODEL, transport=transport)
    result = await provider.generate(request)
    candidate = ResearchNarrativeCandidate.model_validate(result.payload)
    assert result.payload == payload
    assert len(transport.requests) == 1
    packet = synthetic_evidence_packet(
        instrument_id="XSHG:603986", instrument_name="兆易创新", as_of=NOW
    )
    rejected = reporting_module._scope_rejected_fields(
        packet, reporting_module._bind_narrative(packet, candidate), None
    )
    assert ("statement" not in rejected.get(f"hypothesis:{index}", {})) is repair_succeeds
