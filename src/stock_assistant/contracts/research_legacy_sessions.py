"""Lossless legacy session projections; new claim semantics require current read endpoints."""

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field

from stock_assistant.contracts.research import ResearchAvailableResult as CurrentAvailableResult
from stock_assistant.contracts.research import ResearchClaimV1 as ResearchClaim
from stock_assistant.contracts.research import ResearchSessionPage as CurrentSessionPage
from stock_assistant.contracts.research import ResearchSessionView as CurrentSessionView


class ResearchAvailableResult(CurrentAvailableResult):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    claims: Sequence[ResearchClaim] = Field(default_factory=list, max_length=2000)


class ResearchSessionView(CurrentSessionView):
    available_result: ResearchAvailableResult | None = None

    @classmethod
    def from_current(cls, current: CurrentSessionView) -> Self:
        payload = current.model_dump(mode="python")
        available = current.available_result
        if available is not None and available.schema_version == "2.0":
            payload["available_result"] = {
                **available.model_dump(mode="python"),
                "schema_version": "1.1",
                "claims": [
                    {**claim.model_dump(mode="python"), "schema_version": "1.0"}
                    for claim in available.claims
                ],
            }
        # Frozen nested validators reject counterless hypotheses; no content is removed.
        return cls.model_validate(payload)


class ResearchSessionPage(CurrentSessionPage):
    items: Sequence[ResearchSessionView] = Field(default_factory=list, max_length=100)

    @classmethod
    def from_current(cls, current: CurrentSessionPage) -> Self:
        payload = current.model_dump(mode="python")
        payload["items"] = [ResearchSessionView.from_current(item) for item in current.items]
        return cls.model_validate(payload)
