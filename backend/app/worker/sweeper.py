"""Recovering work abandoned by a worker that died.

This is the fix for the defect Sprint 4 shipped with: kill the process
mid-analysis and the record stayed `running` forever, with no way to retry that
game through the application.

A lease is the mechanism. A live worker renews it every few seconds; a dead one
cannot. Once the lease is past due - measured by *database* time, never the
sweeper's clock - the job is provably abandoned and can be safely taken back.

Each job is recovered in its own transaction, and its status is re-checked
under a row lock first. Between finding a stale job and acting on it, another
sweeper may have already handled it, or the original worker may have come back
and renewed. The lock plus re-check is what makes running two sweepers
harmless.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Callable, ContextManager

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.analysis_job import JOB_RUNNING, AnalysisJob
from app.models.analysis_run import STATUS_RUNNING
from app.repositories import analysis as analysis_repo
from app.repositories import analysis_jobs as jobs_repo

logger = logging.getLogger("app.worker")

SessionFactory = Callable[[], ContextManager[Session]]

LEASE_EXPIRED_CODE = "worker_lease_expired"
LEASE_EXPIRED_MESSAGE = (
    "The worker running this analysis stopped responding, so the job was recovered."
)

REQUEUED = "requeued"
EXHAUSTED = "failed"


@dataclass(frozen=True)
class Recovery:
    job_id: uuid.UUID
    result: str
    attempt_count: int


def _default_session() -> ContextManager[Session]:
    return SessionLocal()


def _recover_one(
    db: Session, job_id: uuid.UUID, *, retry_delay_seconds: int
) -> Recovery | None:
    """Recover a single job, or return None if it no longer needs recovering."""
    job = db.execute(
        select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update()
    ).scalars().first()

    # Re-checked under the lock: another sweeper may have taken it, or the
    # original worker may have renewed in the meantime and be alive after all.
    if job is None or job.status != JOB_RUNNING:
        return None
    if job.lease_expires_at is None:
        return None
    # Expiry is judged by database time, so a sweeper with a fast clock cannot
    # steal a job whose lease is genuinely still valid.
    still_expired = db.execute(
        select(AnalysisJob.id).where(
            AnalysisJob.id == job_id,
            AnalysisJob.lease_expires_at < func.now(),
        )
    ).first()
    if still_expired is None:
        return None

    # The run is the record of engine work that was actually happening. Leaving
    # it `running` is exactly the stale state this whole checkpoint exists to
    # remove, so it is closed out before the job moves on.
    run = jobs_repo.latest_run_for_job(db, job_id)
    if run is not None and run.status == STATUS_RUNNING:
        analysis_repo.mark_analysis_run_failed(db, run, LEASE_EXPIRED_MESSAGE)

    attempts_remain = job.attempt_count < job.max_attempts

    if attempts_remain:
        jobs_repo.requeue_job(
            db,
            job_id=job_id,
            worker_id=None,
            error_code=LEASE_EXPIRED_CODE,
            error_message=LEASE_EXPIRED_MESSAGE,
            delay_seconds=retry_delay_seconds,
        )
        result = REQUEUED
    else:
        jobs_repo.fail_job(
            db,
            job_id=job_id,
            worker_id=None,
            error_code=LEASE_EXPIRED_CODE,
            error_message=LEASE_EXPIRED_MESSAGE,
        )
        result = EXHAUSTED

    return Recovery(job_id=job_id, result=result, attempt_count=job.attempt_count)


def recover_stale_jobs(
    session_factory: SessionFactory = _default_session,
    *,
    retry_delay_seconds: int | None = None,
    limit: int = 50,
) -> list[Recovery]:
    """Find and recover every job whose lease has expired.

    Jobs with attempts remaining go back to the queue after a short delay;
    jobs that have used their last attempt are failed. Either way the job stops
    being `running`, which is what makes it visible and actionable again.
    """
    delay = (
        retry_delay_seconds
        if retry_delay_seconds is not None
        else settings.analysis_retry_delay_seconds
    )

    # Read-only, and deliberately not rolled back: closing the session already
    # discards the transaction, and an explicit rollback would throw away
    # uncommitted work when the caller supplies a shared session.
    with session_factory() as db:
        stale_ids = [job.id for job in jobs_repo.find_stale_jobs(db, limit=limit)]

    recoveries: list[Recovery] = []
    for job_id in stale_ids:
        try:
            with session_factory() as db:
                recovery = _recover_one(db, job_id, retry_delay_seconds=delay)
                if recovery is None:
                    continue
                db.commit()
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            logger.exception("Could not recover stale job %s", job_id)
            continue

        recoveries.append(recovery)
        logger.warning(
            "job=%s recovered result=%s attempt=%s",
            recovery.job_id,
            recovery.result,
            recovery.attempt_count,
        )

    return recoveries
