import type { GameDetail, GameSummary } from "../types/library";
import type { Game } from "../types/upload";

/**
 * Adapts DB-backed game shapes (which use different field names, e.g.
 * white_name vs white) into the existing Game type so the preview-era
 * GameList/ChessGameViewer/MoveList components can be reused unchanged.
 */
export function gameFromSummary(summary: GameSummary): Game {
  return {
    index: summary.game_index,
    white: summary.white_name,
    black: summary.black_name,
    white_elo: summary.white_elo,
    black_elo: summary.black_elo,
    date: null,
    event: null,
    result: summary.result,
    eco: summary.eco,
    opening: summary.opening_name,
    opening_source: summary.opening_source,
    opening_matched_ply: summary.opening_matched_ply,
    player_color: summary.user_color,
    player_result: summary.user_result,
    moves: [],
  };
}

export function gameFromDetail(detail: GameDetail): Game {
  return {
    index: detail.game_index,
    white: detail.white_name,
    black: detail.black_name,
    white_elo: detail.white_elo,
    black_elo: detail.black_elo,
    date: detail.played_date_text,
    event: detail.event,
    result: detail.result,
    eco: detail.eco,
    opening: detail.opening_name,
    opening_source: detail.opening_source,
    opening_matched_ply: detail.opening_matched_ply,
    player_color: detail.user_color,
    player_result: detail.user_result,
    moves: detail.moves,
  };
}
