"""Application composition for safe R1 DeepSeek interpretation and smoke checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from pydantic import JsonValue

from stock_assistant.config.settings import Settings
from stock_assistant.contracts.behavior import (
    AttentionRiskIndex,
    BehaviorPressureSummary,
    InterpretationOrigin,
    InterpretationStatus,
    ModelInterpretation,
    PlainLanguageUpdate,
)
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.intelligence import (
    GenerationRequest,
    MessageRole,
    PrivacyLevel,
    PromptMessage,
)
from stock_assistant.platform.llm.deepseek import (
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_PRO_MODEL,
    DeepSeekModel,
    DeepSeekProviderError,
    build_deepseek_provider,
)
from stock_assistant.ports.intelligence import LLMProviderPort

_INTERPRETATION_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "summary": {"type": "string", "minLength": 1, "maxLength": 800},
        "key_points": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
            "minItems": 0,
            "maxItems": 5,
        },
    },
    "required": ["title", "summary", "key_points"],
    "additionalProperties": False,
}


def _schema_hash() -> str:
    encoded = json.dumps(
        _INTERPRETATION_SCHEMA,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fallback(
    attention: AttentionRiskIndex,
    behavior: BehaviorPressureSummary,
) -> ModelInterpretation:
    attention_text = "数据不足,暂不评分" if attention.score is None else f"{attention.score}分"
    behavior_text = "数据不足,暂不评分" if behavior.score is None else f"{behavior.score}分"
    return ModelInterpretation(
        title="智能解读暂不可用",
        summary=(
            f"当前信息核实关注度为{attention_text},交易结构压力为{behavior_text}。"
            "模型服务暂不可用,以上分数与状态仍由确定性规则计算,可继续核对证据。"
        ),
        key_points=["模型没有参与分数计算", "请以证据、数据时间和覆盖情况为准"],
        origin=InterpretationOrigin.RULES,
        status=InterpretationStatus.FALLBACK,
    )


def _provider(settings: Settings, model: DeepSeekModel) -> LLMProviderPort:
    if not settings.deepseek_enabled or settings.deepseek_api_key is None:
        raise DeepSeekProviderError("DEEPSEEK_NOT_CONFIGURED")
    return build_deepseek_provider(
        api_key=settings.deepseek_api_key,
        model=model,
        base_url=settings.deepseek_base_url,
    )


async def generate_r1_interpretation(
    *,
    settings: Settings,
    context: OperationContext,
    attention: AttentionRiskIndex,
    behavior: BehaviorPressureSummary,
    update: PlainLanguageUpdate,
    evidence_ids: list[UUID],
    model: DeepSeekModel = DEEPSEEK_FLASH_MODEL,
) -> ModelInterpretation:
    """Generate from public deterministic inputs, falling back without changing facts or scores."""

    try:
        return await _generate_r1_interpretation(
            settings=settings,
            context=context,
            attention=attention,
            behavior=behavior,
            update=update,
            evidence_ids=evidence_ids,
            model=model,
        )
    except DeepSeekProviderError:
        return _fallback(attention, behavior)


async def generate_r1_pro_interpretation(
    *,
    settings: Settings,
    context: OperationContext,
    attention: AttentionRiskIndex,
    behavior: BehaviorPressureSummary,
    update: PlainLanguageUpdate,
    evidence_ids: list[UUID],
) -> ModelInterpretation | None:
    """Return the optional Pro interpretation; provider failure never impersonates Pro."""

    try:
        return await _generate_r1_interpretation(
            settings=settings,
            context=context,
            attention=attention,
            behavior=behavior,
            update=update,
            evidence_ids=evidence_ids,
            model=DEEPSEEK_PRO_MODEL,
        )
    except DeepSeekProviderError:
        return None


async def _generate_r1_interpretation(
    *,
    settings: Settings,
    context: OperationContext,
    attention: AttentionRiskIndex,
    behavior: BehaviorPressureSummary,
    update: PlainLanguageUpdate,
    evidence_ids: list[UUID],
    model: DeepSeekModel,
) -> ModelInterpretation:
    """Run one model request after the caller has selected its failure semantics."""

    public_payload = {
        "instrument_id": "XSHG:603986",
        "attention_risk_index": attention.model_dump(mode="json"),
        "behavior_pressure_summary": behavior.model_dump(mode="json"),
        "plain_language_update": update.model_dump(mode="json"),
    }
    request = GenerationRequest(
        schema_version="1.0",
        task_type="r1_public_research_interpretation",
        messages=[
            PromptMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "Return one JSON object. Explain only the supplied deterministic research "
                    "indices in plain Chinese. Do not calculate or alter scores, invent facts, "
                    "predict prices, or give trading advice."
                ),
            ),
            PromptMessage(
                role=MessageRole.USER,
                content=json.dumps(public_payload, ensure_ascii=False, sort_keys=True),
            ),
        ],
        response_schema=_INTERPRETATION_SCHEMA,
        response_schema_hash=_schema_hash(),
        response_schema_namespace="r1.public_research_interpretation",
        response_schema_owner="qir",
        evidence_ids=evidence_ids,
        privacy_level=PrivacyLevel.PUBLIC,
        context=context,
        timeout_seconds=30 if model == DEEPSEEK_FLASH_MODEL else 90,
    )
    response = await _provider(settings, model).generate(request)
    origin = (
        InterpretationOrigin.DEEPSEEK_PRO
        if model == DEEPSEEK_PRO_MODEL
        else InterpretationOrigin.DEEPSEEK_FLASH
    )
    key_points = response.payload["key_points"]
    if not isinstance(key_points, list):  # local schema validation makes this defensive only
        return _fallback(attention, behavior)
    return ModelInterpretation(
        title=str(response.payload["title"]),
        summary=str(response.payload["summary"]),
        key_points=[str(item) for item in key_points],
        origin=origin,
        status=InterpretationStatus.GENERATED,
    )


@dataclass(frozen=True, slots=True)
class LlmSmokeResult:
    model: str
    response_schema_hash: str
    raw_response_hash: str
    status: str = "succeeded"


async def run_llm_smoke(
    *, settings: Settings, context: OperationContext
) -> tuple[LlmSmokeResult, ...]:
    """Exercise both fixed models with synthetic public JSON and return redacted receipts."""

    results: list[LlmSmokeResult] = []
    for model in (DEEPSEEK_FLASH_MODEL, DEEPSEEK_PRO_MODEL):
        request = GenerationRequest(
            schema_version="1.0",
            task_type="operator_public_json_smoke",
            messages=[
                PromptMessage(role=MessageRole.SYSTEM, content="Return one JSON object only."),
                PromptMessage(role=MessageRole.USER, content='{"check":"public synthetic"}'),
            ],
            response_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            response_schema_hash=hashlib.sha256(
                b'{"additionalProperties":false,"properties":{"ok":{"type":"boolean"}},'
                b'"required":["ok"],"type":"object"}'
            ).hexdigest(),
            response_schema_namespace="operator.llm_smoke",
            response_schema_owner="qir",
            privacy_level=PrivacyLevel.PUBLIC,
            context=context,
            timeout_seconds=30 if model == DEEPSEEK_FLASH_MODEL else 90,
        )
        response = await _provider(settings, model).generate(request)
        results.append(
            LlmSmokeResult(
                model=model,
                response_schema_hash=response.response_schema_hash,
                raw_response_hash=response.raw_response_hash,
            )
        )
    return tuple(results)


__all__ = ["LlmSmokeResult", "generate_r1_interpretation", "run_llm_smoke"]
