"""PostgreSQL-fenced ownership and checkpoint operations for ingestion jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from api.models import IngestionJob, IngestionStage, IngestionState


class LeaseUnavailable(RuntimeError):
    """A job cannot currently be claimed."""


class StaleLease(RuntimeError):
    """The caller no longer owns the job fence."""


@dataclass(frozen=True, slots=True)
class JobLease:
    job_uuid: str
    generation: int
    owner: str
    token: str
    claim_epoch: int
    expires_at: datetime
    next_chunk_index: int
    indexed_chunks: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _db_now(session: Session, override: datetime | None = None) -> datetime:
    if override is not None:
        return _utc(override)
    value = session.execute(select(func.now())).scalar_one()
    return _utc(value)


def _fenced_job(
    job_uuid: str,
    *,
    generation: int,
    token: str,
    claim_epoch: int,
    now: datetime,
) -> tuple[Any, ...]:
    return (
        IngestionJob.uuid == job_uuid,
        IngestionJob.generation == generation,
        IngestionJob.state == IngestionState.RUNNING,
        IngestionJob.lease_token == token,
        IngestionJob.claim_epoch == claim_epoch,
        IngestionJob.lease_expires_at.is_not(None),
        IngestionJob.lease_expires_at >= now,
    )


def claim_job(
    session: Session,
    job_uuid: str,
    owner: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> JobLease:
    """Atomically claim one job and return its monotonic fencing token."""
    current = _db_now(session, now)
    expires = current + timedelta(seconds=lease_seconds)
    token = str(uuid4())
    eligible = (
        IngestionJob.uuid == job_uuid,
        IngestionJob.attempt_count < IngestionJob.max_attempts,
        or_(IngestionJob.retry_at.is_(None), IngestionJob.retry_at <= current),
        or_(
            IngestionJob.state.in_([IngestionState.PENDING, IngestionState.RETRY_WAIT]),
            (
                (IngestionJob.state == IngestionState.RUNNING)
                & or_(
                    IngestionJob.lease_expires_at.is_(None),
                    IngestionJob.lease_expires_at < current,
                )
            ),
        ),
    )
    statement = (
        update(IngestionJob)
        .where(*eligible)
        .values(
            state=IngestionState.RUNNING,
            lease_owner=owner,
            lease_token=token,
            claim_epoch=IngestionJob.claim_epoch + 1,
            lease_expires_at=expires,
            heartbeat_at=current,
            attempt_count=IngestionJob.attempt_count + 1,
            retry_at=None,
            started_at=func.coalesce(IngestionJob.started_at, current),
            error_code=None,
            error_message=None,
            last_error_class=None,
        )
        .returning(
            IngestionJob.generation,
            IngestionJob.claim_epoch,
            IngestionJob.next_chunk_index,
            IngestionJob.indexed_chunks,
        )
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        raise LeaseUnavailable(f"Job {job_uuid} is not claimable")
    session.flush()
    return JobLease(
        job_uuid=job_uuid,
        generation=row.generation,
        owner=owner,
        token=token,
        claim_epoch=row.claim_epoch,
        expires_at=expires,
        next_chunk_index=row.next_chunk_index,
        indexed_chunks=row.indexed_chunks,
    )


def renew_lease(
    session: Session,
    lease: JobLease,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> datetime:
    current = _db_now(session, now)
    expires = current + timedelta(seconds=lease_seconds)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            )
        )
        .values(lease_expires_at=expires, heartbeat_at=current)
    )
    if result.rowcount != 1:
        raise StaleLease(f"Lease for job {lease.job_uuid} is stale")
    session.flush()
    return expires


def record_artifacts(
    session: Session,
    lease: JobLease,
    *,
    expected_stage: IngestionStage,
    next_stage: IngestionStage,
    values: dict[str, object],
    lease_seconds: int,
    now: datetime | None = None,
) -> None:
    current = _db_now(session, now)
    expires = current + timedelta(seconds=lease_seconds)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            ),
            IngestionJob.stage == expected_stage,
        )
        .values(
            **values,
            stage=next_stage,
            heartbeat_at=current,
            lease_expires_at=expires,
        )
    )
    if result.rowcount != 1:
        raise StaleLease(f"Artifact transition for job {lease.job_uuid} lost its fence")
    session.flush()


def record_checkpoint(
    session: Session,
    lease: JobLease,
    *,
    expected_chunk_index: int,
    next_chunk_index: int,
    indexed_chunks: int,
    now: datetime | None = None,
) -> None:
    if next_chunk_index < expected_chunk_index or indexed_chunks < 0:
        raise ValueError("Checkpoint counters cannot move backward")
    current = _db_now(session, now)
    job = session.execute(
        select(IngestionJob)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            ),
            IngestionJob.stage == IngestionStage.EMBEDDING,
            IngestionJob.next_chunk_index == expected_chunk_index,
            IngestionJob.indexed_chunks <= indexed_chunks,
            IngestionJob.total_chunks >= next_chunk_index,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise StaleLease(f"Checkpoint for job {lease.job_uuid} lost its fence")
    claims = list(job.text_batch_claims or [])
    claims.append(
        {
            "start": expected_chunk_index,
            "end": next_chunk_index,
            "claim_epoch": lease.claim_epoch,
        }
    )
    job.text_batch_claims = claims
    job.next_chunk_index = next_chunk_index
    job.indexed_chunks = indexed_chunks
    job.heartbeat_at = current
    job.progress_pct = (
        100.0 * next_chunk_index / job.total_chunks if job.total_chunks else 0.0
    )
    session.flush()


def record_image_checkpoint(
    session: Session,
    lease: JobLease,
    *,
    expected_image_index: int,
    next_image_index: int,
    indexed_images: int,
    now: datetime | None = None,
) -> None:
    if next_image_index < expected_image_index or indexed_images < 0:
        raise ValueError("Image checkpoint counters cannot move backward")
    current = _db_now(session, now)
    job = session.execute(
        select(IngestionJob)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            ),
            IngestionJob.stage == IngestionStage.IMAGES,
            IngestionJob.next_image_index == expected_image_index,
            IngestionJob.indexed_images <= indexed_images,
            IngestionJob.total_images >= next_image_index,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise StaleLease(f"Image checkpoint for job {lease.job_uuid} lost its fence")
    claims = list(job.image_batch_claims or [])
    claims.append(
        {
            "start": expected_image_index,
            "end": next_image_index,
            "claim_epoch": lease.claim_epoch,
        }
    )
    job.image_batch_claims = claims
    job.next_image_index = next_image_index
    job.indexed_images = indexed_images
    job.heartbeat_at = current
    session.flush()


def complete_job(
    session: Session,
    lease: JobLease,
    *,
    now: datetime | None = None,
) -> None:
    current = _db_now(session, now)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            ),
            IngestionJob.next_chunk_index == IngestionJob.total_chunks,
            IngestionJob.next_image_index == IngestionJob.total_images,
            IngestionJob.stage == IngestionStage.IMAGES,
        )
        .values(
            state=IngestionState.SUCCEEDED,
            stage=IngestionStage.FINALIZING,
            progress_pct=100.0,
            finished_at=current,
            heartbeat_at=current,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
    )
    if result.rowcount != 1:
        raise StaleLease(f"Completion for job {lease.job_uuid} lost its fence")
    session.flush()


def release_for_retry(
    session: Session,
    lease: JobLease,
    *,
    error_code: str,
    error_message: str,
    error_class: str,
    retry_at: datetime,
    now: datetime | None = None,
) -> None:
    current = _db_now(session, now)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            )
        )
        .values(
            state=IngestionState.RETRY_WAIT,
            retry_at=_utc(retry_at),
            error_code=error_code,
            error_message=error_message[:4000],
            last_error_class=error_class[:256],
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=current,
        )
    )
    if result.rowcount != 1:
        raise StaleLease(f"Retry transition for job {lease.job_uuid} lost its fence")
    session.flush()


def fail_job(
    session: Session,
    lease: JobLease,
    *,
    error_code: str,
    error_message: str,
    error_class: str,
    now: datetime | None = None,
) -> None:
    current = _db_now(session, now)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            *_fenced_job(
                lease.job_uuid,
                generation=lease.generation,
                token=lease.token,
                claim_epoch=lease.claim_epoch,
                now=current,
            )
        )
        .values(
            state=IngestionState.FAILED,
            error_code=error_code,
            error_message=error_message[:4000],
            last_error_class=error_class[:256],
            finished_at=current,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=current,
        )
    )
    if result.rowcount != 1:
        raise StaleLease(f"Failure transition for job {lease.job_uuid} lost its fence")
    session.flush()


def cancel_job(
    session: Session,
    job_uuid: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Fence active workers and request reconciled cancellation."""
    current = _db_now(session, now)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            IngestionJob.uuid == job_uuid,
            IngestionJob.state.not_in(
                [
                    IngestionState.SUCCEEDED,
                    IngestionState.FAILED,
                    IngestionState.CANCELLED,
                ]
            ),
        )
        .values(
            state=IngestionState.CANCEL_REQUESTED,
            cancel_requested_at=current,
            claim_epoch=IngestionJob.claim_epoch + 1,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=current,
        )
    )
    session.flush()
    return result.rowcount == 1


def finalize_cancel(
    session: Session, job_uuid: str, *, now: datetime | None = None
) -> bool:
    current = _db_now(session, now)
    result: CursorResult = session.execute(  # type: ignore[assignment,type-arg]
        update(IngestionJob)
        .execution_options(synchronize_session=False)
        .where(
            IngestionJob.uuid == job_uuid,
            IngestionJob.state == IngestionState.CANCEL_REQUESTED,
        )
        .values(state=IngestionState.CANCELLED, finished_at=current)
    )
    session.flush()
    return result.rowcount == 1
