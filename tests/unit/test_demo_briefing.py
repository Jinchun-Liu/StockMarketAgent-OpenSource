from apps.demo_briefing import build_synthetic_premarket_demo

from stock_assistant.contracts.briefings import (
    BriefingChangeType,
    BriefingKind,
    SourceHealthStatus,
)


def test_synthetic_demo_is_deterministic_and_point_in_time_safe() -> None:
    first = build_synthetic_premarket_demo()
    second = build_synthetic_premarket_demo()

    assert first == second
    assert first.model_dump_json(indent=2) == second.model_dump_json(indent=2)
    assert first.kind is BriefingKind.PREMARKET
    assert all(item.first_seen_at <= first.as_of for item in first.items)


def test_synthetic_demo_shows_correction_without_model_forecast() -> None:
    briefing = build_synthetic_premarket_demo()

    assert len(briefing.items) == 1
    item = briefing.items[0]
    assert item.change_type is BriefingChangeType.CORRECTION
    assert item.inference is None
    assert item.forecast is None
    assert item.evidence_ids
    assert item.source_ids == ["synthetic_exchange"]
    assert briefing.model_version == "deterministic-evidence-only"


def test_synthetic_demo_makes_rights_gap_explicit() -> None:
    briefing = build_synthetic_premarket_demo()
    health = {item.source_id: item for item in briefing.source_health}

    assert health["synthetic_exchange"].status is SourceHealthStatus.OK
    assert health["cninfo"].status is SourceHealthStatus.BLOCKED_RIGHTS
    assert health["cninfo"].last_success_at is None
    assert "未配置授权" in (health["cninfo"].gap_reason or "")
