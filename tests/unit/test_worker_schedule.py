from apps.worker.schedule_policy import schedule_doctor

from stock_assistant.config.yaml_config import load_versioned_config


def test_r1_compatibility_and_r2_global_refresh_are_the_only_enabled_jobs() -> None:
    jobs = load_versioned_config("configs/schedules.yaml").data["jobs"]
    enabled = {name for name, spec in jobs.items() if spec["enabled"]}

    assert enabled == {
        "policy_refresh",
        "premarket_ingest",
        "premarket_brief",
        "behavior_intraday",
        "behavior_post_close",
    }
    for job_name in {
        "quote_snapshot",
        "sector_rotation",
        "postmarket_outcome",
    }:
        assert jobs[job_name]["enabled"] is False
        assert jobs[job_name]["stage"] != "R1"
        assert jobs[job_name]["reason"] == "FEATURE_NOT_ENABLED_FOR_STAGE"
    refresh = jobs["policy_refresh"]
    assert refresh["stage"] == "R2"
    assert refresh["interval_seconds"] == 3600
    assert refresh["instrument_parameter"] is False
    assert refresh["watchlist_parameter"] is False


def test_runtime_request_factory_passes_schedule_doctor() -> None:
    assert schedule_doctor() == ()


def test_enabled_jobs_declare_complete_asia_shanghai_windows() -> None:
    config = load_versioned_config("configs/schedules.yaml").data
    assert config["timezone"] == "Asia/Shanghai"

    expected = {
        "premarket_ingest": (
            "premarket",
            "previous_cn_trading_day_15:00",
            "current_cn_trading_day_08:55",
        ),
        "premarket_brief": (
            "premarket",
            "previous_cn_trading_day_15:00",
            "current_cn_trading_day_08:55",
        ),
        "behavior_intraday": (
            "intraday",
            "current_complete_trading_window_minus_10_minutes",
            "current_complete_trading_window",
        ),
        "behavior_post_close": (
            "post_close",
            "current_cn_trading_day_14:50",
            "current_cn_trading_day_15:00",
        ),
    }
    for job_name, (session, start, end) in expected.items():
        spec = config["jobs"][job_name]
        assert spec["session"] == session
        assert "active_target_revision" in spec["idempotency_scope"]
        assert spec["official_calendar_required"] is True
        assert spec["active_target_revision_required"] is True
        assert spec["complete_window_required"] is True
        assert spec["window"]["timezone"] == "Asia/Shanghai"
        assert spec["window"]["start"] == start
        assert spec["window"]["end"] == end
