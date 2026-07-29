import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChessGameViewer } from "./ChessGameViewer";
import * as analysisApi from "../api/analysis";
import type { GameAnalysis, PositionEvaluation } from "../types/analysis";
import type { Game } from "../types/upload";

vi.mock("../api/analysis", async () => {
  const actual = await vi.importActual<typeof analysisApi>("../api/analysis");
  return { ...actual, getGameAnalysis: vi.fn(), analyzeGame: vi.fn() };
});

vi.mock("react-chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

const GAME_ID = "aaaaaaaa-0000-4000-8000-000000000001";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";

function savedGame(): Game {
  return {
    index: 0,
    id: GAME_ID,
    white: "NeilSamant",
    black: "OpponentA",
    white_elo: null,
    black_elo: null,
    date: null,
    event: null,
    result: "1-0",
    eco: "C23",
    opening: "Bishop's Opening",
    opening_source: "lichess",
    opening_matched_ply: 3,
    player_color: "white",
    player_result: "win",
    moves: [
      { ply: 1, san: "e4", uci: "e2e4", fen_before: START_FEN, fen_after: AFTER_E4 },
      { ply: 2, san: "e5", uci: "e7e5", fen_before: AFTER_E4, fen_after: AFTER_E4 },
    ],
  };
}

function previewGame(): Game {
  const game = savedGame();
  delete game.id; // preview responses carry no persistent id
  return game;
}

function evaluation(ply: number, score: PositionEvaluation["score"]): PositionEvaluation {
  return {
    ply,
    score,
    white_score: score,
    best_move_uci: "e2e4",
    depth_reached: 12,
    nodes: 1000,
    time_ms: 5,
  };
}

function completeAnalysis(
  evaluations: PositionEvaluation[] = [
    evaluation(0, { type: "cp", value: 39 }),
    evaluation(1, { type: "cp", value: -71 }),
    evaluation(2, { type: "mate", value: 3 }),
  ],
): GameAnalysis {
  return {
    status: "complete",
    game_id: GAME_ID,
    run_id: "bbbbbbbb-0000-4000-8000-000000000002",
    engine: {
      name: "Stockfish",
      version: "17",
      binary_sha256: null,
      requested_depth: 12,
      options: { Threads: 1, Hash: 64 },
    },
    started_at: "2026-07-27T00:00:00Z",
    completed_at: "2026-07-27T00:00:05Z",
    perspective: "white",
    evaluations,
  };
}

describe("analysis in the game viewer", () => {
  beforeEach(() => {
    // Call history persists across tests otherwise, which would make
    // "was never called" assertions see an earlier test's calls.
    vi.clearAllMocks();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
      status: "not_analyzed",
      game_id: GAME_ID,
    });
    vi.mocked(analysisApi.analyzeGame).mockResolvedValue(completeAnalysis());
  });

  it("shows the Analyze button for a persisted game", async () => {
    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("button", { name: "Analyze Game" })).toBeInTheDocument();
  });

  it("shows no analysis section for a preview game", async () => {
    render(<ChessGameViewer game={previewGame()} />);

    // Wait for anything async to settle before asserting absence.
    await waitFor(() => expect(screen.getByTestId("board")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: "Analyze Game" })).not.toBeInTheDocument();
    expect(screen.queryByText("Engine analysis")).not.toBeInTheDocument();
    expect(analysisApi.getGameAnalysis).not.toHaveBeenCalled();
  });

  it("disables the button and shows progress while analyzing", async () => {
    const user = userEvent.setup();
    let resolve!: (value: GameAnalysis) => void;
    vi.mocked(analysisApi.analyzeGame).mockReturnValue(
      new Promise<GameAnalysis>((r) => {
        resolve = r;
      }),
    );

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Analyze Game" }));

    expect(screen.getByRole("status")).toHaveTextContent("Analyzing game...");
    expect(screen.getByRole("button", { name: "Analyzing..." })).toBeDisabled();

    resolve(completeAnalysis());
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Analyzing..." })).not.toBeInTheDocument(),
    );
  });

  it("renders the graph and engine details when complete", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("img", { name: /Evaluation graph/ })).toBeInTheDocument();
    expect(screen.getByText(/Stockfish 17/)).toBeInTheDocument();
    expect(screen.getByText(/depth 12/)).toBeInTheDocument();
  });

  it("renders a centipawn score for the current ply", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    // Board starts at ply 0 -> +0.39.
    expect(await screen.findByText("+0.39")).toBeInTheDocument();
  });

  it("renders mate distinctly from a centipawn score", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(
      completeAnalysis([evaluation(0, { type: "mate", value: 3 })]),
    );

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByText("M3")).toBeInTheDocument();
    // Appears twice by design: the current-score label and the graph tooltip.
    expect(screen.getAllByText(/Mate in 3/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/\+100000/)).not.toBeInTheDocument();
  });

  it("keeps the score aligned with the board's current ply", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    expect(await screen.findByText("+0.39")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));

    // Ply 1 -> -0.71, and the ply-0 score is no longer displayed.
    expect(await screen.findByText("-0.71")).toBeInTheDocument();
    expect(screen.queryByText("+0.39")).not.toBeInTheDocument();
  });

  it("shows a readable error and a retry action when analysis failed", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
      status: "failed",
      game_id: GAME_ID,
      run_id: "cccccccc-0000-4000-8000-000000000003",
      started_at: "2026-07-27T00:00:00Z",
      completed_at: "2026-07-27T00:00:01Z",
      error_message: "Stockfish exited unexpectedly.",
    });

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Stockfish exited unexpectedly.",
    );
    expect(screen.getByRole("button", { name: "Retry Analysis" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Evaluation graph/ })).not.toBeInTheDocument();
  });

  it("issues a new request when retry is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
      status: "failed",
      game_id: GAME_ID,
      run_id: "cccccccc-0000-4000-8000-000000000003",
      started_at: "2026-07-27T00:00:00Z",
      completed_at: "2026-07-27T00:00:01Z",
      error_message: "Stockfish exited unexpectedly.",
    });

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Retry Analysis" }));

    await waitFor(() => expect(analysisApi.analyzeGame).toHaveBeenCalledWith(GAME_ID));
    expect(await screen.findByRole("img", { name: /Evaluation graph/ })).toBeInTheDocument();
  });

  it("displays the canonical White score, not the player-relative one", async () => {
    // A game the user played as Black: the backend supplies both a
    // player-relative score (-1.50) and the canonical White score (+1.50).
    // Standard engine convention means the graph shows White's view, so a
    // number reads the same way in every game.
    const playerRelative: PositionEvaluation = {
      ply: 0,
      score: { type: "cp", value: -150 },
      white_score: { type: "cp", value: 150 },
      best_move_uci: "e2e4",
      depth_reached: 12,
      nodes: 1000,
      time_ms: 5,
    };
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
      ...completeAnalysis([playerRelative]),
      perspective: "black",
    } as GameAnalysis);

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByText("+1.50")).toBeInTheDocument();
    expect(screen.queryByText("-1.50")).not.toBeInTheDocument();
  });

  it("does not label the graph with a per-game perspective", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
      ...completeAnalysis(),
      perspective: "black",
    } as GameAnalysis);

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByText(/favours White/)).toBeInTheDocument();
    expect(screen.queryByText(/Black perspective/)).not.toBeInTheDocument();
  });

  it("still lets the move list drive the board", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await screen.findByText("+0.39");

    await user.click(screen.getByRole("button", { name: "End" }));

    // Last ply of a 2-move game -> ply 2 -> M3.
    expect(await screen.findByText("M3")).toBeInTheDocument();
  });
});
