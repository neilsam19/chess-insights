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

/** How far through a game an attempt has got. `percent` is server-computed. */
export interface JobProgress {
  completed_positions: number;
  total_positions: number;
  percent: number;
  attempt: number;
  max_attempts: number;
}

/**
 * Analysis is durable work, so a game can be in one of six states. Every
 * variant carries `can_cancel` / `can_retry` rather than the UI re-deriving
 * which actions are legal - the server owns that rule.
 */
export type GameAnalysis =
  | { status: "not_analyzed"; game_id: string }
  | {
      status: "queued";
      game_id: string;
      job_id: string;
      batch_id: string | null;
      queued_at: string;
      progress: JobProgress;
      can_cancel: boolean;
      can_retry: boolean;
    }
  | {
      status: "running";
      game_id: string;
      /** Null only for a legacy Sprint 4 run that predates jobs. */
      job_id: string | null;
      batch_id: string | null;
      run_id: string | null;
      started_at: string;
      progress: JobProgress | null;
      /** Cancel was asked for; the worker stops at its next position boundary. */
      cancel_requested: boolean;
      can_cancel: boolean;
      can_retry: boolean;
    }
  | {
      status: "failed";
      game_id: string;
      job_id: string | null;
      batch_id: string | null;
      run_id: string | null;
      started_at: string | null;
      completed_at: string | null;
      error_code: string | null;
      error_message: string | null;
      can_cancel: boolean;
      can_retry: boolean;
    }
  | {
      status: "cancelled";
      game_id: string;
      job_id: string;
      batch_id: string | null;
      run_id: string | null;
      cancelled_at: string | null;
      can_cancel: boolean;
      can_retry: boolean;
    }
  | {
      status: "complete";
      game_id: string;
      run_id: string;
      job_id: string | null;
      batch_id: string | null;
      engine: EngineMetadata;
      started_at: string;
      completed_at: string | null;
      perspective: "white" | "black";
      evaluations: PositionEvaluation[];
      can_cancel: boolean;
      can_retry: boolean;
    };

export interface JobStatus {
  job_id: string;
  game_id: string;
  batch_id: string | null;
  status: "queued" | "running" | "complete" | "failed" | "cancelled";
  progress: JobProgress;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  can_cancel: boolean;
  can_retry: boolean;
}

/** States where a worker may still change the outcome, so polling continues. */
export const ACTIVE_STATUSES = ["queued", "running"] as const;

export function isActive(analysis: GameAnalysis | null): boolean {
  return (
    analysis !== null &&
    (ACTIVE_STATUSES as readonly string[]).includes(analysis.status)
  );
}

export interface EngineStatus {
  available: boolean;
  engine_name: string | null;
  engine_version: string | null;
  configured_depth: number;
  detail: string | null;
}
