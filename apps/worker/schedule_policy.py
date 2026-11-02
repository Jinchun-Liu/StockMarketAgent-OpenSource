"""Pure validation for the R0P schedule policy without runtime settings side effects."""

from __future__ import annotations

from collections.abc import Mapping

from stock_assistant.config.yaml_config import load_versioned_config

TIMEZONE = "Asia/Shanghai"
R1_JOBS = (
    "premarket_ingest",
    "premarket_brief",
    "behavior_intraday",
    "behavior_post_close",
)
GLOBAL_EVENT_JOBS = ("policy_refresh",)
DEFERRED_JOBS = (
    "quote_snapshot",
    "sector_rotation",
    "postmarket_outcome",
)
DISPATCH_TIMES = {
    "premarket_ingest": ("08:55",),
    "premarket_brief": ("09:05",),
    "behavior_intraday": ("每个完整10分钟窗口",),
    "behavior_post_close": ("15:10",),
}
JOB_WINDOWS = {
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
CONTINUOUS_PREVIEW_OPERATIONS = {
    "ingest": ("08:55", "cron(55 8 ? * MON-FRI *)"),
    "publish": ("09:05", "cron(5 9 ? * MON-FRI *)"),
    "catch_up": ("09:20", "cron(20 9 ? * MON-FRI *)"),
    "behavior_intraday": ("每个完整10分钟窗口", "cron(0/10 9-15 ? * MON-FRI *)"),
    "behavior_post_close": ("15:10", "cron(10 15 ? * MON-FRI *)"),
}


class SchedulePolicyError(ValueError):
    """The schedule file would permit an unsafe or incomplete dispatch."""


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SchedulePolicyError(f"{path} must be a mapping")
    return value


def _boolean(spec: Mapping[str, object], field: str, path: str) -> bool:
    value = spec.get(field)
    if not isinstance(value, bool):
        raise SchedulePolicyError(f"{path}.{field} must be a boolean")
    return value


def _validate_enabled_job(job_name: str, spec: Mapping[str, object]) -> None:
    path = f"jobs.{job_name}"
    if spec.get("stage") != "R1":
        raise SchedulePolicyError(f"{path}.stage must be R1")
    expected_session, expected_start, expected_end = JOB_WINDOWS[job_name]
    if spec.get("session") != expected_session:
        raise SchedulePolicyError(f"{path}.session must be {expected_session}")
    for field in (
        "official_calendar_required",
        "active_target_revision_required",
        "complete_window_required",
    ):
        if not _boolean(spec, field, path):
            raise SchedulePolicyError(f"{path}.{field} must be true")

    window = _mapping(spec.get("window"), f"{path}.window")
    if window.get("timezone") != TIMEZONE:
        raise SchedulePolicyError(f"{path}.window.timezone must be {TIMEZONE}")
    if window.get("start") != expected_start or window.get("end") != expected_end:
        raise SchedulePolicyError(f"{path}.window must define the complete frozen window")

    at = spec.get("at")
    if not isinstance(at, list) or tuple(at) != DISPATCH_TIMES[job_name]:
        raise SchedulePolicyError(f"{path}.at must match the frozen premarket dispatch time")
    idempotency_scope = spec.get("idempotency_scope")
    if not isinstance(idempotency_scope, str) or "active_target_revision" not in idempotency_scope:
        raise SchedulePolicyError(f"{path}.idempotency_scope must include active_target_revision")

    request_factory = _mapping(spec.get("request_factory"), f"{path}.request_factory")
    ready = request_factory.get("ready")
    if not isinstance(ready, bool):
        raise SchedulePolicyError(f"{path}.request_factory.ready must be a boolean")
    if not ready:
        raise SchedulePolicyError(
            f"{path} cannot be enabled until its scheduler-side request factory is ready"
        )
    if request_factory.get("mode") != "synthetic_development_only":
        raise SchedulePolicyError(f"{path}.request_factory.mode must be synthetic_development_only")


def _validate_global_event_job(spec: Mapping[str, object]) -> None:
    path = "jobs.policy_refresh"
    if spec.get("stage") != "R2":
        raise SchedulePolicyError(f"{path}.stage must be R2")
    if spec.get("interval_seconds") != 3600:
        raise SchedulePolicyError(f"{path}.interval_seconds must be 3600")
    if spec.get("idempotency_scope") != "source_time_bucket_and_manifest":
        raise SchedulePolicyError(f"{path}.idempotency_scope is invalid")
    if spec.get("instrument_parameter") is not False:
        raise SchedulePolicyError(f"{path}.instrument_parameter must be false")
    if spec.get("watchlist_parameter") is not False:
        raise SchedulePolicyError(f"{path}.watchlist_parameter must be false")
    request_factory = _mapping(spec.get("request_factory"), f"{path}.request_factory")
    if request_factory.get("ready") is not True:
        raise SchedulePolicyError(f"{path}.request_factory.ready must be true")
    if request_factory.get("mode") != "synthetic_development_only":
        raise SchedulePolicyError(f"{path}.request_factory.mode must be synthetic_development_only")


def _validate_schedule() -> tuple[tuple[str, str], ...]:
    document = _mapping(
        load_versioned_config("configs/schedules.yaml").data,
        "configs/schedules.yaml",
    )
    if document.get("timezone") != TIMEZONE:
        raise SchedulePolicyError(f"timezone must be {TIMEZONE}")
    if document.get("calendar") != "cn_exchange_official":
        raise SchedulePolicyError("calendar must be cn_exchange_official")
    preview = _mapping(document.get("continuous_preview"), "continuous_preview")
    if preview.get("runtime_mode") != "dev_live":
        raise SchedulePolicyError("continuous_preview.runtime_mode must be dev_live")
    if preview.get("scheduler_timezone") != TIMEZONE:
        raise SchedulePolicyError(f"continuous_preview.scheduler_timezone must be {TIMEZONE}")
    if _boolean(preview, "enabled_by_default", "continuous_preview"):
        raise SchedulePolicyError("continuous_preview must be disabled by default")
    if _boolean(preview, "flexible_time_window", "continuous_preview"):
        raise SchedulePolicyError("continuous_preview flexible time window must be disabled")
    operations = _mapping(preview.get("operations"), "continuous_preview.operations")
    if set(operations) != set(CONTINUOUS_PREVIEW_OPERATIONS):
        raise SchedulePolicyError(
            "continuous_preview operations must match the frozen R1 operations"
        )
    for operation, (at, cron) in CONTINUOUS_PREVIEW_OPERATIONS.items():
        spec = _mapping(operations[operation], f"continuous_preview.operations.{operation}")
        if spec.get("at") != at or spec.get("cron") != cron:
            raise SchedulePolicyError(
                f"continuous_preview.operations.{operation} must match the frozen schedule"
            )

    jobs = _mapping(document.get("jobs"), "jobs")
    if set(jobs) != {*R1_JOBS, *GLOBAL_EVENT_JOBS, *DEFERRED_JOBS}:
        raise SchedulePolicyError("jobs must contain exactly the registered task keys")

    for job_name in R1_JOBS:
        spec = _mapping(jobs[job_name], f"jobs.{job_name}")
        if not _boolean(spec, "enabled", f"jobs.{job_name}"):
            raise SchedulePolicyError(f"jobs.{job_name} must remain enabled for R1")
        _validate_enabled_job(job_name, spec)

    policy_refresh = _mapping(jobs["policy_refresh"], "jobs.policy_refresh")
    if not _boolean(policy_refresh, "enabled", "jobs.policy_refresh"):
        raise SchedulePolicyError("jobs.policy_refresh must be enabled for R2")
    _validate_global_event_job(policy_refresh)

    for job_name in DEFERRED_JOBS:
        spec = _mapping(jobs[job_name], f"jobs.{job_name}")
        if _boolean(spec, "enabled", f"jobs.{job_name}"):
            raise SchedulePolicyError(f"jobs.{job_name} is deferred and must remain disabled")
        if spec.get("stage") in {None, "R1"}:
            raise SchedulePolicyError(f"jobs.{job_name}.stage must name its later owner")
        if spec.get("reason") != "FEATURE_NOT_ENABLED_FOR_STAGE":
            raise SchedulePolicyError(
                f"jobs.{job_name}.reason must be FEATURE_NOT_ENABLED_FOR_STAGE"
            )
    return ()


def schedule_doctor() -> tuple[str, ...]:
    """Return no gaps only after concrete typed requests can be generated."""

    return tuple(message for _, message in _validate_schedule())


__all__ = [
    "TIMEZONE",
    "SchedulePolicyError",
    "schedule_doctor",
]
