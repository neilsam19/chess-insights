/**
 * Deep links into a position, and the dashboard's training insights section.
 *
 * The case that shapes the design: the game list is paginated 25 at a time, so
 * a link to a game on page three cannot be resolved by searching the loaded
 * summaries. It has to be fetched by id.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SavedDashboardPage } from "./SavedDashboardPage";
import * as library from "../api/library";
import * as uploadMoveQualityApi from "../api/uploadMoveQuality";
import type { GameDetail, GameSummary } from "../types/library";
import type { UploadMoveQuality } from "../types/uploadMoveQuality";

vi.mock("../api/library", async () => {
  const actual = await vi.importActual<typeof library>("../api/library");
  return {
    ...actual,
    getUpload: vi.fn(),
    getUploadStats: vi.fn(),
    getUploadGames: vi.fn(),
    getGame: vi.fn(),
  };
});

vi.mock("../api/uploadMoveQuality", () => ({ getUploadMoveQuality: vi.fn() }));

vi.mock("../api/analysis", async () => {
  const actual = await vi.importActual<typeof import("../api/analysis")>("../api/analysis");
  return {
    ...actual,
    getGameAnalysis: vi.fn(async (gameId: string) => ({
      status: "not_analyzed" as const,
      game_id: gameId,
    })),
  };
});

vi.mock("../api/moveQuality", () => ({
  getGameMoveQuality: vi.fn(async (gameId: string) => ({
    status: "not_analyzed" as const,
    game_id: gameId,
  })),
}));

vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position: string } }) => (
    <div data-testid="board" data-position={options.position} />
  ),
}));

const UPLOAD_ID = "aaaaaaaa-0000-4000-8000-000000000000";
const ON_PAGE_ONE = "bbbbbbbb-0000-4000-8000-000000000001";
/** Game index 60 - three pages deep, so never in the loaded summaries. */
const DEEP_GAME = "cccccccc-0000-4000-8000-000000000002";

function summary(index: number, id: string): GameSummary {
  return {
    id,
    game_index: index,
    white_name: "PlayerOne",
    black_name: "Opponent",
    white_elo: null,
    black_elo: null,
    result: "1-0",
    user_color: "white",
    user_result: "win",
    eco: "B90",
    opening_name: "Sicilian Defense",
    opening_source: "lichess",
    opening_matched_ply: 10,
    move_count: 4,
  };
}

function detail(id: string, index: number): GameDetail {
  return {
    id,
    game_index: index,
    event: null,
    site: null,
    round: null,
    played_date_text: null,
    white_name: id === DEEP_GAME ? "DeepGameWhite" : "PlayerOne",
    black_name: "Opponent",
    white_elo: null,
    black_elo: null,
    result: "1-0",
    user_color: "white",
    user_result: "win",
    eco: "B90",
    opening_name: "Sicilian Defense",
    opening_source: "lichess",
    opening_matched_ply: 10,
    move_count: 4,
    moves: [1, 2, 3, 4].map((ply) => ({
      ply,
      san: `m${ply}`,
      uci: "e2e4",
      fen_before: ply === 1 ? "fen-start" : `fen-after-${ply - 1}`,
      fen_after: `fen-after-${ply}`,
    })),
  };
}

function insights(): UploadMoveQuality {
  const shared = {
    games: 40,
    total_player_moves: 1600,
    graded_moves: 1587,
    numeric_cpl_moves: 1580,
    unscored_moves: 13,
    coverage_percent: 99,
    average_centipawn_loss: 22,
    median_centipawn_loss: 5,
    exact_best_move_count: 800,
    exact_best_move_comparable: 1587,
    exact_best_move_percent: 53,
    inaccuracies: 90,
    mistakes: 40,
    blunders: 12,
    missed_mates: 0,
    allowed_mates: 2,
    mate_sequences: 13,
    critical_mistakes: 14,
  };

  const move = {
    game_id: DEEP_GAME,
    game_index: 60,
    ply: 3,
    move_number: 2,
    side: "white" as const,
    san: "m3",
    player_color: "white" as const,
    opening: "Sicilian Defense",
    eco: "B90",
    best_move_uci: "g1f3",
    score_before: { kind: "cp" as const, cp: 250, mate_distance: null },
    score_after: { kind: "cp" as const, cp: -305, mate_distance: null },
    centipawn_loss: 555,
    quality: "blunder" as const,
    special_classification: null,
  };

  return {
    upload_id: UPLOAD_ID,
    move_quality_version: 1,
    coverage: {
      total_games: 79,
      complete_games: 40,
      queued_games: 0,
      running_games: 0,
      failed_games: 0,
      cancelled_games: 0,
      not_analyzed_games: 39,
      not_gradable_games: 0,
      graded_player_moves: 1587,
      numeric_cpl_moves: 1580,
    },
    overall: shared,
    by_color: [{ color: "white", metrics: shared }],
    by_opening: [{ opening: "Sicilian Defense", eco: "B90", metrics: shared }],
    top_review_moves: [move],
    recommendations: [
      {
        type: "review_mistakes",
        priority: 1,
        title: "Review your highest-impact positions",
        explanation: "These are the moves after which the assessment fell furthest.",
        evidence_summary: "Your costliest move.",
        evidence_items: [move],
        coverage_note: "Based on 40 of 79 games with completed analysis.",
      },
    ],
  };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/uploads/:uploadId" element={<SavedDashboardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function boardPosition(): string | null {
  return screen.getByTestId("board").getAttribute("data-position");
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(library.getUpload).mockResolvedValue({
    id: UPLOAD_ID,
    original_filename: "All2023.pgn",
    player_name: "PlayerOne",
    uploaded_at: "2026-07-26T00:00:00Z",
    game_count: 79,
    warnings: [],
  });
  vi.mocked(library.getUploadStats).mockResolvedValue({
    total_games: 79,
    wins: 40,
    draws: 10,
    losses: 29,
    unknown_results: 0,
    win_percentage: 50.6,
    score_percentage: 57,
    games_as_white: 40,
    games_as_black: 39,
    openings: [],
  });
  // One page of 25. The deep-linked game at index 60 is deliberately absent.
  vi.mocked(library.getUploadGames).mockResolvedValue({
    items: [summary(0, ON_PAGE_ONE)],
    page: 1,
    page_size: 25,
    total: 79,
  });
  vi.mocked(library.getGame).mockImplementation(async (gameId: string) =>
    detail(gameId, gameId === DEEP_GAME ? 60 : 0),
  );
  vi.mocked(uploadMoveQualityApi.getUploadMoveQuality).mockResolvedValue(insights());
});

describe("without a deep link", () => {
  it("opens the first game at the start of the game", async () => {
    renderAt(`/uploads/${UPLOAD_ID}`);

    await waitFor(() => expect(library.getGame).toHaveBeenCalledWith(ON_PAGE_ONE));
    await waitFor(() => expect(boardPosition()).toBe("fen-start"));
  });
});

describe("a deep link", () => {
  it("opens the named game at the named ply", async () => {
    renderAt(`/uploads/${UPLOAD_ID}?game=${ON_PAGE_ONE}&ply=3`);

    await waitFor(() => expect(boardPosition()).toBe("fen-after-3"));
  });

  it("resolves a game that is not on the loaded page", async () => {
    // Fetched by id rather than looked up among the 25 loaded summaries -
    // without that, a link to game 61 of 79 would silently do nothing.
    renderAt(`/uploads/${UPLOAD_ID}?game=${DEEP_GAME}&ply=2`);

    await waitFor(() => expect(library.getGame).toHaveBeenCalledWith(DEEP_GAME));
    expect(await screen.findByText("DeepGameWhite")).toBeInTheDocument();
    await waitFor(() => expect(boardPosition()).toBe("fen-after-2"));
  });

  it("does not also load the first game", async () => {
    renderAt(`/uploads/${UPLOAD_ID}?game=${DEEP_GAME}&ply=2`);

    await waitFor(() => expect(library.getGame).toHaveBeenCalledWith(DEEP_GAME));
    expect(library.getGame).not.toHaveBeenCalledWith(ON_PAGE_ONE);
  });

  it("opens at the start when the ply is missing or unusable", async () => {
    renderAt(`/uploads/${UPLOAD_ID}?game=${ON_PAGE_ONE}&ply=not-a-number`);

    await waitFor(() => expect(boardPosition()).toBe("fen-start"));
  });

  it("clamps a ply beyond the end of the game", async () => {
    renderAt(`/uploads/${UPLOAD_ID}?game=${ON_PAGE_ONE}&ply=999`);

    // Four moves, so ply 4 is the last position rather than an empty board.
    await waitFor(() => expect(boardPosition()).toBe("fen-after-4"));
  });

  it("reports a game that cannot be loaded", async () => {
    vi.mocked(library.getGame).mockRejectedValue(new Error("gone"));

    renderAt(`/uploads/${UPLOAD_ID}?game=${DEEP_GAME}&ply=2`);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load this game");
  });
});

describe("clicking through from a recommendation", () => {
  it("opens the position and puts it in the URL", async () => {
    const user = userEvent.setup();
    renderAt(`/uploads/${UPLOAD_ID}`);

    const evidence = await screen.findByRole("list", { name: /^Evidence for/ });
    await user.click(within(evidence).getByRole("button", { name: /2\. m3/ }));

    // The URL is the source of truth, so this view survives a reload and can
    // be shared - which is the whole point of the evidence being clickable.
    await waitFor(() => expect(library.getGame).toHaveBeenCalledWith(DEEP_GAME));
    await waitFor(() => expect(boardPosition()).toBe("fen-after-3"));
  });

  it("works from the standalone review list too", async () => {
    const user = userEvent.setup();
    renderAt(`/uploads/${UPLOAD_ID}`);

    const positions = await screen.findByRole("list", { name: "Positions to review" });
    await user.click(within(positions).getByRole("button", { name: /2\. m3/ }));

    await waitFor(() => expect(boardPosition()).toBe("fen-after-3"));
  });
});

describe("the insights section on the page", () => {
  it("shows coverage against the whole library", async () => {
    renderAt(`/uploads/${UPLOAD_ID}`);

    expect(await screen.findByText("Based on 40 of 79 games")).toBeInTheDocument();
  });

  it("keeps Analyze All Games available while coverage is incomplete", async () => {
    renderAt(`/uploads/${UPLOAD_ID}`);

    await screen.findByText("Based on 40 of 79 games");
    expect(screen.getByRole("button", { name: "Analyze All Games" })).toBeEnabled();
  });

  it("reports a failed insights request without blaming the library", async () => {
    vi.mocked(uploadMoveQualityApi.getUploadMoveQuality).mockRejectedValue(
      new Error("down"),
    );

    renderAt(`/uploads/${UPLOAD_ID}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load your training insights",
    );
    expect(screen.queryByText(/Nothing has been analysed yet/)).not.toBeInTheDocument();
  });

  it("leaves the rest of the dashboard working", async () => {
    renderAt(`/uploads/${UPLOAD_ID}`);

    await screen.findByText("Based on 40 of 79 games");
    expect(screen.getByRole("heading", { name: "All2023.pgn" })).toBeInTheDocument();
    expect(screen.getByTestId("board")).toBeInTheDocument();
  });
});
