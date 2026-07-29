"""Turns persisted analysis rows into the API's response shape.

Two things happen here and nowhere else:

* the canonical White-perspective score is converted to the saved player's
  perspective, and
* centipawn and mate scores are kept as distinct types.

Storage stays canonical; perspective is a presentation concern, in the same
way aggregate statistics are always derived rather than stored.
"""

from app.models.analysis_run import AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.schemas.analysis import (
    CompleteAnalysisOut,
    EngineMetadataOut,
    PositionEvaluationOut,
    ScoreOut,
)

WHITE = "white"
BLACK = "black"


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
    game: Game, run: AnalysisRun, evaluations: list[PositionEvaluation]
) -> CompleteAnalysisOut:
    perspective = perspective_for_game(game)
    invert = perspective == BLACK

    return CompleteAnalysisOut(
        game_id=game.id,
        run_id=run.id,
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
