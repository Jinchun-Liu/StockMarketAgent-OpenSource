import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from scripts.generate_contract_schemas import SCHEMA_TARGETS

from stock_assistant.contracts.common import InstrumentRef, MutationContext, OperationContext
from stock_assistant.contracts.jobs import JobStatus
from stock_assistant.contracts.persistence import (
    ActiveTargetLookupRequest,
    ActiveTargetLookupResult,
    ActiveTargetWriteRequest,
    ActiveTargetWriteResult,
)
from stock_assistant.contracts.portfolio import (
    ActiveTargetRevisionRef,
    Watchlist,
    WatchlistEntry,
)
from stock_assistant.contracts.premarket_jobs import (
    PREMARKET_BRIEF_JOB_NAME,
    PREMARKET_INGEST_JOB_NAME,
    PremarketBriefJobPayload,
    PremarketBriefJobRequest,
    PremarketBriefJobResult,
    PremarketBriefTaskResult,
    PremarketIngestJobPayload,
    PremarketIngestJobRequest,
    PremarketIngestJobResult,
    PremarketIngestTaskResult,
)
from stock_assistant.ports.persistence import PortfolioRepositoryPort

NOW = datetime(2026, 7, 23, 1, 5, tzinfo=UTC)
WINDOW_START = NOW - timedelta(hours=17, minutes=5)
WINDOW_END = NOW - timedelta(minutes=10)
WATCHLIST_ID = UUID("00000000-0000-0000-0000-000000000101")
INSTRUMENT = InstrumentRef(
    instrument_id="XSHG:600000",
    symbol="600000",
    exchange="XSHG",
    market="CN",
)


def _operation_context() -> OperationContext:
    return OperationContext(
        schema_version="1.0",
        request_id=UUID("00000000-0000-0000-0000-000000000201"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000202"),
        as_of=NOW,
        requested_at=NOW,
    )


def _mutation_context() -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        request_id=UUID("00000000-0000-0000-0000-000000000301"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000302"),
        as_of=NOW,
        requested_at=NOW,
        idempotency_key="r0p-contract-idempotency-0001",
    )


def _active_target(*, revision: int = 2) -> ActiveTargetRevisionRef:
    return ActiveTargetRevisionRef(
        schema_version="1.0",
        watchlist_id=WATCHLIST_ID,
        watchlist_revision=revision,
        instrument=INSTRUMENT,
        activated_at=WINDOW_START,
    )


def _watchlist(*, revision: int = 2) -> Watchlist:
    return Watchlist(
        watchlist_id=WATCHLIST_ID,
        name="Synthetic active target",
        entries=[
            WatchlistEntry(
                instrument=INSTRUMENT,
                added_at=WINDOW_START,
                alert_enabled=True,
            )
        ],
        revision=revision,
        updated_at=WINDOW_END,
    )


def _ingest_payload() -> PremarketIngestJobPayload:
    return PremarketIngestJobPayload(
        schema_version="1.0",
        active_target=_active_target(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        source_id="synthetic_exchange",
        source_adapter_version="1.0.0",
        rights_snapshot_hash="a" * 64,
        ruleset_version="r1.0.0",
        max_records=100,
    )


def _brief_payload() -> PremarketBriefJobPayload:
    return PremarketBriefJobPayload(
        schema_version="1.0",
        active_target=_active_target(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        source_ids=["synthetic_exchange"],
        ruleset_version="r1.0.0",
        maximum_items=50,
        minimum_materiality=0.4,
    )


def test_watchlist_and_active_target_contracts_freeze_single_target_revision() -> None:
    lookup = ActiveTargetLookupRequest(
        schema_version="1.0",
        context=_operation_context(),
        watchlist_id=WATCHLIST_ID,
    )
    found = ActiveTargetLookupResult(
        schema_version="1.0",
        found=True,
        watchlist=_watchlist(),
        active_target=_active_target(),
        as_of=NOW,
    )
    write = ActiveTargetWriteRequest(
        schema_version="1.0",
        context=_mutation_context(),
        watchlist=_watchlist(),
        active_target=_active_target(),
        expected_revision=1,
    )

    assert lookup.watchlist_id == WATCHLIST_ID
    assert found.active_target == write.active_target

    second_instrument = INSTRUMENT.model_copy(
        update={"instrument_id": "XSHE:000001", "symbol": "000001", "exchange": "XSHE"}
    )
    ordinary_watchlist = Watchlist(
        watchlist_id=WATCHLIST_ID,
        name="Ordinary watchlist",
        entries=[
            _watchlist().entries[0],
            WatchlistEntry(
                instrument=second_instrument,
                added_at=WINDOW_START,
                alert_enabled=True,
            ),
        ],
        revision=2,
        updated_at=WINDOW_END,
    )
    assert len(ordinary_watchlist.entries) == 2

    with pytest.raises(ValidationError, match="requires exactly one alert-enabled"):
        ActiveTargetWriteRequest(
            schema_version="1.0",
            context=_mutation_context(),
            watchlist=ordinary_watchlist,
            active_target=_active_target(),
            expected_revision=1,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"expected_revision": None}, "new watchlists must begin at revision 1"),
        (
            {"active_target": _active_target(revision=3)},
            "active target must match the watchlist revision",
        ),
        (
            {
                "active_target": _active_target().model_copy(
                    update={
                        "instrument": INSTRUMENT.model_copy(
                            update={
                                "instrument_id": "XSHE:000001",
                                "symbol": "000001",
                                "exchange": "XSHE",
                            }
                        )
                    }
                )
            },
            "active target must match the alert-enabled entry",
        ),
    ],
)
def test_active_target_write_rejects_inconsistent_aggregate(
    change: dict[str, object],
    message: str,
) -> None:
    fields: dict[str, object] = {
        "schema_version": "1.0",
        "context": _mutation_context(),
        "watchlist": _watchlist(),
        "active_target": _active_target(),
        "expected_revision": 1,
    }
    fields.update(change)

    with pytest.raises(ValidationError, match=message):
        ActiveTargetWriteRequest.model_validate(fields)


def test_active_target_write_allows_same_revision_noop_and_reports_change() -> None:
    no_op_request = ActiveTargetWriteRequest(
        schema_version="1.0",
        context=_mutation_context(),
        watchlist=_watchlist(revision=2),
        active_target=_active_target(revision=2),
        expected_revision=2,
    )
    no_op_result = ActiveTargetWriteResult(
        schema_version="1.0",
        watchlist=no_op_request.watchlist,
        active_target=no_op_request.active_target,
        created=False,
        changed=False,
        stored_at=NOW,
    )

    assert no_op_result.watchlist.revision == no_op_request.expected_revision
    assert no_op_result.changed is False

    with pytest.raises(
        ValidationError,
        match="newly created active target must be reported as changed",
    ):
        ActiveTargetWriteResult(
            schema_version="1.0",
            watchlist=_watchlist(revision=1),
            active_target=_active_target(revision=1),
            created=True,
            changed=False,
            stored_at=NOW,
        )


def test_active_target_lookup_never_exposes_a_future_revision() -> None:
    with pytest.raises(ValidationError, match="after as_of"):
        ActiveTargetLookupResult(
            schema_version="1.0",
            found=True,
            watchlist=_watchlist().model_copy(update={"updated_at": NOW + timedelta(seconds=1)}),
            active_target=_active_target(),
            as_of=NOW,
        )


def test_portfolio_repository_v1_1_uses_explicit_active_target_contracts() -> None:
    assert set(PortfolioRepositoryPort.__dict__) >= {
        "get_latest",
        "save_snapshot",
        "get_active_target",
        "save_active_target",
    }


def test_premarket_job_requests_have_fixed_names_and_point_in_time_windows() -> None:
    ingest = PremarketIngestJobRequest(
        schema_version="1.0",
        job_id=UUID("00000000-0000-0000-0000-000000000401"),
        job_name=PREMARKET_INGEST_JOB_NAME,
        context=_mutation_context(),
        payload=_ingest_payload(),
        queued_at=NOW,
        attempt=1,
        max_attempts=3,
    )
    brief = PremarketBriefJobRequest(
        schema_version="1.0",
        job_id=UUID("00000000-0000-0000-0000-000000000402"),
        job_name=PREMARKET_BRIEF_JOB_NAME,
        context=_mutation_context(),
        payload=_brief_payload(),
        queued_at=NOW,
        attempt=1,
        max_attempts=3,
    )

    assert ingest.context.correlation_id == _mutation_context().correlation_id
    assert brief.payload.active_target == ingest.payload.active_target

    with pytest.raises(ValidationError):
        PremarketIngestJobRequest.model_validate(
            ingest.model_dump() | {"job_name": PREMARKET_BRIEF_JOB_NAME}
        )
    with pytest.raises(ValidationError, match=r"window_end cannot be later than context\.as_of"):
        PremarketIngestJobRequest.model_validate(
            ingest.model_dump()
            | {
                "payload": ingest.payload.model_copy(
                    update={"window_end": NOW + timedelta(seconds=1)}
                )
            }
        )


def test_premarket_job_results_distinguish_completed_degraded_and_skipped() -> None:
    ingest_summary = PremarketIngestTaskResult(
        schema_version="1.0",
        active_target=_active_target(),
        source_id="synthetic_exchange",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        records_fetched=2,
        evidence_stored=2,
        event_revisions_stored=1,
        data_manifest_hash="b" * 64,
        next_cursor="next-opaque-cursor",
        source_watermark=WINDOW_END,
    )
    PremarketIngestJobResult(
        schema_version="1.0",
        job_id=UUID("00000000-0000-0000-0000-000000000401"),
        status=JobStatus.SUCCEEDED,
        started_at=WINDOW_END,
        completed_at=NOW,
        result=ingest_summary,
        attempt=1,
    )

    briefing_summary = PremarketBriefTaskResult(
        schema_version="1.0",
        active_target=_active_target(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        briefing_id=UUID("00000000-0000-0000-0000-000000000501"),
        generated_at=NOW,
        event_count=1,
        evidence_count=2,
        data_manifest_hash="b" * 64,
        reason_codes=["SOURCE_DELAYED"],
    )
    PremarketBriefJobResult(
        schema_version="1.0",
        job_id=UUID("00000000-0000-0000-0000-000000000402"),
        status=JobStatus.DEGRADED,
        started_at=WINDOW_END,
        completed_at=NOW,
        result=briefing_summary,
        attempt=1,
    )

    skipped_summary = ingest_summary.model_copy(
        update={
            "records_fetched": 0,
            "evidence_stored": 0,
            "event_revisions_stored": 0,
            "data_manifest_hash": None,
            "next_cursor": None,
            "source_watermark": None,
            "reason_codes": ["ACTIVE_TARGET_SUPERSEDED"],
        }
    )
    PremarketIngestJobResult(
        schema_version="1.0",
        job_id=UUID("00000000-0000-0000-0000-000000000403"),
        status=JobStatus.SKIPPED,
        completed_at=NOW,
        result=skipped_summary,
        attempt=1,
    )

    with pytest.raises(ValidationError, match="skipped results require a reason code"):
        PremarketIngestJobResult(
            schema_version="1.0",
            job_id=UUID("00000000-0000-0000-0000-000000000404"),
            status=JobStatus.SKIPPED,
            completed_at=NOW,
            result=skipped_summary.model_copy(update={"reason_codes": []}),
            attempt=1,
        )


def test_r1_and_unified_r2_tasks_publish_concrete_request_and_result_schemas() -> None:
    target_paths = {target.relative_path.as_posix() for target in SCHEMA_TARGETS}
    expected = {
        "schemas/jobs/premarket-ingest-request.v1.0.schema.json",
        "schemas/jobs/premarket-ingest-result.v1.0.schema.json",
        "schemas/jobs/premarket-generate-request.v1.0.schema.json",
        "schemas/jobs/premarket-generate-result.v1.0.schema.json",
        "schemas/jobs/policy-refresh-request.v1.0.schema.json",
        "schemas/jobs/policy-refresh-result.v1.0.schema.json",
        "schemas/jobs/research-generate-request.v1.0.schema.json",
        "schemas/jobs/research-generate-request.v1.1.schema.json",
        "schemas/jobs/research-generate-result.v1.0.schema.json",
    }
    assert expected <= target_paths

    registry = json.loads(Path("governance/interfaces.json").read_text(encoding="utf-8"))
    tasks = {item["key"]: item for item in registry["tasks"]}
    assert tasks["premarket_ingest"]["request_schema"] == (
        "schema:jobs.premarket-ingest-request:v1.2"
    )
    assert tasks["premarket_ingest"]["result_schema"] == (
        "schema:jobs.premarket-ingest-result:v1.2"
    )
    assert tasks["premarket_ingest"]["backward_compatible_request_schemas"] == [
        "schema:jobs.premarket-ingest-request:v1.0",
        "schema:jobs.premarket-ingest-request:v1.1",
    ]
    assert tasks["premarket_ingest"]["backward_compatible_result_schemas"] == [
        "schema:jobs.premarket-ingest-result:v1.0",
        "schema:jobs.premarket-ingest-result:v1.1",
    ]
    assert tasks["premarket_ingest"]["status"] == "implemented"
    assert "stub_behavior" not in tasks["premarket_ingest"]
    assert tasks["premarket_brief"]["request_schema"] == (
        "schema:jobs.premarket-generate-request:v1.0"
    )
    assert tasks["premarket_brief"]["result_schema"] == (
        "schema:jobs.premarket-generate-result:v1.0"
    )
    assert tasks["premarket_brief"]["status"] == "implemented"
    assert "stub_behavior" not in tasks["premarket_brief"]
    assert tasks["policy_refresh"]["request_schema"] == ("schema:jobs.policy-refresh-request:v1.0")
    assert tasks["policy_refresh"]["result_schema"] == ("schema:jobs.policy-refresh-result:v1.0")
    assert tasks["policy_refresh"]["status"] == "implemented"
    assert "stub_behavior" not in tasks["policy_refresh"]
    assert tasks["research_generate"]["version"] == "1.1"
    assert tasks["research_generate"]["request_schema"] == (
        "schema:jobs.research-generate-request:v1.1"
    )
    assert tasks["research_generate"]["backward_compatible_request_schemas"] == [
        "schema:jobs.research-generate-request:v1.0"
    ]
    assert tasks["research_generate"]["result_schema"] == (
        "schema:jobs.research-generate-result:v1.0"
    )
    assert all(
        "request_schema" not in tasks[key] and "result_schema" not in tasks[key]
        for key in {
            "quote_snapshot",
            "sector_rotation",
            "postmarket_outcome",
        }
    )
    assert all(
        tasks[key]["stub_behavior"] == "fail_closed_disabled"
        for key in {
            "quote_snapshot",
            "sector_rotation",
            "postmarket_outcome",
        }
    )
