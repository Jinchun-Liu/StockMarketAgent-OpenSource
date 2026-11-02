from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_assistant.common.errors import ApplicationError
from stock_assistant.contracts.research_reader import (
    ResearchScoreBasis,
    ResearchScorecard,
    ResearchScoreDimension,
    ResearchScoreDimensionId,
    ResearchScoreStatus,
)
from stock_assistant.contracts.research_skills import (
    ResearchSkillAvailability,
    ResearchSkillId,
    ResearchSkillMode,
    ResearchSkillSelection,
)
from stock_assistant.modules.research.skills import (
    METHOD_LIBRARY_VERSION,
    build_skill_catalog,
    compile_method_prompt,
    resolve_skill_selection,
)


def test_catalog_contains_21_methods_and_nine_fixed_core_methods() -> None:
    catalog = build_skill_catalog(instrument_id="XSHG:603986")

    assert len(catalog.items) == 21
    assert {item.skill_id for item in catalog.items} == set(ResearchSkillId)
    assert sum(item.is_core for item in catalog.items) == 9
    assert catalog.method_library_version == METHOD_LIBRARY_VERSION
    blocked = {
        item.skill_id
        for item in catalog.items
        if item.availability is ResearchSkillAvailability.BOUNDARY_BLOCKED
    }
    assert blocked == {
        ResearchSkillId.PORTFOLIO_REVIEW,
        ResearchSkillId.PRIVATE_COMPANY_RESEARCH,
    }
    assert ResearchSkillId.INVESTMENT_MEMO_CRAFT in {item.skill_id for item in catalog.items}


def test_manual_selection_keeps_core_limits_optional_methods_and_rejects_boundary_bypass() -> None:
    selected, receipts = resolve_skill_selection(
        instrument_id="XSHG:603986",
        selection=ResearchSkillSelection(
            schema_version="1.0",
            mode=ResearchSkillMode.MANUAL,
            selected_skill_ids=[ResearchSkillId.INDUSTRY_RESEARCH],
        ),
    )

    assert len(selected) == 10
    assert ResearchSkillId.INDUSTRY_RESEARCH in selected
    assert len(receipts) == 11
    with pytest.raises(ValueError):
        ResearchSkillSelection(
            schema_version="1.0",
            mode=ResearchSkillMode.MANUAL,
            selected_skill_ids=list(ResearchSkillId)[:7],
        )
    with pytest.raises(ApplicationError, match="RESEARCH_SKILL_NOT_AVAILABLE"):
        resolve_skill_selection(
            instrument_id="XSHG:603986",
            selection=ResearchSkillSelection(
                schema_version="1.0",
                mode=ResearchSkillMode.MANUAL,
                selected_skill_ids=[ResearchSkillId.PORTFOLIO_REVIEW],
            ),
        )


def test_runtime_prompt_uses_adapted_profiles_and_contains_no_action_language() -> None:
    prompt = compile_method_prompt(
        [ResearchSkillId.INVESTMENT_RESEARCH, ResearchSkillId.QUALITY_SCREEN]
    ).casefold()
    for phrase in (
        "建议买入",
        "建议卖出",
        "加仓",
        "减仓",
        "持仓比例",
        "目标价",
        "收益承诺",
        "buy rating",
        "sell rating",
        "price target",
    ):
        assert phrase not in prompt

    provenance = json.loads(
        Path("resources/research_methods/ai-berkshire/PROVENANCE.json").read_text(encoding="utf-8")
    )
    assert provenance["commit"] == "fd83d06347c6e3ee50133cda6962f40e226b5252"
    assert len(provenance["files"]) == 22
    assert Path("resources/research_methods/ai-berkshire/LICENSE").exists()


def test_synthetic_scorecard_separates_scenario_coefficient_from_source_confidence() -> None:
    scorecard = ResearchScorecard(
        schema_version="1.1",
        scoring_basis=ResearchScoreBasis.SYNTHETIC_SCENARIO,
        status=ResearchScoreStatus.SCORED,
        overall_score=Decimal("75"),
        score_band="综合较强",
        score_meaning="按合成场景设定计算, 不评价真实公司。",
        evidence_confidence=Decimal("0"),
        evidence_confidence_meaning="真实来源核验: 不适用(合成)",
        dimensions=[
            ResearchScoreDimension(
                schema_version="1.0",
                dimension=dimension,
                display_name=dimension.value,
                weight=Decimal(weight),
                evidence_grade=3,
                raw_score=Decimal("75"),
                reliability_factor=Decimal("1"),
                adjusted_score=Decimal("75"),
            )
            for dimension, weight in zip(
                ResearchScoreDimensionId,
                ("0.20", "0.20", "0.15", "0.10", "0.15", "0.20"),
                strict=True,
            )
        ],
        scoring_rule_version="company-quality-score.v3.synthetic",
    )
    assert scorecard.schema_version == "1.1"
    payload = scorecard.model_dump(mode="python")
    with pytest.raises(ValidationError, match="real source confidence"):
        ResearchScorecard.model_validate({**payload, "evidence_confidence": Decimal("100")})
    dimensions = [item.model_dump(mode="python") for item in scorecard.dimensions]
    dimensions[0].update(reliability_factor=Decimal("0.5"), adjusted_score=Decimal("62.5"))
    with pytest.raises(ValidationError, match="scenario coefficient one"):
        ResearchScorecard.model_validate(
            {
                **payload,
                "dimensions": dimensions,
                "overall_score": Decimal("72.5"),
                "score_band": "基础稳健",
            }
        )
    with pytest.raises(ValidationError, match=r"version 1\.1"):
        ResearchScorecard.model_validate({**payload, "schema_version": "1.0"})
    real = {**payload, "schema_version": "1.0", "evidence_confidence": Decimal("100")}
    real.pop("scoring_basis")
    assert ResearchScorecard.model_validate(real).scoring_basis is ResearchScoreBasis.REAL_EVIDENCE


@pytest.mark.parametrize(
    "module",
    [
        "stock_assistant.contracts.research_legacy_sessions",
        "stock_assistant.modules.research.reporting",
    ],
)
def test_current_and_frozen_research_contracts_import_in_a_fresh_process(module: str) -> None:
    # A fresh process catches circular imports masked by pytest's prior module imports.
    result = subprocess.run(  # noqa: S603 - current interpreter and fixed test module names.
        [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
