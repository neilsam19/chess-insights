"""Queue operations for analysis batches and jobs.

Every function here is a short, self-contained database operation. None of them
commit - the caller owns the transaction boundary, exactly as the existing
analysis repository does - and none of them start an engine or block on one.

Two rules shape this module:

*Database time only.* Leases and availability are compared using PostgreSQL's
`now()`, never the worker's clock. A worker with a skewed clock could otherwise
renew a lease that has actually expired, or steal one that has not.

*Ownership is a WHERE clause, not a check.* Worker mutations match on
`claimed_by` and `status` inside the UPDATE itself and report whether a row was
affected. A stale worker whose lease was reclaimed cannot overwrite the new
owner's work, because its UPDATE simply matches nothing.

The state machine these functions implement is documented in
`docs/adr/0001-durable-analysis-jobs.md` section 4.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.analysis_batch import BATCH_QUEUED, AnalysisBatch
from app.models.analysis_job import (
    DEFAULT_MAX_ATTEMPTS,
    JOB_ACTIVE_STATUSES,
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import STATUS_RUNNING, AnalysisRun
from app.services.engine import EngineIdentity

# Mirrors ADR section 4. Terminal-to-queued edges exist only for explicit
# manual retry; nothing transitions out of `complete`.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    JOB_QUEUED: frozenset({JOB_RUNNING, JOB_CANCELLED}),
    JOB_RUNNING: frozenset({JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED, JOB_QUEUED}),
    JOB_COMPLETE: frozenset(),
    JOB_FAILED: frozenset({JOB_QUEUED}),
    JOB_CANCELLED: frozenset({JOB_QUEUED}),
}

# Manual retry is narrower than the transition table: see `retry_job`.
RETRYABLE_FROM = frozenset({JOB_FAILED, JOB_CANCELLED})


class InvalidJobTransition(Exception):
    """A transition that is not in the state machine was attempted."""

    def __init__(self, job_id: uuid.UUID, from_status: str, to_status: str) -> None:
        self.job_id = job_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Job {job_id} cannot move from {from_status} to {to_status}.")


def assert_transition(job: AnalysisJob, to_status: str) -> None:
    if to_status not in ALLOWED_TRANSITIONS.get(job.status, frozenset()):
        raise InvalidJobTransition(job.id, job.status, to_status)


@dataclass(frozen=True)
class HeartbeatResult:
    """What one lease renewal learned about the job.

    `owned` False means stop and discard. `cancel_requested` True means stop
    and mark the job cancelled - a different outcome, and one the user asked
    for.
    """

    owned: bool
    cancel_requested: bool

    def __bool__(self) -> bool:
        """Truthy while the job is still ours, so callers can read naturally."""
        return self.owned


@dataclass(frozen=True)
class CancelOutcome:
    """What a cancel request actually did.

    A queued job is cancelled outright. A running job is only *asked* - the
    worker performs the transition itself at the next position boundary, which
    is what keeps "no partial evaluations" true.
    """

    job_id: uuid.UUID
    status: str
    cancelled_now: bool
    requested: bool
    already_terminal: bool


# --- Batches ---------------------------------------------------------------


def create_batch(
    db: Session,
    *,
    upload_id: uuid.UUID | None,
    engine_config_hash: str,
    requested_depth: int,
    engine_options: dict,
) -> AnalysisBatch:
    batch = AnalysisBatch(
        upload_id=upload_id,
        status=BATCH_QUEUED,
        engine_config_hash=engine_config_hash,
        requested_depth=requested_depth,
        engine_options=dict(engine_options),
        created_at=func.now(),
    )
    db.add(batch)
    db.flush()
    return batch


def get_batch(db: Session, batch_id: uuid.UUID) -> AnalysisBatch | None:
    return db.get(AnalysisBatch, batch_id)


# --- Enqueue ---------------------------------------------------------------


def enqueue_job(
    db: Session,
    *,
    batch_id: uuid.UUID,
    game_id: uuid.UUID,
    config_hash: str,
    total_positions: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> AnalysisJob:
    """Add one game to a batch.

    Duplicate protection is the partial unique index, not this function - a
    check here could still lose a race against a simultaneous enqueue.
    """
    job = AnalysisJob(
        batch_id=batch_id,
        game_id=game_id,
        status=JOB_QUEUED,
        config_hash=config_hash,
        attempt_count=0,
        max_attempts=max_attempts,
        available_at=func.now(),
        created_at=func.now(),
        total_positions=total_positions,
        completed_positions=0,
    )
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, job_id: uuid.UUID) -> AnalysisJob | None:
    return db.get(AnalysisJob, job_id)


def find_active_job(db: Session, *, game_id: uuid.UUID, config_hash: str) -> AnalysisJob | None:
    """The queued or running job occupying this (game, config) slot, if any."""
    stmt = select(AnalysisJob).where(
        AnalysisJob.game_id == game_id,
        AnalysisJob.config_hash == config_hash,
        AnalysisJob.status.in_(JOB_ACTIVE_STATUSES),
    )
    return db.execute(stmt).scalars().first()


def find_active_job_for_game(db: Session, game_id: uuid.UUID) -> AnalysisJob | None:
    """Any queued or running job for this game, whatever its configuration.

    `find_active_job` answers "is this exact work already queued?" for the
    enqueue decision. This one answers "is anything happening to this game?"
    for display, which is a different question.
    """
    stmt = (
        select(AnalysisJob)
        .where(
            AnalysisJob.game_id == game_id,
            AnalysisJob.status.in_(JOB_ACTIVE_STATUSES),
        )
        .order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def latest_job_for_game(db: Session, game_id: uuid.UUID) -> AnalysisJob | None:
    stmt = (
        select(AnalysisJob)
        .where(AnalysisJob.game_id == game_id)
        .order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def list_jobs_for_games(db: Session, game_ids: Sequence[uuid.UUID]) -> list[AnalysisJob]:
    """Every job for a set of games, one query.

    Ordered newest-first within each game, matching
    `find_active_job_for_game` and `latest_job_for_game`, so picking the first
    match per game gives the same answer those two would.
    """
    if not game_ids:
        return []
    stmt = (
        select(AnalysisJob)
        .where(AnalysisJob.game_id.in_(game_ids))
        .order_by(
            AnalysisJob.game_id,
            AnalysisJob.created_at.desc(),
            AnalysisJob.id.desc(),
        )
    )
    return list(db.execute(stmt).scalars())


def list_jobs_for_batch(db: Session, batch_id: uuid.UUID) -> list[AnalysisJob]:
    stmt = (
        select(AnalysisJob)
        .where(AnalysisJob.batch_id == batch_id)
        .order_by(AnalysisJob.created_at, AnalysisJob.id)
    )
    return list(db.execute(stmt).scalars())


# --- Claiming --------------------------------------------------------------


def claim_next_job(
    db: Session, *, worker_id: str, lease_seconds: int
) -> AnalysisJob | None:
    """Take ownership of the oldest eligible queued job, or return None.

    One statement: the inner SELECT locks a candidate row with
    `FOR UPDATE SKIP LOCKED` so a concurrent worker skips past it rather than
    blocking, and the outer UPDATE claims it. Two workers racing here cannot
    both win, because the second one never sees the locked row.

    Deliberately does *not* create the analysis run. The run records engine
    provenance, and `engine_name` is only known after the engine has started -
    which must not happen inside this transaction. See `start_run_for_job`.
    """
    candidate = (
        select(AnalysisJob.id)
        .where(
            AnalysisJob.status == JOB_QUEUED,
            AnalysisJob.available_at <= func.now(),
            AnalysisJob.cancel_requested_at.is_(None),
            AnalysisJob.attempt_count < AnalysisJob.max_attempts,
        )
        .order_by(AnalysisJob.available_at, AnalysisJob.created_at, AnalysisJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    claimed = db.execute(
        update(AnalysisJob)
        .where(AnalysisJob.id == candidate)
        .values(
            status=JOB_RUNNING,
            attempt_count=AnalysisJob.attempt_count + 1,
            claimed_by=worker_id,
            # Preserved across retries: this is when the job first ran.
            started_at=func.coalesce(AnalysisJob.started_at, func.now()),
            heartbeat_at=func.now(),
            lease_expires_at=func.now() + timedelta(seconds=lease_seconds),
            # A retry starts from ply 0 - resumability is game-level.
            completed_positions=0,
            error_code=None,
            error_message=None,
        )
        .returning(AnalysisJob.id)
    ).scalar_one_or_none()

    if claimed is None:
        return None

    db.flush()
    job = db.get(AnalysisJob, claimed)
    if job is not None:
        db.refresh(job)
    return job


def start_run_for_job(
    db: Session, *, job: AnalysisJob, identity: EngineIdentity, requested_depth: int
) -> AnalysisRun:
    """Record that this attempt has actually begun engine work.

    Separate from claiming because engine identity does not exist until the
    subprocess is up. A job that is claimed but has no run yet means the engine
    is still starting; a job that fails to start one never becomes a run at
    all, matching Sprint 4's rule that a misconfigured server is not a failed
    analysis.
    """
    run = AnalysisRun(
        game_id=job.game_id,
        analysis_job_id=job.id,
        attempt_number=job.attempt_count,
        status=STATUS_RUNNING,
        engine_name=identity.name,
        engine_version=identity.version,
        engine_binary_sha256=identity.binary_sha256,
        requested_depth=requested_depth,
        engine_options=dict(identity.options),
        created_at=func.now(),
        started_at=func.now(),
    )
    db.add(run)
    db.flush()
    return run


def latest_run_for_job(db: Session, job_id: uuid.UUID) -> AnalysisRun | None:
    """Derived rather than stored - see ADR decision D4a."""
    stmt = (
        select(AnalysisRun)
        .where(AnalysisRun.analysis_job_id == job_id)
        .order_by(AnalysisRun.attempt_number.desc(), AnalysisRun.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


# --- Lease maintenance -----------------------------------------------------


def heartbeat_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
    completed_positions: int | None = None,
) -> HeartbeatResult:
    """Renew this worker's lease, optionally recording progress.

    Reports two things at once, from a single statement. `owned` is False when
    the job is no longer ours - reclaimed by the stale sweep or finished by
    someone else - and the worker must stop and discard its results.
    `cancel_requested` is how a running worker learns a user pressed cancel,
    since cancellation sets a flag rather than changing status.

    Both come from one `RETURNING`, so a heartbeat costs the same as before
    rather than adding a second query per interval.

    Progress is monotonic and bounded: a lower value than already recorded is
    ignored, and a value beyond `total_positions` is clamped rather than
    rejected. Both are done in SQL deliberately - an off-by-one in the worker
    would otherwise raise a check violation *inside the heartbeat*, which is
    the one call that must not fail, since it is what keeps the lease alive.
    The database constraint stays as the backstop.
    """
    values: dict = {
        "heartbeat_at": func.now(),
        "lease_expires_at": func.now() + timedelta(seconds=lease_seconds),
    }
    if completed_positions is not None:
        values["completed_positions"] = func.least(
            AnalysisJob.total_positions,
            func.greatest(AnalysisJob.completed_positions, completed_positions),
        )

    row = db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.claimed_by == worker_id,
            AnalysisJob.status == JOB_RUNNING,
        )
        .values(**values)
        .returning(AnalysisJob.cancel_requested_at)
    ).first()

    if row is None:
        return HeartbeatResult(owned=False, cancel_requested=False)
    return HeartbeatResult(owned=True, cancel_requested=row[0] is not None)


def find_stale_jobs(db: Session, *, limit: int = 50) -> list[AnalysisJob]:
    """Running jobs whose lease has expired - their worker is gone.

    Compared against database time, so a worker cannot keep a job alive by
    having a wrong clock.
    """
    stmt = (
        select(AnalysisJob)
        .where(
            AnalysisJob.status == JOB_RUNNING,
            AnalysisJob.lease_expires_at.is_not(None),
            AnalysisJob.lease_expires_at < func.now(),
        )
        .order_by(AnalysisJob.lease_expires_at)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


# --- Terminal transitions --------------------------------------------------


def _release_lease() -> dict:
    """Ownership fields are always cleared together when a job stops running."""
    return {"claimed_by": None, "lease_expires_at": None, "heartbeat_at": None}


def complete_job(db: Session, *, job_id: uuid.UUID, worker_id: str) -> bool:
    """Mark a job finished. Only its current owner may do so."""
    result = db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.claimed_by == worker_id,
            AnalysisJob.status == JOB_RUNNING,
        )
        .values(
            status=JOB_COMPLETE,
            completed_at=func.now(),
            completed_positions=AnalysisJob.total_positions,
            error_code=None,
            error_message=None,
            **_release_lease(),
        )
    )
    return result.rowcount == 1


def fail_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str | None,
    error_code: str,
    error_message: str | None = None,
) -> bool:
    """Record a terminal failure.

    `worker_id` is None for the stale sweep, which fails jobs it does not own -
    that is the whole point of the sweep, and the expired lease is what
    authorizes it.
    """
    conditions = [AnalysisJob.id == job_id, AnalysisJob.status == JOB_RUNNING]
    if worker_id is not None:
        conditions.append(AnalysisJob.claimed_by == worker_id)

    result = db.execute(
        update(AnalysisJob)
        .where(*conditions)
        .values(
            status=JOB_FAILED,
            completed_at=func.now(),
            error_code=error_code,
            error_message=error_message,
            **_release_lease(),
        )
    )
    return result.rowcount == 1


def requeue_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str | None,
    error_code: str | None = None,
    error_message: str | None = None,
    delay_seconds: int = 0,
) -> bool:
    """Return a running job to the queue for another attempt.

    Progress resets to zero: an interrupted game restarts from ply 0, because
    partial evaluations are never persisted. `attempt_count` is *not* reset -
    it is what eventually stops the retry loop.
    """
    conditions = [AnalysisJob.id == job_id, AnalysisJob.status == JOB_RUNNING]
    if worker_id is not None:
        conditions.append(AnalysisJob.claimed_by == worker_id)

    result = db.execute(
        update(AnalysisJob)
        .where(*conditions)
        .values(
            status=JOB_QUEUED,
            available_at=func.now() + timedelta(seconds=delay_seconds),
            completed_positions=0,
            error_code=error_code,
            error_message=error_message,
            **_release_lease(),
        )
    )
    return result.rowcount == 1


def cancel_job(db: Session, *, job_id: uuid.UUID) -> CancelOutcome | None:
    """Cancel a queued job outright, or ask a running one to stop.

    Takes a row lock first: without it, a worker could claim the job in the
    window between reading its status and writing the cancellation, leaving a
    running job that nobody ever asked to stop.
    """
    job = db.execute(
        select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update()
    ).scalars().first()
    if job is None:
        return None

    if job.status == JOB_QUEUED:
        assert_transition(job, JOB_CANCELLED)
        job.status = JOB_CANCELLED
        job.cancel_requested_at = func.now()
        job.completed_at = func.now()
        job.error_code = None
        job.error_message = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        db.flush()
        return CancelOutcome(
            job_id=job_id,
            status=JOB_CANCELLED,
            cancelled_now=True,
            requested=True,
            already_terminal=False,
        )

    if job.status == JOB_RUNNING:
        if job.cancel_requested_at is None:
            job.cancel_requested_at = func.now()
            db.flush()
        return CancelOutcome(
            job_id=job_id,
            status=JOB_RUNNING,
            cancelled_now=False,
            requested=True,
            already_terminal=False,
        )

    return CancelOutcome(
        job_id=job_id,
        status=job.status,
        cancelled_now=False,
        requested=False,
        already_terminal=True,
    )


def mark_job_cancelled(db: Session, *, job_id: uuid.UUID, worker_id: str) -> bool:
    """The worker observing `cancel_requested_at` and standing down."""
    result = db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.claimed_by == worker_id,
            AnalysisJob.status == JOB_RUNNING,
        )
        .values(
            status=JOB_CANCELLED,
            completed_at=func.now(),
            completed_positions=0,
            **_release_lease(),
        )
    )
    return result.rowcount == 1


def retry_job(db: Session, *, job_id: uuid.UUID, extra_attempts: int = 1) -> AnalysisJob | None:
    """Explicit user retry of a failed or cancelled job.

    Raises `InvalidJobTransition` for anything else - notably a completed job,
    which is immutable. `max_attempts` is raised rather than `attempt_count`
    reset, so the attempt history stays honest.

    Note this is stricter than `ALLOWED_TRANSITIONS`. The state machine permits
    `running -> queued`, but that edge belongs to the stale sweep and automatic
    requeue, which own the job's lease. A user asking to retry a job that is
    running right now is asking for the wrong thing - they want cancel.
    """
    job = db.execute(
        select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update()
    ).scalars().first()
    if job is None:
        return None

    if job.status not in RETRYABLE_FROM:
        raise InvalidJobTransition(job.id, job.status, JOB_QUEUED)
    assert_transition(job, JOB_QUEUED)

    job.status = JOB_QUEUED
    job.max_attempts = max(job.max_attempts, job.attempt_count + extra_attempts)
    job.available_at = func.now()
    job.cancel_requested_at = None
    job.completed_at = None
    job.completed_positions = 0
    job.error_code = None
    job.error_message = None
    job.claimed_by = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    db.flush()
    return job


def count_all_jobs_by_status(db: Session) -> dict[str, int]:
    """Queue depth across every batch, for operational reporting only."""
    rows: Iterable[tuple[str, int]] = db.execute(
        select(AnalysisJob.status, func.count()).group_by(AnalysisJob.status)
    ).all()
    return {status: count for status, count in rows}


def count_jobs_by_status(db: Session, batch_id: uuid.UUID) -> dict[str, int]:
    """Per-status job counts, for deriving batch state without loading rows."""
    rows: Iterable[tuple[str, int]] = db.execute(
        select(AnalysisJob.status, func.count())
        .where(AnalysisJob.batch_id == batch_id)
        .group_by(AnalysisJob.status)
    ).all()
    return {status: count for status, count in rows}


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CancelOutcome",
    "HeartbeatResult",
    "InvalidJobTransition",
    "assert_transition",
    "cancel_job",
    "claim_next_job",
    "complete_job",
    "count_all_jobs_by_status",
    "count_jobs_by_status",
    "create_batch",
    "enqueue_job",
    "fail_job",
    "find_active_job",
    "find_active_job_for_game",
    "find_stale_jobs",
    "latest_job_for_game",
    "get_batch",
    "get_job",
    "heartbeat_job",
    "latest_run_for_job",
    "list_jobs_for_batch",
    "list_jobs_for_games",
    "mark_job_cancelled",
    "requeue_job",
    "retry_job",
    "start_run_for_job",
]
