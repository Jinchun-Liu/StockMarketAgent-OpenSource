"""Contract freeze for the unified R2 global-event boundary."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.common import MutationContext, OperationContext
from stock_assistant.contracts.global_events import (
    PolicyRefreshRequest,
    ResearchExposureDirectness,
    ResearchExposureEdge,
    ResearchImpactChannel,
)
from stock_assistant.contracts.persistence import MarketEventQueryV1_1
from stock_assistant.platform.research.synthetic_global_events import (
    synthetic_global_event_manifest_sha256,
    synthetic_global_events,
)

NOW = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)


def test_r2_global_event_enums_are_frozen() -> None:
    assert {item.value for item in ResearchExposureDirectness} == {
        "official_direct_naming",
        "company_disclosed",
        "one_hop_evidence",
        "industry_background",
    }
    assert {item.value for item in ResearchImpactChannel} == {
        "export_demand",
        "input_cost",
        "commodity",
        "fx",
        "interest_rate",
        "capital_flow",
        "sanctions",
        "logistics",
        "technology_control",
        "subsidy",
        "domestic_substitution",
        "inflation_policy",
    }


def test_policy_refresh_request_has_no_stock_or_watchlist_identity() -> None:
    request = PolicyRefreshRequest(
        schema_version="1.0",
        context=MutationContext(
            schema_version="1.0",
            as_of=NOW,
            requested_at=NOW,
            idempotency_key="global-event-refresh-2026-08-27-04",
        ),
        scheduled_for=NOW,
        fixture_manifest_sha256="a" * 64,
    )

    assert set(PolicyRefreshRequest.model_fields) == {
        "schema_version",
        "context",
        "scheduled_for",
        "source_mode",
        "fixture_manifest_version",
        "fixture_manifest_sha256",
    }
    wire = request.model_dump(mode="json")
    assert "instrument_id" not in wire
    assert "watchlist" not in wire
    assert "active_target" not in wire


def test_synthetic_global_catalog_is_stable_and_stock_agnostic() -> None:
    events = synthetic_global_events()

    assert synthetic_global_event_manifest_sha256(events) == (
        synthetic_global_event_manifest_sha256(synthetic_global_events())
    )
    assert len(synthetic_global_event_manifest_sha256(events)) == 64
    assert all(not event.security_ids for event in events)
    assert all(not event.company_ids for event in events)


def test_exposure_paths_reject_two_hops_and_directness_mismatch() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        ResearchExposureEdge(
            from_entity_id="company:test",
            to_entity_id="industry:test",
            directness=ResearchExposureDirectness.ONE_HOP_EVIDENCE,
            hop_count=2,
            evidence_ids=["00000000-0000-0000-0000-000000000001"],
        )
    with pytest.raises(ValidationError, match="one-hop exposure requires"):
        ResearchExposureEdge(
            from_entity_id="company:test",
            to_entity_id="industry:test",
            directness=ResearchExposureDirectness.ONE_HOP_EVIDENCE,
            hop_count=0,
            evidence_ids=["00000000-0000-0000-0000-000000000001"],
        )


def test_market_event_query_v1_1_preserves_pit_cutoff() -> None:
    context = OperationContext(
        schema_version="1.0",
        as_of=NOW,
        requested_at=NOW,
    )
    query = MarketEventQueryV1_1(
        schema_version="1.1",
        context=context,
        industry_ids=["industry:semiconductor"],
        revision_known_until=NOW,
    )
    assert query.industry_ids == ["industry:semiconductor"]

    with pytest.raises(ValidationError, match=r"cannot be later than context\.as_of"):
        MarketEventQueryV1_1(
            schema_version="1.1",
            context=context,
            industry_ids=["industry:semiconductor"],
            revision_known_until=NOW + timedelta(seconds=1),
        )
