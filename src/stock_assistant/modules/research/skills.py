"""Project-owned research method catalog and safe model prompt compiler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.runtime_resources import runtime_resource_path
from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.research_skills import (
    ResearchSkillAvailability,
    ResearchSkillCatalog,
    ResearchSkillCatalogItem,
    ResearchSkillExecution,
    ResearchSkillExecutionStatus,
    ResearchSkillGroup,
    ResearchSkillId,
    ResearchSkillMode,
    ResearchSkillSelection,
)

METHOD_LIBRARY_VERSION = "ai-berkshire-fd83d06-stockassistant-1.0"
_PROFILE_RELATIVE_PATH = "research_methods/ai-berkshire/adapted/methods.v1.json"

_FORBIDDEN_RUNTIME_PHRASES = (
    "建议买入",
    "建议卖出",
    "买入建议",
    "卖出建议",
    "加仓",
    "减仓",
    "持仓比例",
    "目标价",
    "收益承诺",
    "buy rating",
    "sell rating",
    "position size",
    "price target",
)


class _AdaptedMethod(ContractModel):
    id: ResearchSkillId
    display_name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=500)
    group: ResearchSkillGroup
    core: bool
    availability: ResearchSkillAvailability
    disabled_reason: str | None = Field(default=None, max_length=500)
    lenses: list[str] = Field(default_factory=list, max_length=8)


class _ReaderOverlay(ContractModel):
    source: str = Field(min_length=1, max_length=500)
    runtime_name: str = Field(min_length=1, max_length=100)
    instructions: list[str] = Field(min_length=1, max_length=10)


class _AdaptedMethodLibrary(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    method_library_version: Literal["ai-berkshire-fd83d06-stockassistant-1.0"]
    runtime_notice: str = Field(min_length=1, max_length=500)
    global_instructions: list[str] = Field(min_length=1, max_length=20)
    methods: list[_AdaptedMethod] = Field(min_length=21, max_length=21)
    reader_overlay: _ReaderOverlay


def load_method_library(path: Path | None = None) -> _AdaptedMethodLibrary:
    """Load only the adapted runtime profiles, never the retained upstream texts."""

    resource = path or runtime_resource_path(_PROFILE_RELATIVE_PATH)
    payload = json.loads(resource.read_text(encoding="utf-8"))
    library = _AdaptedMethodLibrary.model_validate(payload)
    identifiers = [method.id for method in library.methods]
    if len(set(identifiers)) != len(identifiers) or set(identifiers) != set(ResearchSkillId):
        raise ValueError("adapted method library must contain all 21 methods exactly once")
    runtime_text = json.dumps(payload, ensure_ascii=False).casefold()
    if any(phrase in runtime_text for phrase in _FORBIDDEN_RUNTIME_PHRASES):
        raise ValueError("adapted method library contains forbidden action language")
    return library


def build_skill_catalog(
    *, instrument_id: str, has_recent_comparable_report: bool = False
) -> ResearchSkillCatalog:
    library = load_method_library()
    items: list[ResearchSkillCatalogItem] = []
    for method in library.methods:
        availability = method.availability
        disabled_reason = method.disabled_reason
        if method.id is ResearchSkillId.THESIS_DRIFT and has_recent_comparable_report:
            availability = ResearchSkillAvailability.AVAILABLE
            disabled_reason = None
        items.append(
            ResearchSkillCatalogItem(
                schema_version="1.0",
                skill_id=method.id,
                display_name=method.display_name,
                plain_language_purpose=method.purpose,
                group=method.group,
                is_core=method.core,
                availability=availability,
                disabled_reason=disabled_reason,
                method_version=library.method_library_version,
            )
        )
    return ResearchSkillCatalog(
        schema_version="1.0",
        instrument_id=instrument_id,
        method_library_version=library.method_library_version,
        items=items,
    )


def resolve_skill_selection(
    *,
    instrument_id: str,
    selection: ResearchSkillSelection,
    has_recent_comparable_report: bool = False,
) -> tuple[list[ResearchSkillId], list[ResearchSkillExecution]]:
    catalog = build_skill_catalog(
        instrument_id=instrument_id,
        has_recent_comparable_report=has_recent_comparable_report,
    )
    by_id = {item.skill_id: item for item in catalog.items}
    core = [item.skill_id for item in catalog.items if item.is_core]
    if selection.mode is ResearchSkillMode.AUTO:
        requested = [
            item.skill_id
            for item in catalog.items
            if item.availability is ResearchSkillAvailability.AVAILABLE
        ]
    else:
        requested = [*core, *selection.selected_skill_ids]
    selected = list(dict.fromkeys(requested))
    blocked = [
        by_id[skill_id]
        for skill_id in selected
        if by_id[skill_id].availability is not ResearchSkillAvailability.AVAILABLE
    ]
    if selection.mode is ResearchSkillMode.MANUAL and blocked:
        raise ApplicationError(ErrorCode.VALIDATION_ERROR, "RESEARCH_SKILL_NOT_AVAILABLE")
    executions = [
        ResearchSkillExecution(
            schema_version="1.0",
            skill_id=item.skill_id,
            status=(
                ResearchSkillExecutionStatus.BOUNDARY_BLOCKED
                if item.availability is ResearchSkillAvailability.BOUNDARY_BLOCKED
                else ResearchSkillExecutionStatus.NOT_APPLICABLE
            ),
            template_version=METHOD_LIBRARY_VERSION,
            detail=item.disabled_reason or "手动模式未选择此可选方法。",
        )
        for item in catalog.items
        if item.skill_id not in selected
    ]
    return selected, executions


def compile_method_prompt(skill_ids: list[ResearchSkillId]) -> str:
    """Compile bounded method guidance; upstream Markdown is never opened here."""

    library = load_method_library()
    methods = {item.id: item for item in library.methods}
    selected = [methods[skill_id] for skill_id in skill_ids]
    lines = [
        "Project research method library:",
        *[f"- {instruction}" for instruction in library.global_instructions],
        "Apply these evidence lenses:",
    ]
    for method in selected:
        lenses = ", ".join(method.lenses) if method.lenses else "boundary record only"
        lines.append(f"- {method.display_name}: {method.purpose} Lenses: {lenses}.")
    prompt = "\n".join(lines)
    if any(phrase in prompt.casefold() for phrase in _FORBIDDEN_RUNTIME_PHRASES):
        raise ValueError("compiled method prompt contains forbidden action language")
    return prompt


__all__ = [
    "METHOD_LIBRARY_VERSION",
    "build_skill_catalog",
    "compile_method_prompt",
    "load_method_library",
    "resolve_skill_selection",
]
