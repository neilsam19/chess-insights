"""Pure engine execution: positions in, evaluations out.

Nothing here decides *whether* a game should be analyzed, reuses a previous
result, or owns a job. It loads validated positions and runs the engine over
them. Both the synchronous Sprint 4 path and the Sprint 5 worker use these
functions, so there is exactly one implementation of "analyze a game" even
while two callers still exist.

No function here opens a transaction while the engine is running, and none of
them commits - the caller owns transaction boundaries.
"""

import uuid
from typing import Callable

import chess
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.move import Move
from app.services.engine import ChessEngine, RawEvaluation

MAX_ERROR_MESSAGE_LENGTH = 500

# Called after each position with (completed_count, total_count). Returning
# False asks the executor to stop - used for lease loss and, from Checkpoint 5,
# cancellation. Stopping is always at a position boundary, never mid-position.
ProgressCallback = Callable[[int, int], bool]


class AnalysisError(Exception):
    """Base class for analysis coordination problems."""


class GameNotFoundError(AnalysisError):
    pass


class InvalidGameDataError(AnalysisError):
    """Stored game data failed validation - the engine is never started."""


class AnalysisAbandoned(AnalysisError):
    """Execution stopped early because the caller asked it to.

    Distinct from a failure: nothing went wrong with the engine, so the caller
    decides what this means - a lost lease, or a user cancelling.
    """


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


def load_game_positions(db: Session, game_id: uuid.UUID) -> tuple[Game, list[str]]:
    """Read a game and its validated positions in one short transaction."""
    game = db.get(Game, game_id)
    if game is None:
        raise GameNotFoundError(f"Game not found: {game_id}")

    moves = list(
        db.execute(select(Move).where(Move.game_id == game_id).order_by(Move.ply)).scalars()
    )
    return game, build_positions(game, moves)


def evaluate_positions(
    engine: ChessEngine,
    positions: list[str],
    depth: int,
    *,
    on_progress: ProgressCallback | None = None,
) -> list[tuple[int, RawEvaluation]]:
    """Evaluate every position, accumulating results in memory.

    Nothing is persisted here. Results are written only after the whole game
    succeeds, so a failed or abandoned run never leaves partial rows that
    could be mistaken for usable analysis - which is what keeps Sprint 6's
    inputs trustworthy.

    Raises `AnalysisAbandoned` if `on_progress` returns False.
    """
    total = len(positions)
    evaluations: list[tuple[int, RawEvaluation]] = []

    for ply, fen in enumerate(positions):
        evaluations.append((ply, engine.evaluate(fen, depth)))

        if on_progress is not None and not on_progress(ply + 1, total):
            raise AnalysisAbandoned(
                f"Analysis stopped after {ply + 1} of {total} positions."
            )

    if len(evaluations) != total:  # pragma: no cover - defensive
        raise InvalidGameDataError(
            f"Expected {total} evaluations, produced {len(evaluations)}."
        )

    return evaluations


__all__ = [
    "AnalysisAbandoned",
    "AnalysisError",
    "GameNotFoundError",
    "InvalidGameDataError",
    "MAX_ERROR_MESSAGE_LENGTH",
    "ProgressCallback",
    "build_positions",
    "evaluate_positions",
    "load_game_positions",
]
