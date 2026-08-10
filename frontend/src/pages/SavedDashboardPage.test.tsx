import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SavedDashboardPage } from "./SavedDashboardPage";
import * as library from "../api/library";
import type { GameDetail, GameSummary } from "../types/library";

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

// This page renders the board viewer, which asks for analysis and - once that
// is complete - for move quality. Without these the tests reach for a real
// server on port 8001, which is slow when it is absent and different when it
// is present.
vi.mock("../api/analysis", async () => {
  const actual =
    await vi.importActual<typeof import("../api/analysis")>("../api/analysis");
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

// The training insights section has its own tests; here it only needs to stay
// off the network. Rejecting renders its error state, which these tests
// deliberately ignore.
vi.mock("../api/uploadMoveQuality", () => ({
  getUploadMoveQuality: vi.fn(async () => {
    throw new Error("not exercised by these tests");
  }),
}));

const UPLOAD_ID = "aaaaaaaa-0000-4000-8000-000000000000";

function summary(index: number, id: string, white: string, opening: string): GameSummary {
  return {
    id,
    game_index: index,
    white_name: white,
    black_name: "Opponent",
    white_elo: null,
    black_elo: null,
    result: "1-0",
    user_color: "white",
    user_result: "win",
    eco: "B90",
    opening_name: opening,
    opening_source: "lichess",
    opening_matched_ply: 10,
    move_count: 2,
  };
}

function detail(id: string, opening: string): GameDetail {
  return {
    id,
    game_index: 0,
    event: null,
    site: null,
    round: null,
    played_date_text: null,
    white_name: "PlayerOne",
    black_name: "Opponent",
    white_elo: null,
    black_elo: null,
    result: "1-0",
    user_color: "white",
    user_result: "win",
    eco: "B90",
    opening_name: opening,
    opening_source: "lichess",
    opening_matched_ply: 10,
    move_count: 1,
    moves: [
      {
        ply: 1,
        san: "e4",
        uci: "e2e4",
        fen_before: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        fen_after: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
      },
    ],
  };
}

const GAME_ONE = "11111111-0000-4000-8000-000000000000";
const GAME_TWO = "22222222-0000-4000-8000-000000000000";

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/uploads/${UPLOAD_ID}`]}>
      <Routes>
        <Route path="/uploads/:uploadId" element={<SavedDashboardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("SavedDashboardPage", () => {
  beforeEach(() => {
    vi.mocked(library.getUpload).mockResolvedValue({
      id: UPLOAD_ID,
      original_filename: "All2023.pgn",
      player_name: "PlayerOne",
      uploaded_at: "2026-07-26T00:00:00Z",
      game_count: 2,
      warnings: [],
    });
    vi.mocked(library.getUploadStats).mockResolvedValue({
      total_games: 2,
      wins: 2,
      draws: 0,
      losses: 0,
      unknown_results: 0,
      win_percentage: 100,
      score_percentage: 100,
      games_as_white: 2,
      games_as_black: 0,
      openings: [
        {
          name: "Sicilian Defense: Najdorf Variation",
          games: 2,
          wins: 2,
          draws: 0,
          losses: 0,
          score_percentage: 100,
        },
      ],
    });
    vi.mocked(library.getUploadGames).mockResolvedValue({
      items: [
        summary(0, GAME_ONE, "PlayerOne", "Sicilian Defense: Najdorf Variation"),
        summary(1, GAME_TWO, "PlayerTwo", "Nimzo-Indian Defense"),
      ],
      page: 1,
      page_size: 25,
      total: 2,
    });
    vi.mocked(library.getGame).mockImplementation(async (gameId: string) =>
      detail(gameId, gameId === GAME_ONE ? "Sicilian Defense: Najdorf Variation" : "Nimzo-Indian Defense"),
    );
  });

  it("fetches full moves only for the selected game", async () => {
    const user = userEvent.setup();
    renderPage();

    // The first game is auto-selected, so exactly one detail fetch happens -
    // the list endpoint supplies summaries without moves.
    await waitFor(() => expect(library.getGame).toHaveBeenCalledTimes(1));
    expect(library.getGame).toHaveBeenCalledWith(GAME_ONE);

    // Selecting another game lazily fetches just that one.
    await user.click(await screen.findByText(/PlayerTwo vs Opponent/));

    await waitFor(() => expect(library.getGame).toHaveBeenCalledTimes(2));
    expect(library.getGame).toHaveBeenLastCalledWith(GAME_TWO);

    // Still one page fetch - selecting a game must not refetch the list.
    expect(library.getUploadGames).toHaveBeenCalledTimes(1);
  });

  it("shows the same canonical opening in the summary and the selected-game view", async () => {
    renderPage();

    // The first game is auto-selected, so its opening appears twice: once in
    // the list row and once in the board viewer's header. Waited for as a
    // condition rather than with findAllByText, which returns as soon as one
    // match exists and so would pass before the viewer had rendered.
    await waitFor(() =>
      expect(
        screen.getAllByText("Sicilian Defense: Najdorf Variation · B90"),
      ).toHaveLength(2),
    );

    // The unselected game appears only in the list.
    expect(screen.getAllByText("Nimzo-Indian Defense · B90")).toHaveLength(1);
  });

  it("renders a not-found state when the upload is missing", async () => {
    vi.mocked(library.getUpload).mockRejectedValue(new library.LibraryError("gone", 404));

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be found/i);
  });
});
