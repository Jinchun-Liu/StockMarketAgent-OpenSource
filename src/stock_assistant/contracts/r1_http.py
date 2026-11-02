"""Frozen R1 HTTP route-to-contract mappings without handler maturity claims."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from stock_assistant.contracts.event_views import (
    EventEvidenceView,
    EventEvidenceViewV1_1,
    EventEvidenceViewV1_2,
)
from stock_assistant.contracts.ingestion import (
    SourceAccessChannelV1_2,
    SourceEvidenceRole,
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceQualityTier,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingView,
    PremarketBriefingViewV1_1,
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)

R1_SOURCE_PROVENANCE_MEDIA_TYPE = "application/vnd.stockassistant.r1-source-provenance.v1+json"
R1_SOURCE_PROVENANCE_MEDIA_TYPE_V1_2 = (
    "application/vnd.stockassistant.r1-source-provenance.v1.2+json"
)
R1_RESEARCH_PRODUCT_MEDIA_TYPE_V1_3 = "application/vnd.stockassistant.r1-research-product.v1.3+json"

PremarketBriefingHttpData = Annotated[
    PremarketBriefingView
    | PremarketBriefingViewV1_1
    | PremarketBriefingViewV1_2
    | PremarketBriefingViewV1_3,
    Field(discriminator="schema_version"),
]
EventEvidenceHttpData = Annotated[
    EventEvidenceView | EventEvidenceViewV1_1 | EventEvidenceViewV1_2,
    Field(discriminator="schema_version"),
]


def _project_source_provenance_v1_1(
    value: SourceProvenanceV1_2,
) -> SourceProvenance | None:
    """Project only 1.2 combinations whose added semantics are deterministic in 1.1.

    Public-static, replay rights, free official/web sources, BaoStock, discovery and
    cross-check provenance cannot be represented without dropping a material 1.2
    distinction, so callers must keep serving 412 for those combinations.
    """

    dev_live_is_reconstructable = (
        value.runtime_mode is SourceRuntimeModeV1_2.DEV_LIVE
        and value.access_channel is SourceAccessChannelV1_2.AKSHARE
        and value.quality_tier is SourceQualityTier.FREE_STRUCTURED
        and value.evidence_role is SourceEvidenceRole.STRUCTURED_HELPER
    )
    authorized_is_reconstructable = (
        value.runtime_mode is SourceRuntimeModeV1_2.AUTHORIZED
        and value.access_channel is SourceAccessChannelV1_2.AUTHORIZED_API
        and value.quality_tier is SourceQualityTier.AUTHORIZED
        and value.evidence_role is SourceEvidenceRole.AUTHORITATIVE
    )
    if not (dev_live_is_reconstructable or authorized_is_reconstructable):
        return None

    payload = value.model_dump(mode="json")
    payload["schema_version"] = "1.1"
    payload.pop("quality_tier")
    payload.pop("evidence_role")
    return SourceProvenance.model_validate(payload)


def project_premarket_briefing_v1_1(
    value: PremarketBriefingViewV1_2 | PremarketBriefingViewV1_3,
) -> PremarketBriefingViewV1_1 | None:
    """Losslessly project a 1.2 briefing, or abstain when 1.1 cannot express it."""

    stable = project_premarket_briefing_v1_2(value)
    primary = _project_source_provenance_v1_1(stable.ingestion_progress.provenance)
    coverage = [_project_source_provenance_v1_1(item.provenance) for item in stable.source_coverage]
    if primary is None or any(item is None for item in coverage):
        return None
    payload = stable.model_dump(mode="json")
    payload["schema_version"] = "1.1"
    payload["ingestion_progress"]["provenance"] = primary.model_dump(mode="json")
    for item, provenance in zip(payload["source_coverage"], coverage, strict=True):
        assert provenance is not None
        item["provenance"] = provenance.model_dump(mode="json")
    return PremarketBriefingViewV1_1.model_validate(payload)


def project_premarket_briefing_v1_2(
    value: PremarketBriefingViewV1_2 | PremarketBriefingViewV1_3,
) -> PremarketBriefingViewV1_2:
    """Project 1.3 to its complete 1.2 subset without changing old semantics."""

    if isinstance(value, PremarketBriefingViewV1_3):
        payload = value.model_dump(mode="json")
        payload["schema_version"] = "1.2"
        for field in (
            "attention_risk_index",
            "behavior_pressure_summary",
            "plain_language_update",
            "flash_interpretation",
            "pro_interpretation",
        ):
            payload.pop(field)
        return PremarketBriefingViewV1_2.model_validate(payload)
    return value


def project_event_evidence_v1_1(
    value: EventEvidenceViewV1_2,
) -> EventEvidenceViewV1_1 | None:
    """Losslessly project 1.2 evidence, or abstain when 1.1 cannot express it."""

    projected = [_project_source_provenance_v1_1(item.provenance) for item in value.evidence]
    if any(item is None for item in projected):
        return None
    payload = value.model_dump(mode="json")
    payload["schema_version"] = "1.1"
    for item, provenance in zip(payload["evidence"], projected, strict=True):
        assert provenance is not None
        item["provenance"] = provenance.model_dump(mode="json")
    return EventEvidenceViewV1_1.model_validate(payload)


def project_premarket_briefing_v1_0(
    value: PremarketBriefingHttpData,
) -> PremarketBriefingView:
    """Return the stable default HTTP representation without 1.1-only lineage."""

    if isinstance(
        value,
        (PremarketBriefingViewV1_1, PremarketBriefingViewV1_2, PremarketBriefingViewV1_3),
    ):
        stable: PremarketBriefingViewV1_1 | PremarketBriefingViewV1_2
        stable = (
            project_premarket_briefing_v1_2(value)
            if isinstance(value, PremarketBriefingViewV1_3)
            else value
        )
        payload = stable.model_dump(mode="json")
        payload["schema_version"] = "1.0"
        payload.pop("runtime_mode", None)
        payload.pop("release_tier", None)
        payload["ingestion_progress"].pop("provenance", None)
        for coverage in payload["source_coverage"]:
            coverage.pop("provenance", None)
        return PremarketBriefingView.model_validate(payload)
    return value


def project_event_evidence_v1_0(value: EventEvidenceHttpData) -> EventEvidenceView:
    """Return the stable default evidence representation without 1.1-only lineage."""

    if isinstance(value, (EventEvidenceViewV1_1, EventEvidenceViewV1_2)):
        payload = value.model_dump(mode="json")
        payload["schema_version"] = "1.0"
        payload.pop("runtime_mode", None)
        for evidence in payload["evidence"]:
            evidence.pop("event_time", None)
            evidence.pop("provenance", None)
        return EventEvidenceView.model_validate(payload)
    return value


@dataclass(frozen=True, slots=True)
class HttpProblemContract:
    """One stable RFC 9457 status/code pair exposed by a route."""

    status: int
    code: str


ACTIVE_TARGET_REVISION_CONFLICT = HttpProblemContract(
    status=409,
    code="ACTIVE_TARGET_REVISION_CONFLICT",
)
IDEMPOTENCY_KEY_REUSE_CONFLICT = HttpProblemContract(
    status=409,
    code="IDEMPOTENCY_KEY_REUSE_CONFLICT",
)


@dataclass(frozen=True, slots=True)
class HttpContractRoute:
    method: str
    path: str
    request_contract: str | None
    response_data_contract: str
    request_binding: Literal["none", "body", "query"] = "none"
    success_envelope: str = "stock_assistant.contracts.api.ApiResponse"
    error_contract: str = "stock_assistant.contracts.api.ProblemDetails"
    error_media_type: str = "application/problem+json"
    requires_idempotency_key: bool = False
    idempotency_key_header: str | None = None
    problems: tuple[HttpProblemContract, ...] = ()


R1_HTTP_CONTRACT_ROUTES = (
    HttpContractRoute(
        method="GET",
        path="/api/v1/active-target",
        request_contract=None,
        response_data_contract="stock_assistant.contracts.active_targets.ActiveTargetState",
    ),
    HttpContractRoute(
        method="PUT",
        path="/api/v1/active-target",
        request_contract="stock_assistant.contracts.active_targets.ActiveTargetSelectionRequest",
        response_data_contract=(
            "stock_assistant.contracts.active_targets.ActiveTargetMutationResult"
        ),
        request_binding="body",
        requires_idempotency_key=True,
        idempotency_key_header="Idempotency-Key",
        problems=(
            ACTIVE_TARGET_REVISION_CONFLICT,
            IDEMPOTENCY_KEY_REUSE_CONFLICT,
        ),
    ),
    HttpContractRoute(
        method="GET",
        path="/api/v1/briefings/premarket/latest",
        request_contract=None,
        response_data_contract="stock_assistant.contracts.r1_http.PremarketBriefingHttpData",
    ),
    HttpContractRoute(
        method="GET",
        path="/api/v1/events/{event_id}/evidence",
        request_contract=("stock_assistant.contracts.event_views.EventEvidenceRevisionSelector"),
        response_data_contract="stock_assistant.contracts.r1_http.EventEvidenceHttpData",
        request_binding="query",
    ),
)
