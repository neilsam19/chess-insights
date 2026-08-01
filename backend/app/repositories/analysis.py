import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.analysis_run import (
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_RUNNING,
    AnalysisRun,
)
from app.models.position_evaluation import PositionEvaluation
from app.services.engine import EngineIdentity, RawEvaluation


def create_analysis_run(
    db: Session,
    *,
    game_id: uuid.UUID,
    identity: EngineIdentity,
    requested_depth: int,
) -> AnalysisRun:
    """Record a started run. Caller owns the transaction boundary."""
    now = datetime.now(timezone.utc)
    run = AnalysisRun(
        game_id=game_id,
        status=STATUS_RUNNING,
        engine_name=identity.name,
        engine_version=identity.version,
        engine_binary_sha256=identity.binary_sha256,
        requested_depth=requested_depth,
        engine_options=dict(identity.options),
        created_at=now,
        started_at=now,
    )
    db.add(run)
    db.flush()  # populate run.id without committing
    return run


def get_analysis_run(db: Session, run_id: uuid.UUID) -> AnalysisRun | None:
    return db.get(AnalysisRun, run_id)


def get_latest_analysis_run_for_game(db: Session, game_id: uuid.UUID) -> AnalysisRun | None:
    """The most recent run, which determines the game's current analysis state.

    Ordered by created_at with id as a tiebreaker so two runs created in the
    same instant still resolve deterministically.
    """
    stmt = (
        select(AnalysisRun)
        .where(AnalysisRun.game_id == game_id)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_latest_complete_run_for_game(db: Session, game_id: uuid.UUID) -> AnalysisRun | None:
    """The newest run that actually produced evaluations.

    Distinct from `get_latest_analysis_run_for_game`: a failed retry is more
    recent but has no results, and hiding a good analysis behind it would be
    worse than useless.
    """
    stmt = (
        select(AnalysisRun)
        .where(AnalysisRun.game_id == game_id, AnalysisRun.status == STATUS_COMPLETE)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_run_with_evaluations(db: Session, run_id: uuid.UUID) -> AnalysisRun | None:
    stmt = (
        select(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .options(selectinload(AnalysisRun.evaluations))
    )
    return db.execute(stmt).scalar_one_or_none()


def list_position_evaluations_for_run(
    db: Session, run_id: uuid.UUID
) -> list[PositionEvaluation]:
    stmt = (
        select(PositionEvaluation)
        .where(PositionEvaluation.analysis_run_id == run_id)
        .order_by(PositionEvaluation.ply)
    )
    return list(db.execute(stmt).scalars())


def save_position_evaluations(
    db: Session,
    *,
    run_id: uuid.UUID,
    evaluations: Iterable[tuple[int, RawEvaluation]],
) -> int:
    """Persist `(ply, evaluation)` pairs for a run.

    Written in one go after a whole game succeeds, so a failed run never
    leaves partial evaluations that could be mistaken for usable analysis.
    """
    count = 0
    for ply, evaluation in evaluations:
        db.add(
            PositionEvaluation(
                analysis_run_id=run_id,
                ply=ply,
                score_cp_white=evaluation.score_cp_white,
                mate_in_white=evaluation.mate_in_white,
                best_move_uci=evaluation.best_move_uci,
                depth_reached=evaluation.depth_reached,
                nodes=evaluation.nodes,
                time_ms=evaluation.time_ms,
            )
        )
        count += 1
    db.flush()
    return count


def mark_analysis_run_complete(db: Session, run: AnalysisRun) -> AnalysisRun:
    run.status = STATUS_COMPLETE
    run.completed_at = datetime.now(timezone.utc)
    run.error_message = None
    db.flush()
    return run


def mark_analysis_run_failed(db: Session, run: AnalysisRun, error_message: str) -> AnalysisRun:
    run.status = STATUS_FAILED
    run.completed_at = datetime.now(timezone.utc)
    run.error_message = error_message
    db.flush()
    return run


def mark_analysis_run_cancelled(
    db: Session, run: AnalysisRun, message: str | None = None
) -> AnalysisRun:
    """A run stopped by the user.

    Kept distinct from failed: nothing went wrong, and showing "analysis
    failed" for something the user chose would be misleading. Like a failed
    run, it holds zero evaluations.
    """
    run.status = STATUS_CANCELLED
    run.completed_at = datetime.now(timezone.utc)
    run.error_message = message
    db.flush()
    return run


__all__ = [
    "STATUS_CANCELLED",
    "STATUS_COMPLETE",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "create_analysis_run",
    "mark_analysis_run_cancelled",
    "get_analysis_run",
    "get_latest_analysis_run_for_game",
    "get_run_with_evaluations",
    "list_position_evaluations_for_run",
    "mark_analysis_run_complete",
    "mark_analysis_run_failed",
    "save_position_evaluations",
]
