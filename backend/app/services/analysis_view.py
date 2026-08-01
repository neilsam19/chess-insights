"""Turns persisted analysis rows into the API's response shape.

Two things happen here and nowhere else:

* the canonical White-perspective score is converted to the saved player's
  perspective, and
* centipawn and mate scores are kept as distinct types.

Storage stays canonical; perspective is a presentation concern, in the same
way aggregate statistics are always derived rather than stored.
"""

from sqlalchemy.orm import Session

from app.models.analysis_batch import AnalysisBatch
from app.models.analysis_job import (
    JOB_ACTIVE_STATUSES,
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import STATUS_CANCELLED, STATUS_RUNNING, AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis as analysis_repo
from app.repositories import analysis_jobs as jobs_repo
from app.repositories.analysis_jobs import RETRYABLE_FROM
from app.schemas.analysis import (
    BatchCountsOut,
    BatchStatusOut,
    CancelledAnalysisOut,
    CompleteAnalysisOut,
    EngineMetadataOut,
    FailedAnalysisOut,
    JobProgressOut,
    JobStatusOut,
    NotAnalyzedOut,
    PositionEvaluationOut,
    QueuedAnalysisOut,
    RunningAnalysisOut,
    ScoreOut,
)
from app.services.analysis_batches import derive_batch_status

WHITE = "white"
BLACK = "black"

BATCH_COUNT_KEYS = (JOB_QUEUED, JOB_RUNNING, JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED)


def perspective_for_game(game: Game) -> str:
    """Which side scores should be reported from.

    `user_color` is "unknown" when the confirmed player name matched neither
    side, which is reachable for any upload saved under a name absent from
    the file. There is no player perspective to take in that case, so the
    canonical White view is reported as-is rather than rejecting the game.
    """
    return BLACK if game.user_color == BLACK else WHITE


def _score_out(
    score_cp_white: int | None, mate_in_white: int | None, *, invert: bool
) -> ScoreOut:
    sign = -1 if invert else 1
    if mate_in_white is not None:
        return ScoreOut(type="mate", value=sign * mate_in_white)
    if score_cp_white is not None:
        return ScoreOut(type="cp", value=sign * score_cp_white)
    # The database check constraint makes this unreachable for stored rows.
    raise ValueError("Stored evaluation has neither a centipawn nor a mate score.")


def evaluation_out(
    evaluation: PositionEvaluation, *, invert: bool
) -> PositionEvaluationOut:
    return PositionEvaluationOut(
        ply=evaluation.ply,
        score=_score_out(
            evaluation.score_cp_white, evaluation.mate_in_white, invert=invert
        ),
        white_score=_score_out(
            evaluation.score_cp_white, evaluation.mate_in_white, invert=False
        ),
        best_move_uci=evaluation.best_move_uci,
        depth_reached=evaluation.depth_reached,
        nodes=evaluation.nodes,
        time_ms=evaluation.time_ms,
    )


def complete_analysis_out(
    game: Game,
    run: AnalysisRun,
    evaluations: list[PositionEvaluation],
    job: AnalysisJob | None = None,
) -> CompleteAnalysisOut:
    perspective = perspective_for_game(game)
    invert = perspective == BLACK

    return CompleteAnalysisOut(
        game_id=game.id,
        run_id=run.id,
        job_id=run.analysis_job_id,
        batch_id=job.batch_id if job is not None else None,
        engine=EngineMetadataOut(
            name=run.engine_name,
            version=run.engine_version,
            binary_sha256=run.engine_binary_sha256,
            requested_depth=run.requested_depth,
            options=dict(run.engine_options or {}),
        ),
        started_at=run.started_at,
        completed_at=run.completed_at,
        perspective=perspective,
        evaluations=[evaluation_out(e, invert=invert) for e in evaluations],
    )


def progress_out(job: AnalysisJob) -> JobProgressOut:
    total = job.total_positions or 0
    done = min(job.completed_positions or 0, total)
    return JobProgressOut(
        completed_positions=done,
        total_positions=total,
        # Rounded once, here, so every client shows the same number.
        percent=int(round(100 * done / total)) if total else 0,
        attempt=job.attempt_count,
        max_attempts=job.max_attempts,
    )


def job_status_out(job: AnalysisJob) -> JobStatusOut:
    return JobStatusOut(
        job_id=job.id,
        game_id=job.game_id,
        batch_id=job.batch_id,
        status=job.status,
        progress=progress_out(job),
        queued_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_code=job.error_code,
        error_message=job.error_message,
        cancel_requested=job.cancel_requested_at is not None,
        can_cancel=(
            job.status in JOB_ACTIVE_STATUSES and job.cancel_requested_at is None
        ),
        can_retry=job.status in RETRYABLE_FROM,
    )


def batch_status_out(db: Session, batch: AnalysisBatch) -> BatchStatusOut:
    """A batch's live state, derived from its jobs rather than read from it.

    Progress is aggregated across every job, so one long game does not make the
    whole upload look stuck at 0%.
    """
    jobs = jobs_repo.list_jobs_for_batch(db, batch.id)
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1

    total_positions = sum(job.total_positions for job in jobs)
    completed_positions = sum(
        job.total_positions if job.status == JOB_COMPLETE else job.completed_positions
        for job in jobs
    )
    status = derive_batch_status(counts)

    return BatchStatusOut(
        batch_id=batch.id,
        upload_id=batch.upload_id,
        status=status,
        counts=BatchCountsOut(**{key: counts.get(key, 0) for key in BATCH_COUNT_KEYS}),
        total_jobs=len(jobs),
        completed_positions=completed_positions,
        total_positions=total_positions,
        percent=(
            int(round(100 * completed_positions / total_positions))
            if total_positions
            else 0
        ),
        games_complete=counts.get(JOB_COMPLETE, 0),
        created_at=batch.created_at,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        cancel_requested=batch.cancel_requested_at is not None,
        can_cancel=any(job.status in JOB_ACTIVE_STATUSES for job in jobs),
        failed_game_ids=[job.game_id for job in jobs if job.status == JOB_FAILED],
    )


def game_analysis_state(db: Session, game: Game) -> object:
    """The current analysis state of one game, as the API reports it.

    Order of precedence, and why:

    1. **Active job** - something is happening right now, and that is the most
       useful thing to show even if an older analysis exists.
    2. **Completed run** - real results beat a later failed retry. The data is
       there; hiding it behind an error would be worse than useless.
    3. **Terminal job** - failed or cancelled, with whether retry is offered.
    4. **Legacy run** - a Sprint 4 run with no job behind it.
    5. **Nothing** - `not_analyzed`, which is derived and never stored.
    """
    active = jobs_repo.find_active_job_for_game(db, game.id)
    if active is not None:
        if active.status == JOB_QUEUED:
            return QueuedAnalysisOut(
                game_id=game.id,
                job_id=active.id,
                batch_id=active.batch_id,
                queued_at=active.created_at,
                progress=progress_out(active),
            )

        run = jobs_repo.latest_run_for_job(db, active.id)
        cancelling = active.cancel_requested_at is not None
        return RunningAnalysisOut(
            game_id=game.id,
            job_id=active.id,
            batch_id=active.batch_id,
            run_id=run.id if run is not None else None,
            started_at=active.started_at or active.created_at,
            progress=progress_out(active),
            cancel_requested=cancelling,
            # Asking twice achieves nothing - it is already stopping.
            can_cancel=not cancelling,
        )

    complete_run = analysis_repo.get_latest_complete_run_for_game(db, game.id)
    if complete_run is not None:
        evaluations = analysis_repo.list_position_evaluations_for_run(db, complete_run.id)
        job = (
            jobs_repo.get_job(db, complete_run.analysis_job_id)
            if complete_run.analysis_job_id
            else None
        )
        return complete_analysis_out(game, complete_run, evaluations, job)

    latest_job = jobs_repo.latest_job_for_game(db, game.id)
    if latest_job is not None:
        run = jobs_repo.latest_run_for_job(db, latest_job.id)
        if latest_job.status == JOB_CANCELLED:
            return CancelledAnalysisOut(
                game_id=game.id,
                job_id=latest_job.id,
                batch_id=latest_job.batch_id,
                run_id=run.id if run is not None else None,
                cancelled_at=latest_job.completed_at,
            )
        return FailedAnalysisOut(
            game_id=game.id,
            job_id=latest_job.id,
            batch_id=latest_job.batch_id,
            run_id=run.id if run is not None else None,
            started_at=latest_job.started_at,
            completed_at=latest_job.completed_at,
            error_code=latest_job.error_code,
            error_message=latest_job.error_message,
            can_retry=latest_job.status in RETRYABLE_FROM,
        )

    # Sprint 4 left runs with no job behind them; they are still reportable.
    legacy = analysis_repo.get_latest_analysis_run_for_game(db, game.id)
    if legacy is not None:
        if legacy.status == STATUS_RUNNING:
            return RunningAnalysisOut(
                game_id=game.id,
                run_id=legacy.id,
                started_at=legacy.started_at,
                can_cancel=False,
            )
        if legacy.status == STATUS_CANCELLED:
            return CancelledAnalysisOut(
                game_id=game.id,
                job_id=legacy.analysis_job_id or legacy.id,
                run_id=legacy.id,
                cancelled_at=legacy.completed_at,
                can_retry=False,
            )
        return FailedAnalysisOut(
            game_id=game.id,
            run_id=legacy.id,
            started_at=legacy.started_at,
            completed_at=legacy.completed_at,
            error_message=legacy.error_message,
        )

    return NotAnalyzedOut(game_id=game.id)
