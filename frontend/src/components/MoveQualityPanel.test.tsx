import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChessGameViewer } from "./ChessGameViewer";
import * as analysisApi from "../api/analysis";
import * as moveQualityApi from "../api/moveQuality";
import type { GameAnalysis } from "../types/analysis";
import type {
  CompleteMoveQuality,
  GameMoveQuality,
  MoveQuality,
  MoveQualitySummary,
} from "../types/moveQuality";
import type { Game } from "../types/upload";

vi.mock("../api/analysis", async () => {
  const actual = await vi.importActual<typeof analysisApi>("../api/analysis");
  return { ...actual, getGameAnalysis: vi.fn(), analyzeGame: vi.fn() };
});

vi.mock("../api/moveQuality", () => ({ getGameMoveQuality: vi.fn() }));

vi.mock("react-chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

const GAME_ID = "aaaaaaaa-0000-4000-8000-000000000001";
const RUN_ID = "bbbbbbbb-0000-4000-8000-000000000002";
const JOB_ID = "dddddddd-0000-4000-8000-000000000004";

const FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

/** White is the saved player, so plies 1 and 3 are the graded ones. */
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
    result: "0-1",
    eco: "C20",
    opening: "King's Pawn Game",
    opening_source: "lichess",
    opening_matched_ply: 2,
    player_color: "white",
    player_result: "loss",
    moves: [
      { ply: 1, san: "e4", uci: "e2e4", fen_before: FEN, fen_after: FEN },
      { ply: 2, san: "e5", uci: "e7e5", fen_before: FEN, fen_after: FEN },
      { ply: 3, san: "Qh5", uci: "d1h5", fen_before: FEN, fen_after: FEN },
      { ply: 4, san: "Nc6", uci: "b8c6", fen_before: FEN, fen_after: FEN },
    ],
  };
}

function previewGame(): Game {
  const game = savedGame();
  delete game.id; // preview responses carry no persistent id
  return game;
}

function moveFact(overrides: Partial<MoveQuality> & { ply: number }): MoveQuality {
  return {
    move_number: Math.ceil(overrides.ply / 2),
    side: overrides.ply % 2 === 1 ? "white" : "black",
    san: "e4",
    played_move_uci: "e2e4",
    best_move_uci: "e2e4",
    is_exact_best_move: true,
    score_before: { kind: "cp", cp: 20, mate_distance: null },
    score_after: { kind: "cp", cp: 20, mate_distance: null },
    centipawn_loss: 0,
    quality: "good",
    special_classification: null,
    unscored_reason: null,
    ...overrides,
  };
}

function summary(overrides: Partial<MoveQualitySummary> = {}): MoveQualitySummary {
  return {
    total_player_moves: 2,
    graded_moves: 2,
    numeric_cpl_moves: 2,
    unscored_moves: 0,
    coverage_percent: 100,
    average_centipawn_loss: 160,
    median_centipawn_loss: 140,
    exact_best_move_count: 1,
    exact_best_move_comparable: 2,
    exact_best_move_percent: 50,
    inaccuracies: 0,
    mistakes: 0,
    blunders: 1,
    missed_mates: 0,
    allowed_mates: 0,
    mate_sequences: 0,
    critical_mistakes: 1,
    ...overrides,
  };
}

function completeQuality(
  moves: MoveQuality[],
  summaryOverrides: Partial<MoveQualitySummary> = {},
): CompleteMoveQuality {
  return {
    status: "complete",
    game_id: GAME_ID,
    analysis_run_id: RUN_ID,
    job_id: JOB_ID,
    player_color: "white",
    engine: {
      name: "Stockfish",
      version: "17",
      binary_sha256: null,
      requested_depth: 12,
      options: { Threads: 1, Hash: 64 },
    },
    move_quality_version: 1,
    summary: summary(summaryOverrides),
    moves,
  };
}

/** The default pair: a good opening move, then a blunder. */
function defaultMoves(): MoveQuality[] {
  return [
    moveFact({ ply: 1, san: "e4", quality: "good", centipawn_loss: 0 }),
    moveFact({
      ply: 3,
      san: "Qh5",
      played_move_uci: "d1h5",
      best_move_uci: "g1f3",
      is_exact_best_move: false,
      quality: "blunder",
      centipawn_loss: 320,
      score_before: { kind: "cp", cp: 20, mate_distance: null },
      score_after: { kind: "cp", cp: -300, mate_distance: null },
    }),
  ];
}

function completeAnalysis(): GameAnalysis {
  return {
    status: "complete",
    game_id: GAME_ID,
    run_id: RUN_ID,
    job_id: JOB_ID,
    batch_id: null,
    engine: {
      name: "Stockfish",
      version: "17",
      binary_sha256: null,
      requested_depth: 12,
      options: {},
    },
    started_at: "2026-08-02T00:00:00Z",
    completed_at: "2026-08-02T00:00:05Z",
    perspective: "white",
    evaluations: [],
    can_cancel: false,
    can_retry: false,
  };
}

function running(): GameAnalysis {
  return {
    status: "running",
    game_id: GAME_ID,
    job_id: JOB_ID,
    batch_id: null,
    run_id: RUN_ID,
    started_at: "2026-08-02T00:00:00Z",
    progress: {
      completed_positions: 1,
      total_positions: 4,
      percent: 25,
      attempt: 1,
      max_attempts: 2,
    },
    cancel_requested: false,
    can_cancel: true,
    can_retry: false,
  };
}

/** Renders the viewer and waits for the move-quality section to settle. */
async function renderGraded(
  quality: GameMoveQuality = completeQuality(defaultMoves()),
) {
  vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());
  vi.mocked(moveQualityApi.getGameMoveQuality).mockResolvedValue(quality);

  render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

  // Two chained promises - analysis, then move quality - and this file is the
  // gate for every assertion below it, so it gets more than the 1s default.
  await screen.findByRole("heading", { name: "Move quality" }, { timeout: 4000 });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue({
    status: "not_analyzed",
    game_id: GAME_ID,
  });
});

describe("move-list annotations", () => {
  it("annotates the player's moves and leaves the opponent's alone", async () => {
    await renderGraded();

    // Ply 3 is the player's second move, so the list labels it "2.".
    expect(
      screen.getByRole("button", { name: "2. Qh5, Blunder, lost 3.20 pawns" }),
    ).toBeInTheDocument();

    // The opponent's replies are not graded, so they carry no markup at all.
    expect(screen.getByRole("button", { name: "e5" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nc6" })).toBeInTheDocument();
  });

  it("leaves the player's acceptable moves unmarked", async () => {
    await renderGraded();

    expect(screen.getByRole("button", { name: "1. e4" })).toBeInTheDocument();
  });

  it("renders mate errors with their own label, never a centipawn figure", async () => {
    await renderGraded(
      completeQuality([
        moveFact({
          ply: 1,
          san: "e4",
          quality: "critical",
          special_classification: "missed_mate",
          centipawn_loss: null,
          score_before: { kind: "mate_for", cp: null, mate_distance: 2 },
          score_after: { kind: "cp", cp: 400, mate_distance: null },
        }),
        moveFact({
          ply: 3,
          san: "Qh5",
          quality: "blunder",
          centipawn_loss: 320,
          is_exact_best_move: false,
        }),
      ]),
    );

    const missed = screen.getByRole("button", { name: "1. e4, Missed mate" });
    const blunder = screen.getByRole("button", { name: /^2\. Qh5, Blunder/ });

    expect(missed).toBeInTheDocument();
    expect(blunder).toBeInTheDocument();
    // The distinction that matters: mate is never priced in pawns.
    expect(missed.textContent).not.toMatch(/pawns/);
    expect(blunder.textContent).toMatch(/3\.20 pawns/);
    expect(missed.textContent).not.toBe(blunder.textContent);
  });

  it("annotates a move the player was mated by", async () => {
    await renderGraded(
      completeQuality([
        moveFact({
          ply: 1,
          san: "e4",
          quality: "critical",
          special_classification: "allowed_mate",
          centipawn_loss: null,
          score_after: { kind: "mate_against", cp: null, mate_distance: 1 },
        }),
      ]),
    );

    expect(
      screen.getByRole("button", { name: "1. e4, Allowed mate" }),
    ).toBeInTheDocument();
  });
});

describe("the summary card", () => {
  it("shows the transparent metrics with their sample sizes", async () => {
    await renderGraded();

    expect(screen.getByText("Average centipawn loss")).toBeInTheDocument();
    expect(screen.getByText("160")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText(/Averaged over 2 of your 2 moves/)).toBeInTheDocument();
  });

  it("explains what the metrics mean", async () => {
    await renderGraded();

    expect(screen.getByText(/100 centipawns is one pawn/)).toBeInTheDocument();
    expect(
      screen.getByText(/several moves are often nearly as strong/),
    ).toBeInTheDocument();
  });

  it("reports an absent average as a gap, not as flawless play", async () => {
    await renderGraded(
      completeQuality(
        [
          moveFact({
            ply: 1,
            san: "e4",
            quality: "unscored",
            special_classification: "mate_sequence",
            centipawn_loss: null,
          }),
        ],
        {
          total_player_moves: 1,
          graded_moves: 0,
          numeric_cpl_moves: 0,
          unscored_moves: 1,
          coverage_percent: 0,
          average_centipawn_loss: null,
          median_centipawn_loss: null,
          exact_best_move_percent: null,
          exact_best_move_comparable: 0,
          exact_best_move_count: 0,
          blunders: 0,
          critical_mistakes: 0,
        },
      ),
    );

    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/1 move was not scored/)).toBeInTheDocument();
    // "ACPL 0" would read as perfect play rather than as no sample.
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });
});

describe("the current move readout", () => {
  /** Scoped so move-list markup can never satisfy a readout assertion. */
  function readout() {
    return within(screen.getByRole("group", { name: "Current move" }));
  }

  it("shows exact-best-move as a badge separate from the quality", async () => {
    const user = userEvent.setup();
    await renderGraded();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("group", { name: "Current move" });

    // Two independent facts about the same move: how good it was, and whether
    // it happened to be the engine's first choice.
    expect(readout().getByText("Acceptable")).toBeInTheDocument();
    expect(readout().getByText("Matched the engine")).toBeInTheDocument();
  });

  it("still reads a non-best move with a small loss as acceptable", async () => {
    const user = userEvent.setup();
    await renderGraded(
      completeQuality([
        moveFact({
          ply: 1,
          san: "e4",
          quality: "good",
          centipawn_loss: 20,
          is_exact_best_move: false,
          best_move_uci: "d2d4",
        }),
      ]),
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("group", { name: "Current move" });

    expect(readout().getByText("Acceptable")).toBeInTheDocument();
    // Differing from the engine is not itself an error, so neither the badge
    // nor any negative verdict should appear - and the move stays unmarked in
    // the list.
    expect(readout().queryByText("Matched the engine")).not.toBeInTheDocument();
    expect(readout().queryByText(/Mistake|Inaccuracy|Blunder/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1. e4" })).toBeInTheDocument();
  });

  it("names a mate error rather than pricing it", async () => {
    const user = userEvent.setup();
    await renderGraded(
      completeQuality([
        moveFact({
          ply: 1,
          san: "e4",
          quality: "critical",
          special_classification: "missed_mate",
          centipawn_loss: null,
          score_before: { kind: "mate_for", cp: null, mate_distance: 2 },
          score_after: { kind: "cp", cp: 400, mate_distance: null },
        }),
      ]),
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("group", { name: "Current move" });

    expect(readout().getByText("Missed mate")).toBeInTheDocument();
    // The score reads as mate, not as a very large number of pawns.
    expect(readout().getByText(/M2 to \+4\.00/)).toBeInTheDocument();
    expect(readout().queryByText(/centipawns lost/)).not.toBeInTheDocument();
  });

  it("says nothing about an opponent move", async () => {
    const user = userEvent.setup();
    await renderGraded();

    await user.click(screen.getByRole("button", { name: "e5" }));

    expect(
      await screen.findByText(/Select one of your moves/),
    ).toBeInTheDocument();
  });
});

describe("states with nothing to show", () => {
  it("shows no metrics at all while analysis is still running", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(running());

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await screen.findByRole("status");

    expect(screen.queryByRole("heading", { name: "Move quality" })).not.toBeInTheDocument();
    expect(screen.queryByText("Average centipawn loss")).not.toBeInTheDocument();
    expect(screen.queryByText("Critical mistakes")).not.toBeInTheDocument();
    expect(moveQualityApi.getGameMoveQuality).not.toHaveBeenCalled();
  });

  it("shows no metrics for a game that was never analyzed", async () => {
    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await screen.findByRole("button", { name: "Analyze Game" });

    expect(screen.queryByRole("heading", { name: "Move quality" })).not.toBeInTheDocument();
    expect(moveQualityApi.getGameMoveQuality).not.toHaveBeenCalled();
  });

  it("explains a game with no identifiable player instead of grading one", async () => {
    await renderGraded({
      status: "not_gradable",
      game_id: GAME_ID,
      reason: "unknown_player_color",
      detail:
        "This game was saved under a player name that matched neither side, so there is no player whose moves can be graded.",
    });

    expect(screen.getByText(/matched neither side/)).toBeInTheDocument();
    expect(screen.queryByText("Average centipawn loss")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1. e4" })).toBeInTheDocument();
  });

  it("reports a failed fetch without claiming the moves were perfect", async () => {
    vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());
    vi.mocked(moveQualityApi.getGameMoveQuality).mockRejectedValue(new Error("down"));

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load move quality",
    );
    expect(screen.queryByText("Average centipawn loss")).not.toBeInTheDocument();
  });
});

describe("the preview path", () => {
  it("renders the move list unchanged and asks for nothing", async () => {
    render(<ChessGameViewer game={previewGame()} />);

    await waitFor(() => expect(screen.getByTestId("board")).toBeInTheDocument());

    for (const san of ["1. e4", "e5", "2. Qh5", "Nc6"]) {
      expect(screen.getByRole("button", { name: san })).toBeInTheDocument();
    }
    expect(screen.queryByRole("heading", { name: "Move quality" })).not.toBeInTheDocument();
    expect(moveQualityApi.getGameMoveQuality).not.toHaveBeenCalled();
    expect(analysisApi.getGameAnalysis).not.toHaveBeenCalled();
  });
});

describe("fetching", () => {
  it("asks for move quality once, not on every analysis poll", async () => {
    await renderGraded();

    expect(moveQualityApi.getGameMoveQuality).toHaveBeenCalledTimes(1);
    expect(moveQualityApi.getGameMoveQuality).toHaveBeenCalledWith(GAME_ID);
  });

  it("loads move quality once a running analysis finishes", async () => {
    vi.mocked(analysisApi.getGameAnalysis)
      .mockResolvedValueOnce(running())
      .mockResolvedValue(completeAnalysis());
    vi.mocked(moveQualityApi.getGameMoveQuality).mockResolvedValue(
      completeQuality(defaultMoves()),
    );

    render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
    await screen.findByRole("status");
    expect(moveQualityApi.getGameMoveQuality).not.toHaveBeenCalled();

    // The panel polls on its own timer; the summary must appear off the back
    // of that, without a second poller of its own.
    expect(
      await screen.findByRole("heading", { name: "Move quality" }, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(moveQualityApi.getGameMoveQuality).toHaveBeenCalledTimes(1);
  });
});
