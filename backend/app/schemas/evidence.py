"""The payload a future explanation feature would be given.

Nothing here calls a model, and nothing here is exposed over HTTP yet. What
this checkpoint fixes is the *shape*: a stable, self-contained record of what
happened in one position, assembled only from stored rows and the Sprint 6
derivations, so that a later sprint can hand it to a model without that model
needing to recompute anything or reach for the database.

Two properties are load-bearing.

**Every field traces to a source.** Each one is either a column of a stored row
or a value derived by `move_quality`, and the field comments say which. A
future explanation that cites a fact can therefore be validated against this
payload rather than trusted.

**Facts and prose never merge.** Generated text lives in the envelope beside
the evidence, never inside it. There is no free-text field anywhere in an
evidence item - only ids, numbers, enum values, and strings that came verbatim
out of the database (SAN, UCI, FEN, opening name). That is what makes it
impossible for a model's output to be mistaken later for something measured.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.analysis import ScoreOut
from app.schemas.move_quality import PlayerScoreOut

# Bumped when this payload's *shape* changes, independently of
# MOVE_QUALITY_VERSION, which identifies the rules that produced the values in
# it. A consumer needs both: one to parse, one to know what the numbers mean.
EVIDENCE_CONTRACT_VERSION = 1


class EvidenceGame(BaseModel):
    """Which game, and the context needed to talk about it.

    Every field is a stored column on `games` or `uploads`.
    """

    game_id: uuid.UUID
    upload_id: uuid.UUID
    game_index: int
    white_name: str
    black_name: str
    result: str
    played_date_text: str | None
    player_color: Literal["white", "black"]
    player_result: str
    eco: str | None
    opening_name: str | None
    opening_source: str | None
    opening_matched_ply: int | None


class EvidenceMove(BaseModel):
    """The move itself, as stored. `moves` columns plus ply arithmetic."""

    move_row_id: int
    ply: int
    move_number: int
    side: Literal["white", "black"]
    san: str
    uci: str
    fen_before: str
    fen_after: str


class EvidenceEngineView(BaseModel):
    """What the engine said about the two positions.

    `best_move_uci` and the search statistics are stored columns on
    `position_evaluations`. `is_exact_best_move` is a CP1 derivation, and is
    null - never false - when there was no stored best move to compare with.
    """

    before_evaluation_id: int | None
    after_evaluation_id: int | None
    best_move_uci: str | None
    is_exact_best_move: bool | None
    depth_reached: int | None
    nodes: int | None
    time_ms: int | None


class EvidenceAssessment(BaseModel):
    """What the position was worth, stored and derived, side by side.

    `stored_*` are the canonical White-perspective values exactly as written by
    the worker. `player_*` are the CP1 derivation of the same rows from the
    graded player's point of view.

    Both are carried deliberately. The stored form is the raw fact and can be
    checked against the database; the player-relative form is the only one that
    can say whether a checkmate was delivered or received, because a stored
    `mate 0` is signless and negating zero changes nothing.
    """

    stored_white_score_before: ScoreOut | None
    stored_white_score_after: ScoreOut | None
    player_score_before: PlayerScoreOut | None
    player_score_after: PlayerScoreOut | None

    # CP1 derivations. Null centipawn loss for every mate transition - mate is
    # never given a centipawn value.
    centipawn_loss: int | None
    quality: Literal["good", "inaccuracy", "mistake", "blunder", "critical", "unscored"]
    special_classification: Literal["missed_mate", "allowed_mate", "mate_sequence"] | None
    unscored_reason: str | None


class EvidenceProvenance(BaseModel):
    """Which analysis produced this, and under which rules.

    Engine identity is recorded per run because evaluations are only comparable
    within one engine build and one set of settings.

    There is deliberately no engine path here, and no other filesystem
    location. `EngineMetadataOut` already omits it for the same reason: where
    the binary lives is a property of the machine, not of the analysis, and it
    is exactly the sort of value that should never leave the server.
    """

    analysis_run_id: uuid.UUID
    analysis_job_id: uuid.UUID | None
    engine_name: str
    engine_version: str | None
    engine_binary_sha256: str | None
    requested_depth: int
    engine_options: dict[str, int | str | bool]
    run_started_at: datetime | None
    run_completed_at: datetime | None
    # Which set of thresholds and formulas produced the assessment above.
    move_quality_version: int


class EvidenceItem(BaseModel):
    """One position, complete enough to be explained on its own.

    Self-contained on purpose, including provenance: a future feature may send
    a single item, and an item that needed its bundle to be interpretable would
    make that impossible to do safely.
    """

    game: EvidenceGame
    move: EvidenceMove
    engine: EvidenceEngineView
    assessment: EvidenceAssessment
    provenance: EvidenceProvenance


class EvidenceEnvelope(BaseModel):
    """Evidence, and the slot where generated prose would sit beside it.

    `explanation` is always null in this sprint. It exists so that the
    separation is structural rather than a convention: there is nowhere inside
    `evidence` to put a sentence, so a generated one can never be stored where
    a measured value is read from.
    """

    contract_version: int = EVIDENCE_CONTRACT_VERSION
    evidence: list[EvidenceItem]
    explanation: str | None = None


__all__ = [
    "EVIDENCE_CONTRACT_VERSION",
    "EvidenceAssessment",
    "EvidenceEngineView",
    "EvidenceEnvelope",
    "EvidenceGame",
    "EvidenceItem",
    "EvidenceMove",
    "EvidenceProvenance",
]
