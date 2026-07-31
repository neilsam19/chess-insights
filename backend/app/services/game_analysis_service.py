"""Synchronous analysis of one saved game - the Sprint 4 request path.

This module is scheduled for deletion. Checkpoint 6 replaces the endpoint it
serves with durable job creation, at which point the reuse and conflict rules
below move to the enqueue layer for good (ADR decision D4). Until then it
remains the only thing serving `POST /api/games/{id}/analysis`, so it keeps
working exactly as it did in Sprint 4.

What it no longer does is *execute* the analysis. Position loading and engine
iteration live in `analysis_executor`, shared with the worker, so there is one
implementation of the actual work even while two callers exist.

Transaction shape is the point of what remains. Upload import is a single
atomic transaction; engine analysis deliberately is not, because a database
transaction must never stay open while Stockfish is thinking:

    1. create the run as "running"        (short, committed)
    2. ... analyze, holding no transaction ...
    3. save evaluations + mark complete   (short, committed)
       or mark failed                     (short, committed)
"""

import uuid
from dataclasses import dataclass
from typing import Callable, ContextManager

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.analysis_run import STATUS_COMPLETE, STATUS_RUNNING
from app.repositories import analysis as analysis_repo
from app.services.analysis_executor import (
    MAX_ERROR_MESSAGE_LENGTH,
    AnalysisError,
    GameNotFoundError,
    InvalidGameDataError,
    build_positions,
    evaluate_positions,
    load_game_positions,
)
from app.services.engine import (
    ChessEngine,
    EngineError,
    RawEvaluation,
    build_stockfish_engine,
    engine_session,
)

SessionFactory = Callable[[], ContextManager[Session]]
EngineFactory = Callable[[], ChessEngine]


class AnalysisAlreadyRunningError(AnalysisError):
    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        super().__init__(f"Analysis is already running for this game: {run_id}")


@dataclass(frozen=True)
class AnalysisResult:
    run_id: uuid.UUID
    created: bool
    """False when an existing complete run was returned instead of re-analyzing."""


def _default_session() -> ContextManager[Session]:
    return SessionLocal()


def sanitize_error(exc: Exception) -> str:
    """Keep engine detail useful but never leak paths, secrets, or tracebacks."""
    if isinstance(exc, EngineError):
        message = str(exc) or "Engine analysis failed."
    else:
        message = "Analysis failed unexpectedly."
    return message[:MAX_ERROR_MESSAGE_LENGTH]


# Retained under the old private name: several tests and the worker's error
# handling refer to it, and renaming it buys nothing.
_sanitize_error = sanitize_error


def analyze_game(
    game_id: uuid.UUID,
    *,
    session_factory: SessionFactory = _default_session,
    engine_factory: EngineFactory = build_stockfish_engine,
    depth: int | None = None,
) -> AnalysisResult:
    """Analyze one saved game from ply 0 to ply N.

    Returns the run id. Raises `GameNotFoundError`, `InvalidGameDataError`,
    `AnalysisAlreadyRunningError`, or an `EngineError` - engine startup
    failures propagate without creating a run, because a misconfigured server
    is not a failed analysis.
    """
    requested_depth = depth if depth is not None else settings.stockfish_depth

    # --- Read and validate before touching the engine -------------------
    with session_factory() as db:
        game, positions = load_game_positions(db, game_id)
        expected_count = game.move_count + 1

        latest = analysis_repo.get_latest_analysis_run_for_game(db, game_id)
        if latest is not None:
            if latest.status == STATUS_RUNNING:
                raise AnalysisAlreadyRunningError(latest.id)
            if latest.status == STATUS_COMPLETE:
                # Already analyzed - return the existing result rather than
                # burning engine time. Re-analysis is a Sprint 5 concern.
                return AnalysisResult(run_id=latest.id, created=False)

    # --- Start the engine. A startup failure creates no run. -------------
    with engine_session(engine_factory) as engine:
        identity = engine.identify()

        # Short transaction: record that analysis has begun.
        with session_factory() as db:
            latest = analysis_repo.get_latest_analysis_run_for_game(db, game_id)
            if latest is not None and latest.status == STATUS_RUNNING:
                raise AnalysisAlreadyRunningError(latest.id)

            run = analysis_repo.create_analysis_run(
                db,
                game_id=game_id,
                identity=identity,
                requested_depth=requested_depth,
            )
            run_id = run.id
            db.commit()

        # --- No transaction is held while the engine works. --------------
        try:
            evaluations: list[tuple[int, RawEvaluation]] = evaluate_positions(
                engine, positions, requested_depth
            )

            if len(evaluations) != expected_count:
                raise InvalidGameDataError(
                    f"Expected {expected_count} evaluations, produced {len(evaluations)}."
                )
        except Exception as exc:
            _mark_failed(session_factory, run_id, sanitize_error(exc))
            raise

    # --- Persist everything at once, then complete. ----------------------
    try:
        with session_factory() as db:
            run = analysis_repo.get_analysis_run(db, run_id)
            if run is None:  # pragma: no cover - the run was just created
                raise AnalysisError("Analysis run disappeared before completion.")

            analysis_repo.save_position_evaluations(
                db, run_id=run_id, evaluations=evaluations
            )
            analysis_repo.mark_analysis_run_complete(db, run)
            db.commit()
    except Exception as exc:
        _mark_failed(session_factory, run_id, sanitize_error(exc))
        raise

    return AnalysisResult(run_id=run_id, created=True)


def _mark_failed(session_factory: SessionFactory, run_id: uuid.UUID, message: str) -> None:
    """Record failure in its own short transaction.

    Deliberately swallows secondary errors: the original failure is what the
    caller needs to see, not a follow-on database problem while recording it.
    """
    try:
        with session_factory() as db:
            db.rollback()  # discard anything left over from the failed attempt
            run = analysis_repo.get_analysis_run(db, run_id)
            if run is not None:
                analysis_repo.mark_analysis_run_failed(db, run, message)
                db.commit()
    except Exception:  # noqa: BLE001 - never mask the original failure
        pass


__all__ = [
    "AnalysisAlreadyRunningError",
    "AnalysisError",
    "AnalysisResult",
    "EngineFactory",
    "GameNotFoundError",
    "InvalidGameDataError",
    "SessionFactory",
    "analyze_game",
    "build_positions",
    "sanitize_error",
]
