"""Port for deterministic, auditable source-quality scoring."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.quality import QualityAssessmentRequest, QualityAssessmentResult


@runtime_checkable
class ContentQualityPort(Protocol):
    def assess(self, request: QualityAssessmentRequest) -> QualityAssessmentResult: ...
