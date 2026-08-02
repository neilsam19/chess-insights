/** Wire types for derived move quality. Field names mirror the API exactly. */

import type { EngineMetadata, GameAnalysis } from "./analysis";

export type PlayerScoreKind = "cp" | "mate_for" | "mate_against";

/**
 * A position assessment from the selected player's point of view.
 *
 * Deliberately not `EvaluationScore`. That shape carries a signed number, and a
 * delivered checkmate is stored as mate 0, which is signless - so
 * `{type: "mate", value: 0}` cannot say whether the player mated or was mated.
 * The server folds the direction into `kind` instead, which is why this needs
 * its own formatter rather than reusing `formatScore`.
 */
export interface PlayerScore {
  kind: PlayerScoreKind;
  /** Player-relative centipawns. Populated only when `kind` is "cp". */
  cp: number | null;
  /** Moves until mate. 0 means checkmate is already on the board. */
  mate_distance: number | null;
}

export type MoveQualityLabel =
  | "good"
  | "inaccuracy"
  | "mistake"
  | "blunder"
  /** A mate error. Disjoint from `blunder`, so nothing is counted twice. */
  | "critical"
  | "unscored";

export type SpecialClassification = "missed_mate" | "allowed_mate" | "mate_sequence";

export interface MoveQuality {
  ply: number;
  move_number: number;
  side: "white" | "black";
  san: string;
  played_move_uci: string;
  best_move_uci: string | null;
  /** Null - never false - when there was no stored best move to compare to. */
  is_exact_best_move: boolean | null;
  score_before: PlayerScore | null;
  score_after: PlayerScore | null;
  /** Null for every mate transition. Mate is never given a centipawn value. */
  centipawn_loss: number | null;
  quality: MoveQualityLabel;
  special_classification: SpecialClassification | null;
  unscored_reason: string | null;
}

/**
 * Three move counts rather than one: a move can be graded without having a
 * numeric centipawn loss, so coverage and the average draw on different
 * samples.
 */
export interface MoveQualitySummary {
  total_player_moves: number;
  graded_moves: number;
  numeric_cpl_moves: number;
  unscored_moves: number;
  coverage_percent: number;

  /** Null, never 0, when nothing could be averaged. */
  average_centipawn_loss: number | null;
  median_centipawn_loss: number | null;

  exact_best_move_count: number;
  exact_best_move_comparable: number;
  /** Integer percentage, 0-100. Null when nothing was comparable. */
  exact_best_move_percent: number | null;

  inaccuracies: number;
  mistakes: number;
  blunders: number;
  missed_mates: number;
  allowed_mates: number;
  mate_sequences: number;
  critical_mistakes: number;
}

/** The game has analysis, but no identifiable player whose moves to grade. */
export interface NotGradable {
  status: "not_gradable";
  game_id: string;
  reason: "unknown_player_color";
  detail: string;
}

export interface CompleteMoveQuality {
  status: "complete";
  game_id: string;
  analysis_run_id: string;
  job_id: string | null;
  player_color: "white" | "black";
  engine: EngineMetadata;
  /** Which set of thresholds and formulas produced these values. */
  move_quality_version: number;
  summary: MoveQualitySummary;
  moves: MoveQuality[];
}

/**
 * Every non-complete state is reused from `GameAnalysis` rather than restated,
 * because the server returns those variants verbatim from the same resolution
 * the analysis endpoint uses. Restating them here would let the two drift.
 */
export type GameMoveQuality =
  | Exclude<GameAnalysis, { status: "complete" }>
  | NotGradable
  | CompleteMoveQuality;

/** How a single move is marked up in the move list. A view type, not wire. */
export interface MoveAnnotation {
  /** Short marker. Decorative - always paired with `label`, never alone. */
  symbol: string;
  /** The full statement, read by screen readers and shown on hover. */
  label: string;
  /** Styling hook. Colour is never the only carrier of this distinction. */
  tone: "inaccuracy" | "mistake" | "blunder" | "critical";
}
