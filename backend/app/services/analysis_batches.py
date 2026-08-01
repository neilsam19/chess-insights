"""Whole-upload analysis: one request, one batch, many jobs.

Two things live here and nowhere else.

**Batch state derivation.** A batch has no state of its own - it is entirely a
function of its jobs. `derive_batch_status` is the single authority for that
function, and it is a pure function of status counts so it can be tested
exhaustively without a database. The `status` column is a cache written only by
`refresh_batch_status`; reads always derive, so the two can never disagree in a
way a user would see.

**Bulk enqueue.** Creating one batch and every job in a single transaction, and
deciding per game whether there is anything to do at all. Re-running a bulk
request after most games are analysed should queue almost nothing, which is
what makes the action safe to press twice.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis_batch import (
    BATCH_CANCELLED,
    BATCH_COMPLETE,
    BATCH_FAILED,
    BATCH_PARTIAL_FAILED,
    BATCH_QUEUED,
    BATCH_RUNNING,
    AnalysisBatch,
)
from app.models.analysis_job import (
    JOB_ACTIVE_STATUSES,
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import STATUS_COMPLETE, AnalysisRun
from app.models.game import Game
from app.models.upload import Upload
from app.repositories import analysis_jobs as jobs_repo
from app.services.analysis_config import (
    compute_engine_config_hash,
    compute_job_config_hash,
)
from app.services.analysis_enqueue import run_matches_config
from app.services.engine import resolve_engine_config

SKIPPED_COMPLETE = "skipped_complete"
ALREADY_ACTIVE = "already_active"
INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class BulkEnqueueOutcome:
    batch: AnalysisBatch | None
    queued: int
    skipped_complete: int
    already_active: int
    ineligible: int
    total_games: int
    ineligible_game_ids: list[uuid.UUID] = field(default_factory=list)


def derive_batch_status(counts: dict[str, int]) -> str:
    """A batch's status, as a pure function of its jobs' statuses.

    Order matters and is deliberate:

    * A batch with **no jobs at all** is complete. That is not a degenerate
      case - it is what a bulk request returns when every game already has a
      matching analysis, and reporting it as anything else would be wrong.
    * `partial_failed` outranks `failed`: if anything succeeded, saying the
      whole batch failed would hide real results.
    * `cancelled` is checked after failures, because a failure is information
      the user needs and a cancellation is something they already know about.
    """
    queued = counts.get(JOB_QUEUED, 0)
    running = counts.get(JOB_RUNNING, 0)
    complete = counts.get(JOB_COMPLETE, 0)
    failed = counts.get(JOB_FAILED, 0)
    cancelled = counts.get(JOB_CANCELLED, 0)
    total = queued + running + complete + failed + cancelled

    if total == 0:
        return BATCH_COMPLETE

    if running > 0:
        return BATCH_RUNNING
    if queued > 0:
        # Still queued work: `queued` only while nothing has happened yet.
        return BATCH_QUEUED if queued == total else BATCH_RUNNING

    # Everything is terminal from here.
    if failed > 0 and complete > 0:
        return BATCH_PARTIAL_FAILED
    if failed > 0:
        return BATCH_FAILED
    if cancelled > 0:
        return BATCH_CANCELLED
    return BATCH_COMPLETE


def batch_status(db: Session, batch: AnalysisBatch) -> str:
    return derive_batch_status(jobs_repo.count_jobs_by_status(db, batch.id))


def refresh_batch_status(db: Session, batch_id: uuid.UUID) -> str | None:
    """Recompute and store a batch's status. The only writer of that column.

    Called after any job transition. `completed_at` is set when the batch
    first reaches a terminal state and cleared if a retry reopens it, so it
    always means what it says.
    """
    batch = db.get(AnalysisBatch, batch_id)
    if batch is None:
        return None

    status = batch_status(db, batch)
    batch.status = status

    from sqlalchemy import func

    if status in {BATCH_RUNNING} and batch.started_at is None:
        batch.started_at = func.now()

    if status in {BATCH_COMPLETE, BATCH_PARTIAL_FAILED, BATCH_FAILED, BATCH_CANCELLED}:
        if batch.completed_at is None:
            batch.completed_at = func.now()
    else:
        batch.completed_at = None

    db.flush()
    return status


def enqueue_upload_analysis(
    db: Session, upload: Upload, *, depth: int | None = None
) -> BulkEnqueueOutcome:
    """Queue every game in an upload that still needs analysing.

    All-or-nothing: the batch and every job are created in the caller's
    transaction, so a failure part-way leaves nothing behind rather than half
    an upload queued.

    Eligibility here is the cheap check - a game with no moves. Single-game
    enqueue validates stored positions fully because it can afford to and can
    report 422 for one game; doing that for every game in an upload would mean
    loading every move of every game just to answer "should I queue this?".
    Deeper corruption surfaces as a failed job, which is visible and
    retryable.

    Raises `EngineUnavailableError` if the engine is unusable, before creating
    anything.
    """
    config = resolve_engine_config(depth=depth)
    engine_config_hash = compute_engine_config_hash(config)

    games = list(
        db.execute(
            select(Game).where(Game.upload_id == upload.id).order_by(Game.game_index)
        ).scalars()
    )
    game_ids = [game.id for game in games]

    # Three queries rather than three per game: reuse and duplicate checks are
    # answered in memory from here.
    complete_runs: dict[uuid.UUID, list[AnalysisRun]] = {}
    if game_ids:
        for run in db.execute(
            select(AnalysisRun)
            .where(
                AnalysisRun.game_id.in_(game_ids),
                AnalysisRun.status == STATUS_COMPLETE,
            )
            .order_by(AnalysisRun.created_at.desc())
        ).scalars():
            complete_runs.setdefault(run.game_id, []).append(run)

    active_configs: set[tuple[uuid.UUID, str]] = set()
    if game_ids:
        for job in db.execute(
            select(AnalysisJob).where(
                AnalysisJob.game_id.in_(game_ids),
                AnalysisJob.status.in_(JOB_ACTIVE_STATUSES),
            )
        ).scalars():
            active_configs.add((job.game_id, job.config_hash))

    queued: list[Game] = []
    skipped_complete = 0
    already_active = 0
    ineligible: list[uuid.UUID] = []

    for game in games:
        if not game.move_count:
            ineligible.append(game.id)
            continue

        job_config_hash = compute_job_config_hash(engine_config_hash, game.game_hash)

        if any(
            run_matches_config(run, engine_config_hash)
            for run in complete_runs.get(game.id, [])
        ):
            skipped_complete += 1
            continue

        if (game.id, job_config_hash) in active_configs:
            already_active += 1
            continue

        queued.append(game)

    if not queued:
        # Nothing to do. Deliberately no empty batch: it would show up in the
        # UI as a thing that happened, when in fact nothing did.
        return BulkEnqueueOutcome(
            batch=None,
            queued=0,
            skipped_complete=skipped_complete,
            already_active=already_active,
            ineligible=len(ineligible),
            total_games=len(games),
            ineligible_game_ids=ineligible,
        )

    batch = jobs_repo.create_batch(
        db,
        upload_id=upload.id,
        engine_config_hash=engine_config_hash,
        requested_depth=config.depth,
        engine_options=config.options,
    )
    for game in queued:
        jobs_repo.enqueue_job(
            db,
            batch_id=batch.id,
            game_id=game.id,
            config_hash=compute_job_config_hash(engine_config_hash, game.game_hash),
            total_positions=game.move_count + 1,
        )

    refresh_batch_status(db, batch.id)

    return BulkEnqueueOutcome(
        batch=batch,
        queued=len(queued),
        skipped_complete=skipped_complete,
        already_active=already_active,
        ineligible=len(ineligible),
        total_games=len(games),
        ineligible_game_ids=ineligible,
    )


def cancel_batch(db: Session, batch: AnalysisBatch) -> int:
    """Ask every unfinished job in a batch to stop.

    Queued jobs are cancelled outright; running ones are asked and stop at
    their next position boundary. Finished jobs are left alone - their results
    are real and cancelling a batch is not a reason to discard them.
    """
    from sqlalchemy import func

    affected = 0
    for job in jobs_repo.list_jobs_for_batch(db, batch.id):
        if job.status in JOB_ACTIVE_STATUSES:
            jobs_repo.cancel_job(db, job_id=job.id)
            affected += 1

    if batch.cancel_requested_at is None:
        batch.cancel_requested_at = func.now()

    refresh_batch_status(db, batch.id)
    return affected


__all__ = [
    "ALREADY_ACTIVE",
    "INELIGIBLE",
    "SKIPPED_COMPLETE",
    "BulkEnqueueOutcome",
    "batch_status",
    "cancel_batch",
    "derive_batch_status",
    "enqueue_upload_analysis",
    "refresh_batch_status",
]
