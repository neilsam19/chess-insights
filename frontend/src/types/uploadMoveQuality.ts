/** Wire types for upload-level move quality. Field names mirror the API. */

import type {
  MoveQualitySummary,
  PlayerScore,
  SpecialClassification,
} from "./moveQuality";

/** Per-game summary metrics plus how many games they were computed over. */
export interface MoveQualityMetrics extends MoveQualitySummary {
  games: number;
}

/**
 * Which games the metrics are - and are not - based on.
 *
 * Every bucket is mutually exclusive and they sum to `total_games`, so the UI
 * can always show the difference between the library and the sample. 40
 * analysed games must never render as 79.
 */
export interface UploadCoverage {
  total_games: number;
  complete_games: number;
  queued_games: number;
  running_games: number;
  failed_games: number;
  cancelled_games: number;
  not_analyzed_games: number;
  not_gradable_games: number;
  graded_player_moves: number;
  numeric_cpl_moves: number;
}

export interface ColorMoveQuality {
  color: "white" | "black";
  metrics: MoveQualityMetrics;
}

export interface OpeningMoveQuality {
  /** Null for games with no classification - a bucket, not a name. */
  opening: string | null;
  eco: string | null;
  metrics: MoveQualityMetrics;
}

/** One position worth revisiting, with everything needed to link to it. */
export interface ReviewMove {
  game_id: string;
  game_index: number;
  ply: number;
  move_number: number;
  side: "white" | "black";
  san: string;
  player_color: "white" | "black";
  opening: string | null;
  eco: string | null;
  best_move_uci: string | null;
  score_before: PlayerScore | null;
  score_after: PlayerScore | null;
  centipawn_loss: number | null;
  quality: "mistake" | "blunder" | "critical";
  special_classification: Exclude<SpecialClassification, "mate_sequence"> | null;
}

export type RecommendationType =
  | "review_mistakes"
  | "conversion_practice"
  | "opening_priority"
  | "color_priority";

export interface TrainingRecommendation {
  type: RecommendationType;
  /** 1 is the most important; always 1..n with no gaps. */
  priority: number;
  title: string;
  explanation: string;
  evidence_summary: string;
  evidence_items: ReviewMove[];
  coverage_note: string;
}

export interface UploadMoveQuality {
  upload_id: string;
  move_quality_version: number;
  coverage: UploadCoverage;
  overall: MoveQualityMetrics;
  by_color: ColorMoveQuality[];
  by_opening: OpeningMoveQuality[];
  top_review_moves: ReviewMove[];
  /** Empty when the evidence supports nothing. Never filled with filler. */
  recommendations: TrainingRecommendation[];
}

/** Games that are neither analysed nor being analysed right now. */
export function gamesWithoutAnalysis(coverage: UploadCoverage): number {
  return (
    coverage.not_analyzed_games + coverage.failed_games + coverage.cancelled_games
  );
}
