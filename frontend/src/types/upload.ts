export interface Move {
  ply: number;
  san: string;
  uci: string;
  fen_before: string;
  fen_after: string;
}

export type PlayerColor = "white" | "black" | "unknown";
export type PlayerResult = "win" | "draw" | "loss" | "unknown";

export interface Game {
  index: number;
  /**
   * Persistent database id. Absent for preview games, which exist only in
   * the response body and can never be analyzed.
   */
  id?: string;
  white: string;
  black: string;
  white_elo: number | null;
  black_elo: number | null;
  date: string | null;
  event: string | null;
  result: string;
  // Canonical classification from the backend, not the raw PGN header.
  eco: string | null;
  opening: string | null;
  opening_source: string | null;
  opening_matched_ply: number | null;
  player_color: PlayerColor;
  player_result: PlayerResult;
  moves: Move[];
}

export interface OpeningStat {
  name: string;
  games: number;
  wins: number;
  draws: number;
  losses: number;
  score_percentage: number;
}

export interface UploadStats {
  total_games: number;
  wins: number;
  draws: number;
  losses: number;
  unknown_results: number;
  win_percentage: number;
  score_percentage: number;
  games_as_white: number;
  games_as_black: number;
  openings: OpeningStat[];
}

export interface UploadPreviewResponse {
  games: Game[];
  stats: UploadStats;
  warnings: string[];
}
