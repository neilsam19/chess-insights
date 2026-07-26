import type { Move } from "./upload";

export type PlayerColor = "white" | "black" | "unknown";
export type PlayerResult = "win" | "draw" | "loss" | "unknown";

export interface SavedUpload {
  id: string;
  original_filename: string;
  player_name: string;
  uploaded_at: string;
  game_count: number;
  warnings: string[];
}

export interface UploadListItem {
  id: string;
  original_filename: string;
  player_name: string;
  uploaded_at: string;
  game_count: number;
}

export interface PaginatedUploads {
  items: UploadListItem[];
  page: number;
  page_size: number;
  total: number;
}

export interface GameSummary {
  id: string;
  game_index: number;
  white_name: string;
  black_name: string;
  white_elo: number | null;
  black_elo: number | null;
  result: string;
  user_color: PlayerColor;
  user_result: PlayerResult;
  eco: string | null;
  opening_name: string | null;
  opening_source: string | null;
  opening_matched_ply: number | null;
  move_count: number;
}

export interface PaginatedGameSummaries {
  items: GameSummary[];
  page: number;
  page_size: number;
  total: number;
}

export interface GameDetail {
  id: string;
  game_index: number;
  event: string | null;
  site: string | null;
  round: string | null;
  played_date_text: string | null;
  white_name: string;
  black_name: string;
  white_elo: number | null;
  black_elo: number | null;
  result: string;
  user_color: PlayerColor;
  user_result: PlayerResult;
  eco: string | null;
  opening_name: string | null;
  opening_source: string | null;
  opening_matched_ply: number | null;
  move_count: number;
  moves: Move[];
}
