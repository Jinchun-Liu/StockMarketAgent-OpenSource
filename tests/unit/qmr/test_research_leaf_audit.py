"""Replay the sealed, synthetic eight-request comparison without network calls."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.research import (
    ResearchNarrativeDraft,
    ResearchNarrativeHypothesis,
    ResearchNarrativeSection,
    ResearchSupportAssessment,
    ResearchSupportLeafAssessment,
)
from stock_assistant.contracts.research_reader import (
    ResearchScoreCandidate,
    ResearchScoreDimensionId,
)
from stock_assistant.modules.research.reporting import _aggregate_support_leaves

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "research"
GOLD = json.loads((FIXTURES / "reliability-audit-pairs.v1.json").read_text())
RESPONSES = json.loads((FIXTURES / "reliability-audit-responses.v1.json").read_text())


def _inputs(group):
    hypotheses = []
    sections = []
    score = None
    for item in group["items"]:
        if "hypothesis" in item:
            value = deepcopy(item["hypothesis"])
            value["supporting_evidence_ids"] = [b["evidence_id"] for b in value["supporting_basis"]]
            value["counter_evidence_ids"] = [b["evidence_id"] for b in value["counter_basis"]]
            hypotheses.append(ResearchNarrativeHypothesis.model_validate(value))
        elif "section" in item:
            sections.append(
                ResearchNarrativeSection.model_validate(
                    {k: v for k, v in item["section"].items() if k != "claim_ids"}
                )
            )
        else:
            selection = {
                "grade": item["assessment"]["grade"],
                "reasons": item["assessment"]["reasons"],
            }
            score = ResearchScoreCandidate.model_validate(
                {"schema_version": "2.0", **{d.value: selection for d in ResearchScoreDimensionId}}
            )
    # These are independent fixed alternatives, intentionally sharing a section
    # kind. Every item is typed; this comparison is not a publishable report.
    draft = ResearchNarrativeDraft(schema_version="1.0").model_copy(
        update={"hypotheses": hypotheses, "sections": sections}
    )
    return draft, score


def test_sealed_comparison_inputs_and_budget() -> None:
    assert (
        hashlib.sha256((FIXTURES / "reliability-audit-pairs.v1.json").read_bytes()).hexdigest()
        == RESPONSES["gold_sha256"]
    )
    assert RESPONSES["physical_requests"] == RESPONSES["logical_calls"] == 8
    assert RESPONSES["structure_repairs"] == RESPONSES["transport_retries"] == 0
    assert len(RESPONSES["results"]) == 8


@pytest.mark.parametrize("group", GOLD["groups"], ids=lambda group: group["id"])
def test_fixed_leaf_outcomes_and_legacy_false_rejections(group) -> None:
    draft, score = _inputs(group)
    rows = {r["variant"]: r for r in RESPONSES["results"] if r["group"] == group["id"]}
    current = _aggregate_support_leaves(
        ResearchSupportLeafAssessment.model_validate(rows["leaf"]["payload"]),
        draft,
        score,
        set(group["expected"]),
    )
    assert current.schema_version == "3.2"
    assert {v.item_id: v.supported for v in current.verdicts} == group["expected"]
    assert ResearchSupportAssessment.model_validate_json(current.model_dump_json()) == current
    old = ResearchSupportAssessment.model_validate(rows["legacy"]["payload"])
    assert {v.item_id: v.supported for v in old.verdicts} == rows["legacy"]["verdicts"]
    assert rows["legacy"]["false_accept"] == rows["leaf"]["false_accept"] == []
    assert rows["leaf"]["false_reject"] == []
    assert rows["legacy"]["false_reject"] == (
        ["hypothesis:0", "hypothesis:1"] if group["id"] == "conditional_premise" else []
    )
    # Ordering is presentation only; IDs and criteria determine association.
    shuffled = deepcopy(rows["leaf"]["payload"])
    shuffled["verdicts"].reverse()
    for item in shuffled["verdicts"]:
        item["field_checks"].reverse()
    reordered = _aggregate_support_leaves(
        ResearchSupportLeafAssessment.model_validate(shuffled),
        draft,
        score,
        set(group["expected"]),
    )
    assert {v.item_id: v.supported for v in reordered.verdicts} == group["expected"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_item",
        "duplicate_item",
        "unknown_item",
        "missing_leaf",
        "duplicate_leaf",
        "unknown_criterion",
        "unknown_reason",
        "wrong_quote",
        "invented_field",
        "aggregate_flag",
        "leaf_flag",
        "legacy_wire",
    ],
)
def test_invalid_leaf_response_cannot_be_aggregated(fault: str) -> None:
    group = GOLD["groups"][0]
    row = next(
        r for r in RESPONSES["results"] if r["group"] == group["id"] and r["variant"] == "leaf"
    )
    payload = deepcopy(row["payload"])
    item = payload["verdicts"][0]
    leaf = item["field_checks"][0]
    if fault == "missing_item":
        payload["verdicts"].pop()
    elif fault == "duplicate_item":
        payload["verdicts"].append(deepcopy(item))
    elif fault == "unknown_item":
        item["item_id"] = "hypothesis:99"
    elif fault == "missing_leaf":
        item["field_checks"].pop()
    elif fault == "duplicate_leaf":
        item["field_checks"].append(deepcopy(leaf))
    elif fault == "unknown_criterion":
        leaf["criterion"] = "looks_good"
    elif fault == "unknown_reason":
        leaf["reason_code"] = "PROBABLY"
    elif fault == "wrong_quote":
        leaf["target_quote"] = "这段文字不存在于待审字段。"
    elif fault == "invented_field":
        leaf["field_path"] = "summary"
    elif fault == "aggregate_flag":
        item["supported"] = True
    elif fault == "leaf_flag":
        leaf["supported"] = True
    else:
        payload["schema_version"] = "3.1"
    draft, score = _inputs(group)
    with pytest.raises((ValueError, ValidationError)):
        _aggregate_support_leaves(
            ResearchSupportLeafAssessment.model_validate(payload),
            draft,
            score,
            set(group["expected"]),
        )


def test_legacy_whole_rejection_is_never_recomputed_from_positive_leaves() -> None:
    old = deepcopy(next(r for r in RESPONSES["results"] if r["variant"] == "legacy")["payload"])
    item = old["verdicts"][0]
    item.update(supported=False, reason_code="INFERENCE_UNSUPPORTED")
    for leaf in item["field_checks"]:
        leaf["supported"] = True
    saved = ResearchSupportAssessment.model_validate(old)
    restored = ResearchSupportAssessment.model_validate_json(saved.model_dump_json())
    assert not restored.verdicts[0].supported
    assert all(leaf.supported for leaf in restored.verdicts[0].field_checks)


def test_twenty_conditions_and_counter_basis_fit_the_existing_hypothesis_budget() -> None:
    from stock_assistant.modules.research.reporting import _support_check_keys, _support_fields

    draft, _ = _inputs(GOLD["groups"][3])
    hypothesis = draft.hypotheses[0].model_copy(
        update={"falsification_conditions": [f"第{i}项条件仍需后续证据核对。" for i in range(20)]}
    )
    draft = ResearchNarrativeDraft(schema_version="1.0", hypotheses=[hypothesis])
    fields = _support_fields(draft, None)["hypothesis:0"]
    required = _support_check_keys("hypothesis:0", fields, draft)
    assert len(required) == 24
    response = ResearchSupportLeafAssessment.model_validate(
        {
            "schema_version": "4.0",
            "verdicts": [
                {
                    "item_id": "hypothesis:0",
                    "field_checks": [
                        {
                            "field_path": path,
                            "criterion": criterion,
                            "reason_code": "SUPPORTED",
                            "target_quote": fields[path][:400],
                            "reason": "固定结构容量样例。",
                        }
                        for path, criterion in required
                    ],
                }
            ],
        }
    )
    assert _aggregate_support_leaves(response, draft, None, {"hypothesis:0"}).verdicts[0].supported
