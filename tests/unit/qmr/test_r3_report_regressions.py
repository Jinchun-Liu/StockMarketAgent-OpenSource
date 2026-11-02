"""Fixed rejected-premise cases; fake responses only, no model or network calls."""

import json
from copy import deepcopy

import pytest
from tests.unit.qmr import test_research_report_generator as t

from stock_assistant.contracts.intelligence import GenerationRequest, GenerationResponse
from stock_assistant.modules.research.reporting import (
    ResearchGenerationError,
    ResearchReportGenerator,
)

PREMISE = "自动化产线提高生产效率\uff0c支持盈利改善。"


class ExtraPremiseOracle(t._RecordingProvider):
    def __init__(self, dependent: bool) -> None:
        super().__init__()
        self.dependent = dependent

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        response = await super().generate(request)
        payload = t._scenario_draft(response, request)
        if request.task_type == "research_report_narrative":
            extra = deepcopy(payload["hypotheses"][0])
            extra["statement"] = PREMISE
            payload["hypotheses"].append(extra)
            if self.dependent:
                for section in payload["sections"]:
                    if section["kind"] in {"bull_case", "conflicts_and_unknowns"}:
                        section["summary"] = PREMISE
        elif request.task_type == "research_support_assessment":
            data = json.loads(request.messages[-1].content)
            second = all(item["item_id"].startswith("section:") for item in data["items"])
            if second:
                assert all(item["item_id"].startswith("section:") for item in data["items"])
                assert "hypothesis:2" not in json.dumps(data.get("dependency_context", []))
            for verdict in payload["verdicts"]:
                passed = verdict["item_id"] != "hypothesis:2" and not (second and self.dependent)
                verdict.update(
                    supported=passed,
                    reason_code="SUPPORTED" if passed else "INFERENCE_UNSUPPORTED",
                )
                for field in verdict["field_checks"]:
                    field["supported"] = passed
        return response.model_copy(update={"payload": t._scenario_wire(payload, request)})


@pytest.mark.parametrize("dependent", [True, False])
async def test_extra_rejected_premise_requires_summary_reaudit(
    dependent: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(t.reporting_module, "utc_now", lambda: t.NOW)

    provider = ExtraPremiseOracle(dependent)
    session, packet = t._inputs()
    if dependent:
        with pytest.raises(ResearchGenerationError) as caught:
            await ResearchReportGenerator(provider).generate(session=session, packet=packet)
        result = caught.value.available_result
        assert result is not None and PREMISE not in result.model_dump_json()
    else:
        result = await ResearchReportGenerator(provider).generate(session=session, packet=packet)
        assert result.reader_edition is not None
    assert len([r for r in provider.requests if r.task_type == "research_support_assessment"]) == 2
    assert len([r for r in provider.requests if r.task_type == "research_report_narrative"]) == 1


@pytest.mark.parametrize("interrupt_round", [1, 2])
async def test_reserved_unknown_audit_is_not_resent_after_json_restore(
    interrupt_round: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.setattr(t.reporting_module, "utc_now", lambda: t.NOW)
    saved = []

    class InterruptedOracle(t._RecordingProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            response = await super().generate(request)
            payload = t._scenario_draft(response, request)
            if request.task_type == "research_report_narrative":
                payload["hypotheses"].append(deepcopy(payload["hypotheses"][0]))
            elif request.task_type == "research_support_assessment":
                count = len([r for r in self.requests if r.task_type == request.task_type])
                if count == interrupt_round:
                    raise asyncio.CancelledError
                for verdict in payload["verdicts"]:
                    if verdict["item_id"] == "hypothesis:2":
                        verdict.update(supported=False, reason_code="INFERENCE_UNSUPPORTED")
                        for field in verdict["field_checks"]:
                            field["supported"] = False
            return response.model_copy(update={"payload": t._scenario_wire(payload, request)})

    async def save(checkpoint):
        saved.append(checkpoint.model_dump_json())

    provider = InterruptedOracle()
    session, packet = t._inputs()
    with pytest.raises(asyncio.CancelledError):
        await ResearchReportGenerator(provider).generate(
            session=session, packet=packet, save_checkpoint=save
        )
    checkpoint = t.ResearchCheckpoint.model_validate_json(saved[-1])
    assert checkpoint.support_rounds_reserved == interrupt_round
    assert checkpoint.support_pending_ids
    before = len(provider.requests)
    with pytest.raises(ResearchGenerationError, match="MODEL_UNAVAILABLE"):
        await ResearchReportGenerator(provider).generate(
            session=session, packet=packet, checkpoint=checkpoint
        )
    assert len(provider.requests) == before
