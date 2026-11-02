"""Regression tests for explicit, extensible port result envelopes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import EntityRef, MutationContext
from stock_assistant.contracts.jobs import (
    JobCancelResult,
    JobResult,
    JobResultLookup,
    JobStatus,
    ScheduledJob,
    ScheduledJobTemplate,
    ScheduleKind,
    ScheduleLookupResult,
    ScheduleTrigger,
)
from stock_assistant.contracts.models import (
    TrainingRunLookupResult,
    TrainingRunRecord,
    TrainingRunStatus,
)
from stock_assistant.contracts.storage import LockAcquireResult, LockLease
from stock_assistant.contracts.system import (
    AuditOutcome,
    AuditRecord,
    AuditWriteRequest,
    AuditWriteResult,
)

NOW = datetime(2026, 7, 22, 9, 30, tzinfo=UTC)


class _Payload(ContractModel):
    value: str


def _job_result(*, job_id: UUID | None = None, started_at: datetime = NOW) -> JobResult[_Payload]:
    return JobResult[_Payload](
        schema_version="1.0",
        job_id=job_id or uuid4(),
        status=JobStatus.RUNNING,
        started_at=started_at,
        attempt=1,
    )


def _schedule() -> ScheduledJob[_Payload]:
    return ScheduledJob[_Payload](
        schema_version="1.0",
        schedule_name="sector_rotation",
        job_template=ScheduledJobTemplate[_Payload](
            schema_version="1.0",
            job_name="stock_assistant.sectors.rotate.v1",
            payload=_Payload(value="synthetic"),
            idempotency_scope="sector-rotation",
        ),
        trigger=ScheduleTrigger(kind=ScheduleKind.INTERVAL, interval_seconds=600),
        enabled=True,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW,
        revision=1,
    )


def _lease() -> LockLease:
    return LockLease(
        schema_version="1.0",
        resource_key="sector:rotation",
        owner_id="worker-1",
        acquired_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        fencing_token=1,
    )


def _mutation_context(*, actor_id: str, correlation_id: UUID) -> MutationContext:
    return MutationContext(
        schema_version="1.0",
        actor_id=actor_id,
        correlation_id=correlation_id,
        as_of=NOW,
        requested_at=NOW,
        idempotency_key="unified-result-contract-0001",
    )


def test_job_lookup_makes_absence_identity_and_cutoff_explicit() -> None:
    job = _job_result()
    result = JobResultLookup[_Payload](
        schema_version="1.0",
        job_id=job.job_id,
        found=True,
        job=job,
        as_of=NOW,
    )
    assert result.job is job

    with pytest.raises(ValidationError, match="found must reflect"):
        JobResultLookup[_Payload](
            schema_version="1.0",
            job_id=job.job_id,
            found=False,
            job=job,
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="must match job_id"):
        JobResultLookup[_Payload](
            schema_version="1.0",
            job_id=uuid4(),
            found=True,
            job=job,
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="recorded after as_of"):
        future = _job_result(started_at=NOW + timedelta(seconds=1))
        JobResultLookup[_Payload](
            schema_version="1.0",
            job_id=future.job_id,
            found=True,
            job=future,
            as_of=NOW,
        )


def test_job_cancellation_result_cannot_contradict_status() -> None:
    cancelled = JobCancelResult(
        schema_version="1.0",
        job_id=uuid4(),
        cancelled=True,
        status=JobStatus.CANCELLED,
        processed_at=NOW,
    )
    assert cancelled.status is JobStatus.CANCELLED

    with pytest.raises(ValidationError, match="cancelled must reflect"):
        JobCancelResult(
            schema_version="1.0",
            job_id=uuid4(),
            cancelled=True,
            status=JobStatus.RUNNING,
            processed_at=NOW,
        )
    with pytest.raises(ValidationError, match="requires reason_code"):
        JobCancelResult(
            schema_version="1.0",
            job_id=uuid4(),
            cancelled=False,
            status=JobStatus.SUCCEEDED,
            processed_at=NOW,
        )


def test_schedule_lookup_binds_identity_and_revision_visibility() -> None:
    schedule = _schedule()
    found = ScheduleLookupResult[_Payload](
        schema_version="1.0",
        schedule_id=schedule.schedule_id,
        found=True,
        schedule=schedule,
        as_of=NOW,
    )
    assert found.schedule is schedule

    with pytest.raises(ValidationError, match="must match schedule_id"):
        ScheduleLookupResult[_Payload](
            schema_version="1.0",
            schedule_id=uuid4(),
            found=True,
            schedule=schedule,
            as_of=NOW,
        )
    with pytest.raises(ValidationError, match="recorded after as_of"):
        ScheduleLookupResult[_Payload](
            schema_version="1.0",
            schedule_id=schedule.schedule_id,
            found=True,
            schedule=schedule,
            as_of=NOW - timedelta(seconds=1),
        )


def test_training_lookup_uses_an_explicit_absence_envelope_and_cutoff() -> None:
    run_id = uuid4()
    absent = TrainingRunLookupResult(
        schema_version="1.0",
        run_id=run_id,
        found=False,
        as_of=NOW,
    )
    assert absent.run is None

    with pytest.raises(ValidationError, match="found must reflect"):
        TrainingRunLookupResult(
            schema_version="1.0",
            run_id=run_id,
            found=True,
            as_of=NOW,
        )

    future_run = TrainingRunRecord.model_construct(
        run_id=run_id,
        status=TrainingRunStatus.QUEUED,
        requested_at=NOW + timedelta(seconds=1),
        started_at=None,
        completed_at=None,
        failure_code=None,
    )
    with pytest.raises(ValidationError, match="recorded after as_of"):
        TrainingRunLookupResult(
            schema_version="1.0",
            run_id=run_id,
            found=True,
            run=future_run,
            as_of=NOW,
        )


def test_lock_acquisition_result_represents_contention_without_none() -> None:
    lease = _lease()
    acquired = LockAcquireResult(
        schema_version="1.0",
        acquired=True,
        lease=lease,
        processed_at=NOW,
    )
    assert acquired.lease is lease

    contention = LockAcquireResult(
        schema_version="1.0",
        acquired=False,
        processed_at=NOW,
        reason_code="LOCK_BUSY",
    )
    assert contention.lease is None

    with pytest.raises(ValidationError, match="reflect whether a lease"):
        LockAcquireResult(
            schema_version="1.0",
            acquired=False,
            lease=lease,
            processed_at=NOW,
            reason_code="LOCK_BUSY",
        )
    with pytest.raises(ValidationError, match="requires reason_code"):
        LockAcquireResult(
            schema_version="1.0",
            acquired=False,
            processed_at=NOW,
        )


def test_audit_write_binds_actor_trace_and_returns_an_acknowledgement() -> None:
    correlation_id = uuid4()
    record = AuditRecord(
        occurred_at=NOW,
        actor_id="local-user",
        action="portfolio.read",
        resource=EntityRef(entity_type="portfolio", entity_id="default"),
        outcome=AuditOutcome.SUCCEEDED,
        correlation_id=correlation_id,
    )
    request = AuditWriteRequest(
        schema_version="1.0",
        context=_mutation_context(actor_id="local-user", correlation_id=correlation_id),
        record=record,
    )
    result = AuditWriteResult(
        schema_version="1.0",
        audit_id=request.record.audit_id,
        stored_at=NOW,
    )
    assert result.audit_id == record.audit_id

    with pytest.raises(ValidationError, match="correlation IDs must match"):
        AuditWriteRequest(
            schema_version="1.0",
            context=_mutation_context(actor_id="local-user", correlation_id=uuid4()),
            record=record,
        )
    with pytest.raises(ValidationError, match="actor IDs must match"):
        AuditWriteRequest(
            schema_version="1.0",
            context=_mutation_context(actor_id="different-user", correlation_id=correlation_id),
            record=record,
        )
