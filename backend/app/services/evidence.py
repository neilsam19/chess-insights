"""Assembles the evidence contract from stored rows and CP1 derivations.

Read-only, like the rest of the sprint's read side: nothing is written, no job
is enqueued, and no engine is started.

The module adds no rules of its own. Which run is current, how a score is
converted to the player's perspective, and how a move is graded are all decided
elsewhere and reused here verbatim - so an explanation built on this payload
describes the same analysis the user is looking at, rather than a second
opinion assembled for the model's benefit.
"""

import uuid
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.analysis_run import AnalysisRun
from app.models.game import Game
from app.models.move import Move
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis as analysis_repo
from app.schemas.analysis import ScoreOut
from app.schemas.evidence import (
    EVIDENCE_CONTRACT_VERSION,
    EvidenceAssessment,
    EvidenceEngineView,
    EvidenceEnvelope,
    EvidenceGame,
    EvidenceItem,
    EvidenceMove,
    EvidenceProvenance,
)
from app.schemas.move_quality import PlayerScoreOut
from app.services.move_quality import (
    GRADABLE_COLORS,
    MOVE_QUALITY_VERSION,
    MoveQuality,
    PlayerScore,
    StoredScore,
    derive_move_quality,
)
from app.services.move_quality_view import load_played_moves


def _player_score_out(score: PlayerScore | None) -> PlayerScoreOut | None:
    if score is None:
        return None
    return PlayerScoreOut(kind=score.kind, cp=score.cp, mate_distance=score.mate_distance)


def _stored_score_out(evaluation: PositionEvaluation | None) -> ScoreOut | None:
    """The canonical White-perspective value, untouched.

    No perspective flip and no interpretation: this is the row as the worker
    wrote it, so a consumer can check the derived values against it.
    """
    if evaluation is None:
        return None
    if evaluation.mate_in_white is not None:
        return ScoreOut(type="mate", value=evaluation.mate_in_white)
    if evaluation.score_cp_white is not None:
        return ScoreOut(type="cp", value=evaluation.score_cp_white)
    # Unreachable for stored rows - the check constraint requires one of them.
    return None


def _game_out(game: Game) -> EvidenceGame:
    return EvidenceGame(
        game_id=game.id,
        upload_id=game.upload_id,
        game_index=game.game_index,
        white_name=game.white_name,
        black_name=game.black_name,
        result=game.result,
        played_date_text=game.played_date_text,
        player_color=game.user_color,
        player_result=game.user_result,
        eco=game.eco,
        opening_name=game.opening_name,
        opening_source=game.opening_source,
        opening_matched_ply=game.opening_matched_ply,
    )


def _provenance_out(run: AnalysisRun) -> EvidenceProvenance:
    return EvidenceProvenance(
        analysis_run_id=run.id,
        analysis_job_id=run.analysis_job_id,
        engine_name=run.engine_name,
        engine_version=run.engine_version,
        engine_binary_sha256=run.engine_binary_sha256,
        requested_depth=run.requested_depth,
        engine_options=dict(run.engine_options or {}),
        run_started_at=run.started_at,
        run_completed_at=run.completed_at,
        move_quality_version=MOVE_QUALITY_VERSION,
    )


def _item(
    *,
    game: Game,
    run: AnalysisRun,
    move: Move,
    fact: MoveQuality,
    before: PositionEvaluation | None,
    after: PositionEvaluation | None,
) -> EvidenceItem:
    return EvidenceItem(
        game=_game_out(game),
        move=EvidenceMove(
            move_row_id=move.id,
            ply=move.ply,
            move_number=fact.move_number,
            side=fact.side,
            san=move.san,
            uci=move.uci,
            fen_before=move.fen_before,
            fen_after=move.fen_after,
        ),
        engine=EvidenceEngineView(
            before_evaluation_id=before.id if before is not None else None,
            after_evaluation_id=after.id if after is not None else None,
            best_move_uci=fact.best_move_uci,
            is_exact_best_move=fact.is_exact_best_move,
            # From the *before* position: that is the search whose best move the
            # player's choice is compared against.
            depth_reached=before.depth_reached if before is not None else None,
            nodes=before.nodes if before is not None else None,
            time_ms=before.time_ms if before is not None else None,
        ),
        assessment=EvidenceAssessment(
            stored_white_score_before=_stored_score_out(before),
            stored_white_score_after=_stored_score_out(after),
            player_score_before=_player_score_out(fact.score_before),
            player_score_after=_player_score_out(fact.score_after),
            centipawn_loss=fact.centipawn_loss,
            quality=fact.quality,
            special_classification=fact.special_classification,
            unscored_reason=fact.unscored_reason,
        ),
        provenance=_provenance_out(run),
    )


def game_evidence(
    db: Session, game: Game, *, plies: Sequence[int] | None = None
) -> EvidenceEnvelope:
    """Evidence for a game's graded moves, or an empty envelope.

    Returns nothing rather than raising when there is no usable analysis or no
    identifiable player: absence of evidence is a normal state, and an empty
    envelope says so in the same shape a full one does.

    `plies` narrows the result to specific moves, for the common case of
    explaining the handful of positions a recommendation points at.
    """
    empty = EvidenceEnvelope(contract_version=EVIDENCE_CONTRACT_VERSION, evidence=[])

    if game.user_color not in GRADABLE_COLORS:
        return empty

    run = analysis_repo.get_latest_complete_run_for_game(db, game.id)
    if run is None:
        return empty

    evaluations = analysis_repo.list_position_evaluations_for_run(db, run.id)
    by_ply: dict[int, PositionEvaluation] = {e.ply: e for e in evaluations}
    moves_by_ply: dict[int, Move] = {move.ply: move for move in game.moves}

    facts = derive_move_quality(
        load_played_moves(game),
        [
            StoredScore(
                ply=evaluation.ply,
                score_cp_white=evaluation.score_cp_white,
                mate_in_white=evaluation.mate_in_white,
                best_move_uci=evaluation.best_move_uci,
            )
            for evaluation in evaluations
        ],
        game.user_color,
    )

    wanted = None if plies is None else set(plies)

    return EvidenceEnvelope(
        contract_version=EVIDENCE_CONTRACT_VERSION,
        evidence=[
            _item(
                game=game,
                run=run,
                move=moves_by_ply[fact.ply],
                fact=fact,
                before=by_ply.get(fact.ply - 1),
                after=by_ply.get(fact.ply),
            )
            for fact in facts
            if (wanted is None or fact.ply in wanted) and fact.ply in moves_by_ply
        ],
    )


def evidence_for_positions(
    db: Session, positions: Sequence[tuple[uuid.UUID, int]], games: Sequence[Game]
) -> EvidenceEnvelope:
    """Evidence for specific (game, ply) pairs - what a recommendation cites.

    `games` are supplied by the caller rather than fetched here, so ownership
    has already been established: a recommendation's evidence must never be
    able to reach a game the request did not ask about.
    """
    by_id = {game.id: game for game in games}
    wanted: dict[uuid.UUID, list[int]] = {}
    for game_id, ply in positions:
        wanted.setdefault(game_id, []).append(ply)

    items: list[EvidenceItem] = []
    for game_id, plies in wanted.items():
        game = by_id.get(game_id)
        if game is None:
            continue
        items.extend(game_evidence(db, game, plies=plies).evidence)

    return EvidenceEnvelope(contract_version=EVIDENCE_CONTRACT_VERSION, evidence=items)


__all__ = ["evidence_for_positions", "game_evidence"]
