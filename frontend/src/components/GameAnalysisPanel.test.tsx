import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChessGameViewer } from "./ChessGameViewer";
import * as analysisApi from "../api/analysis";
import type {
  GameAnalysis,
  JobStatus,
  PositionEvaluation,
} from "../types/analysis";
import type { Game } from "../types/upload";

vi.mock("../api/analysis", async () => {
  const actual = await vi.importActual<typeof analysisApi>("../api/analysis");
  return {
    ...actual,
    getGameAnalysis: vi.fn(),
    analyzeGame: vi.fn(),
    cancelJob: vi.fn(),
    retryJob: vi.fn(),
  };
});

vi.mock("react-chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

const GAME_ID = "aaaaaaaa-0000-4000-8000-000000000001";
const JOB_ID = "dddddddd-0000-4000-8000-000000000004";

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

function progress(completed: number, total = 3, attempt = 1) {
  return {
    completed_positions: completed,
    total_positions: total,
    percent: Math.round((100 * completed) / total),
    attempt,
    max_attempts: 2,
  };
}

function queued(): GameAnalysis {
  return {
    status: "queued",
    game_id: GAME_ID,
    job_id: JOB_ID,
    batch_id: null,
    queued_at: "2026-07-27T00:00:00Z",
    progress: progress(0),
    can_cancel: true,
    can_retry: false,
  };
}

function running(completed = 1, attempt = 1): GameAnalysis {
  return {
    status: "running",
    game_id: GAME_ID,
    job_id: JOB_ID,
    batch_id: null,
    run_id: "eeeeeeee-0000-4000-8000-000000000005",
    started_at: "2026-07-27T00:00:01Z",
    progress: progress(completed, 3, attempt),
    cancel_requested: false,
    can_cancel: true,
    can_retry: false,
  };
}

/** Cancel asked for, worker not stopped yet - the real in-between state. */
function cancelling(): GameAnalysis {
  return { ...running(1), cancel_requested: true, can_cancel: false } as GameAnalysis;
}

function failed(): GameAnalysis {
  return {
    status: "failed",
    game_id: GAME_ID,
    job_id: JOB_ID,
    batch_id: null,
    run_id: "cccccccc-0000-4000-8000-000000000003",
    started_at: "2026-07-27T00:00:00Z",
    completed_at: "2026-07-27T00:00:01Z",
    error_code: "engine_crashed",
    error_message: "Stockfish exited unexpectedly.",
    can_cancel: false,
    can_retry: true,
  };
}

function cancelled(): GameAnalysis {
  return {
    status: "cancelled",
    game_id: GAME_ID,
    job_id: JOB_ID,
    batch_id: null,
    run_id: null,
    cancelled_at: "2026-07-27T00:00:02Z",
    can_cancel: false,
    can_retry: true,
  };
}

function jobStatus(status: JobStatus["status"]): JobStatus {
  return {
    job_id: JOB_ID,
    game_id: GAME_ID,
    batch_id: null,
    status,
    progress: progress(0),
    queued_at: "2026-07-27T00:00:00Z",
    started_at: null,
    completed_at: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    can_cancel: status === "queued" || status === "running",
    can_retry: status === "failed" || status === "cancelled",
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
    job_id: JOB_ID,
    batch_id: null,
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
    can_cancel: false,
    can_retry: false,
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
    vi.mocked(analysisApi.analyzeGame).mockResolvedValue(queued());
    vi.mocked(analysisApi.cancelJob).mockResolvedValue(jobStatus("cancelled"));
    vi.mocked(analysisApi.retryJob).mockResolvedValue(jobStatus("queued"));
  });

  it("shows the Analyze button for a persisted game", async () => {
    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("button", { name: "Analyze Game" })).toBeInTheDocument();
  });

  it("shows no analysis section for a preview game", async () => {
    render(<ChessGameViewer game={previewGame()} />);

    await waitFor(() => expect(screen.getByTestId("board")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: "Analyze Game" })).not.toBeInTheDocument();
    expect(screen.queryByText("Engine analysis")).not.toBeInTheDocument();
    expect(analysisApi.getGameAnalysis).not.toHaveBeenCalled();
  });

  it("returns immediately to a queued state instead of waiting for the engine", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce({ status: "not_analyzed", game_id: GAME_ID })
      .mockResolvedValue(queued());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Analyze Game" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Queued for analysis");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("shows position progress while running", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(running(2));

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Analyzing position 2 of 3",
    );
    expect(screen.getByRole("progressbar", { name: "Analysis progress" })).toHaveValue(2);
  });

  it("shows which attempt is running after a retry", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(running(1, 2));

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByText(/Attempt 2 of 2/)).toBeInTheDocument();
  });

  it("keeps the board usable while analysis runs", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(running(1));

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await screen.findByRole("status");

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByTestId("board")).toBeInTheDocument();
  });

  it("cancels a running job through the job endpoint", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(running(1))
      .mockResolvedValue(cancelled());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(analysisApi.cancelJob).toHaveBeenCalledWith(JOB_ID));
    expect(await screen.findByText(/Analysis cancelled/)).toBeInTheDocument();
  });

  it("acknowledges a cancellation the worker has not acted on yet", async () => {
    // A running job stops only at a position boundary, so there is a real gap
    // between asking and stopping. Showing unchanged progress through that gap
    // makes the button look broken.
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(cancelling());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("status")).toHaveTextContent("Cancelling");
    expect(screen.getByRole("button", { name: "Cancelling..." })).toBeDisabled();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("does not send a second cancel while one is pending", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(cancelling());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Cancelling..." }));

    expect(analysisApi.cancelJob).not.toHaveBeenCalled();
  });

  it("offers retry on a cancelled analysis", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(cancelled())
      .mockResolvedValue(queued());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Analyze Game" }));

    await waitFor(() => expect(analysisApi.retryJob).toHaveBeenCalledWith(JOB_ID));
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

    expect(await screen.findByText("+0.39")).toBeInTheDocument();
  });

  it("renders mate distinctly from a centipawn score", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(
      completeAnalysis([evaluation(0, { type: "mate", value: 3 })]),
    );

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByText("M3")).toBeInTheDocument();
    expect(screen.getAllByText(/Mate in 3/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/\+100000/)).not.toBeInTheDocument();
  });

  it("keeps the score aligned with the board's current ply", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    expect(await screen.findByText("+0.39")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("-0.71")).toBeInTheDocument();
    expect(screen.queryByText("+0.39")).not.toBeInTheDocument();
  });

  it("shows a readable error and a retry action when analysis failed", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(failed());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Stockfish exited unexpectedly.",
    );
    expect(screen.getByRole("button", { name: "Retry Analysis" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Evaluation graph/ })).not.toBeInTheDocument();
  });

  it("retries a failed job rather than queueing a duplicate", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(failed())
      .mockResolvedValue(queued());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await user.click(await screen.findByRole("button", { name: "Retry Analysis" }));

    await waitFor(() => expect(analysisApi.retryJob).toHaveBeenCalledWith(JOB_ID));
    expect(analysisApi.analyzeGame).not.toHaveBeenCalled();
  });

  it("displays the canonical White score, not the player-relative one", async () => {
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

    expect(await screen.findByText("M3")).toBeInTheDocument();
  });
});

describe("polling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function tick(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it("polls while active and stops once the analysis completes", async () => {
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(queued())
      .mockResolvedValueOnce(running(1))
      .mockResolvedValueOnce(running(2))
      .mockResolvedValue(completeAnalysis());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await tick(0);

    await tick(1600);
    await tick(1600);
    await tick(1600);

    expect(screen.getByRole("img", { name: /Evaluation graph/ })).toBeInTheDocument();

    // Terminal: no further requests once there is nothing left to watch.
    const callsWhenDone = vi.mocked(analysisApi.getGameAnalysis).mock.calls.length;
    await tick(10000);
    expect(vi.mocked(analysisApi.getGameAnalysis).mock.calls.length).toBe(callsWhenDone);
  });

  it("never polls a game that has not been analyzed", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
      status: "not_analyzed",
      game_id: GAME_ID,
    });

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await tick(0);
    const initial = vi.mocked(analysisApi.getGameAnalysis).mock.calls.length;

    await tick(10000);

    expect(vi.mocked(analysisApi.getGameAnalysis).mock.calls.length).toBe(initial);
  });

  it("stops polling when the component unmounts", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(running(1));

    const { unmount } = render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await tick(0);
    await tick(1600);
    const beforeUnmount = vi.mocked(analysisApi.getGameAnalysis).mock.calls.length;

    unmount();
    await tick(10000);

    expect(vi.mocked(analysisApi.getGameAnalysis).mock.calls.length).toBe(beforeUnmount);
  });

  it("treats a failed poll as a connection problem, not a failed analysis", async () => {
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(running(1))
      .mockRejectedValue(new Error("network down"));

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await tick(0);
    await tick(1600);

    expect(screen.getByRole("status")).toHaveTextContent("Analyzing position");
    expect(screen.getByText(/Lost contact with the server/)).toBeInTheDocument();
    // Crucially not reported as the analysis itself failing.
    expect(screen.queryByRole("button", { name: "Retry Analysis" })).not.toBeInTheDocument();
  });

  it("backs off after repeated poll failures", async () => {
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(running(1))
      .mockRejectedValue(new Error("network down"));

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await tick(0);

    await tick(1600); // first failure -> next poll at ~3s
    const afterFirstFailure = vi.mocked(analysisApi.getGameAnalysis).mock.calls.length;

    // Half of the new interval must not be enough to trigger another attempt.
    await tick(1600);
    expect(vi.mocked(analysisApi.getGameAnalysis).mock.calls.length).toBe(
      afterFirstFailure,
    );

    await tick(1600);
    expect(vi.mocked(analysisApi.getGameAnalysis).mock.calls.length).toBeGreaterThan(
      afterFirstFailure,
    );
  });
});
