/** Wire types for engine analysis. Field names mirror the API exactly. */

export type EvaluationScore =
  | { type: "cp"; value: number }
  | { type: "mate"; value: number };

export interface PositionEvaluation {
  ply: number;
  /** From the saved player's perspective - positive means the player is better. */
  score: EvaluationScore;
  /** Canonical White-perspective value, as persisted. */
  white_score: EvaluationScore;
  best_move_uci: string | null;
  depth_reached: number | null;
  nodes: number | null;
  time_ms: number | null;
}

export interface EngineMetadata {
  name: string;
  version: string | null;
  binary_sha256: string | null;
  requested_depth: number;
  options: Record<string, unknown>;
}

export type GameAnalysis =
  | { status: "not_analyzed"; game_id: string }
  | { status: "running"; game_id: string; run_id: string; started_at: string }
  | {
      status: "failed";
      game_id: string;
      run_id: string;
      started_at: string;
      completed_at: string | null;
      error_message: string | null;
    }
  | {
      status: "complete";
      game_id: string;
      run_id: string;
      engine: EngineMetadata;
      started_at: string;
      completed_at: string | null;
      perspective: "white" | "black";
      evaluations: PositionEvaluation[];
    };

export interface EngineStatus {
  available: boolean;
  engine_name: string | null;
  engine_version: string | null;
  configured_depth: number;
  detail: string | null;
}
