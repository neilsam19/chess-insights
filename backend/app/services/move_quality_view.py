"""Turns persisted rows into the move-quality API response.

The read side of Sprint 6. Nothing here writes, enqueues, or starts an engine -
it loads what the worker already produced and hands it to the pure derivation
in `move_quality`.

Two rules shape the whole module:

* **The current analysis is whatever the existing state resolution says it
  is.** This module does not decide which run to read; it asks
  `game_analysis_state` and reacts. A second definition of "current" would
  eventually disagree with the first, and the disagreement would show up as a
  game whose evaluation graph and move annotations describe different runs.

* **Derivation reads model rows, not the wire format.** The player-relative
  `PositionEvaluationOut.score` cannot express a delivered checkmate, because
  `mate 0` is signless and -1 * 0 is still 0. Raw rows plus ply parity can.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.game import Game
from app.repositories import analysis as analysis_repo
from app.schemas.analysis import CompleteAnalysisOut
from app.schemas.move_quality import (
    GameMoveQualityOut,
    MoveQualityOut,
    MoveQualitySummaryOut,
    NotGradableOut,
    PlayerScoreOut,
)
from app.services.analysis_view import game_analysis_state
from app.services.move_quality import (
    GRADABLE_COLORS,
    MOVE_QUALITY_VERSION,
    GameMoveQualitySummary,
    MoveQuality,
    PlayedMove,
    PlayerScore,
    StoredScore,
    derive_move_quality,
    summarize_moves,
)

UNKNOWN_COLOR_DETAIL = (
    "This game was saved under a player name that matched neither side, so "
    "there is no player whose moves can be graded."
)


def _player_score_out(score: PlayerScore | None) -> PlayerScoreOut | None:
    if score is None:
        return None
    return PlayerScoreOut(kind=score.kind, cp=score.cp, mate_distance=score.mate_distance)


def _move_out(fact: MoveQuality) -> MoveQualityOut:
    return MoveQualityOut(
        ply=fact.ply,
        move_number=fact.move_number,
        side=fact.side,
        san=fact.san,
        played_move_uci=fact.played_move_uci,
        best_move_uci=fact.best_move_uci,
        is_exact_best_move=fact.is_exact_best_move,
        score_before=_player_score_out(fact.score_before),
        score_after=_player_score_out(fact.score_after),
        centipawn_loss=fact.centipawn_loss,
        quality=fact.quality,
        special_classification=fact.special_classification,
        unscored_reason=fact.unscored_reason,
    )


def _summary_out(summary: GameMoveQualitySummary) -> MoveQualitySummaryOut:
    return MoveQualitySummaryOut(**vars(summary))


def load_stored_scores(db: Session, run_id: uuid.UUID) -> list[StoredScore]:
    """Raw evaluations for a run, as derivation inputs.

    One query. The canonical White-perspective values are passed through
    untouched; perspective and mate attribution are the derivation's job.
    """
    return [
        StoredScore(
            ply=evaluation.ply,
            score_cp_white=evaluation.score_cp_white,
            mate_in_white=evaluation.mate_in_white,
            best_move_uci=evaluation.best_move_uci,
        )
        for evaluation in analysis_repo.list_position_evaluations_for_run(db, run_id)
    ]


def load_played_moves(game: Game) -> list[PlayedMove]:
    """The game's moves, already ordered by ply by the relationship."""
    return [
        PlayedMove(ply=move.ply, san=move.san, uci=move.uci) for move in game.moves
    ]


def game_move_quality(db: Session, game: Game) -> object:
    """Move quality for one game, or why there is none.

    Colour is checked before analysis state: a game with no identifiable player
    can never be graded, however often it is analysed, so reporting
    "not analyzed" would invite the user to queue work that cannot help them.
    """
    if game.user_color not in GRADABLE_COLORS:
        return NotGradableOut(game_id=game.id, detail=UNKNOWN_COLOR_DETAIL)

    state = game_analysis_state(db, game)
    if not isinstance(state, CompleteAnalysisOut):
        # Queued, running, failed, cancelled, or never analysed. Returned
        # verbatim so `can_cancel` and `can_retry` can never drift from the
        # analysis endpoint's answer.
        return state

    facts = derive_move_quality(
        load_played_moves(game),
        # Reloaded as model rows rather than reusing the response's
        # evaluations, which are the wire format this derivation must not
        # depend on. One extra query on an endpoint that is not polled.
        load_stored_scores(db, state.run_id),
        game.user_color,
    )

    return GameMoveQualityOut(
        game_id=game.id,
        analysis_run_id=state.run_id,
        job_id=state.job_id,
        player_color=game.user_color,
        engine=state.engine,
        move_quality_version=MOVE_QUALITY_VERSION,
        summary=_summary_out(summarize_moves(facts)),
        moves=[_move_out(fact) for fact in facts],
    )


__all__ = ["game_move_quality", "load_played_moves", "load_stored_scores"]
