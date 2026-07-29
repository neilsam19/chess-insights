"""Coordinates one complete engine analysis of one saved game.

Transaction shape is the point of this module. Upload import is a single
atomic transaction; engine analysis deliberately is not, because a database
transaction must never stay open while Stockfish is thinking. Instead the run
lifecycle is three short transactions:

    1. create the run as "running"        (short, committed)
    2. ... analyze, holding no transaction ...
    3. save evaluations + mark complete   (short, committed)
       or mark failed                     (short, committed)

Sessions come from an injected factory rather than `SessionLocal` directly, so
tests can supply the rollback-scoped session from `conftest` and never leave
rows behind in the developer's database.
"""

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, ContextManager, Iterator

import chess
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.analysis_run import STATUS_COMPLETE, STATUS_RUNNING
from app.models.game import Game
from app.models.move import Move
from app.repositories import analysis as analysis_repo
from app.services.engine import (
    ChessEngine,
    EngineError,
    RawEvaluation,
    build_stockfish_engine,
    engine_session,
)

SessionFactory = Callable[[], ContextManager[Session]]
EngineFactory = Callable[[], ChessEngine]

MAX_ERROR_MESSAGE_LENGTH = 500


class AnalysisError(Exception):
    """Base class for analysis coordination problems."""


class GameNotFoundError(AnalysisError):
    pass


class InvalidGameDataError(AnalysisError):
    """Stored game data failed validation - the engine is never started."""


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


def build_positions(game: Game, moves: list[Move]) -> list[str]:
    """Ordered FENs for plies 0..N, taken from stored positions.

    Uses the FENs recorded at import rather than replaying `raw_pgn`, so
    analysis reflects exactly the positions the rest of the app displays.
    Any inconsistency is a hard error - a partial or malformed move sequence
    is never silently analyzed.
    """
    if not moves:
        # Zero-move games are rejected rather than analyzed as a bare starting
        # position: there is nothing to learn and no move to attribute.
        raise InvalidGameDataError("This game has no moves to analyze.")

    if len(moves) != game.move_count:
        raise InvalidGameDataError(
            f"Stored move count does not match the game: expected {game.move_count}, "
            f"found {len(moves)}."
        )

    plies = [move.ply for move in moves]
    if plies != list(range(1, len(moves) + 1)):
        raise InvalidGameDataError("Stored moves are missing, unordered, or non-contiguous.")

    positions = [moves[0].fen_before] + [move.fen_after for move in moves]

    for index, fen in enumerate(positions):
        if not fen:
            raise InvalidGameDataError(f"Missing position for ply {index}.")
        try:
            chess.Board(fen)
        except ValueError as exc:
            raise InvalidGameDataError(f"Invalid position at ply {index}: {exc}") from exc

    if len(positions) != game.move_count + 1:
        raise InvalidGameDataError(
            f"Expected {game.move_count + 1} positions, built {len(positions)}."
        )

    return positions


def _sanitize_error(exc: Exception) -> str:
    """Keep engine detail useful but never leak paths, secrets, or tracebacks."""
    if isinstance(exc, EngineError):
        message = str(exc) or "Engine analysis failed."
    else:
        message = "Analysis failed unexpectedly."
    return message[:MAX_ERROR_MESSAGE_LENGTH]


@contextmanager
def _no_op_close(session: Session) -> Iterator[Session]:
    yield session


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
        game = db.get(Game, game_id)
        if game is None:
            raise GameNotFoundError(f"Game not found: {game_id}")

        moves = list(
            db.execute(
                select(Move).where(Move.game_id == game_id).order_by(Move.ply)
            ).scalars()
        )
        positions = build_positions(game, moves)
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
            evaluations: list[tuple[int, RawEvaluation]] = [
                (ply, engine.evaluate(fen, requested_depth))
                for ply, fen in enumerate(positions)
            ]

            if len(evaluations) != expected_count:
                raise InvalidGameDataError(
                    f"Expected {expected_count} evaluations, produced {len(evaluations)}."
                )
        except Exception as exc:
            _mark_failed(session_factory, run_id, _sanitize_error(exc))
            raise

    # --- Persist everything at once, then complete. ----------------------
    # Evaluations are accumulated in memory and written only after the whole
    # game succeeds, so a failed run never leaves partial rows that could be
    # mistaken for usable analysis.
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
        _mark_failed(session_factory, run_id, _sanitize_error(exc))
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
