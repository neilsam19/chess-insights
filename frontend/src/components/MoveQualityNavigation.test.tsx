import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChessGameViewer } from "./ChessGameViewer";
import * as analysisApi from "../api/analysis";
import * as moveQualityApi from "../api/moveQuality";
import type { GameAnalysis, PositionEvaluation } from "../types/analysis";
import type { CompleteMoveQuality, MoveQuality } from "../types/moveQuality";
import type { Game, Move } from "../types/upload";

vi.mock("../api/analysis", async () => {
  const actual = await vi.importActual<typeof analysisApi>("../api/analysis");
  return { ...actual, getGameAnalysis: vi.fn() };
});

vi.mock("../api/moveQuality", () => ({ getGameMoveQuality: vi.fn() }));

// Exposes the position so a test can prove the board actually moved, rather
// than only that some state changed.
vi.mock("react-chessboard", () => ({
  Chessboard: ({ options }: { options: { position: string } }) => (
    <div data-testid="board" data-position={options.position} />
  ),
}));

const GAME_ID = "aaaaaaaa-0000-4000-8000-000000000001";
const RUN_ID = "bbbbbbbb-0000-4000-8000-000000000002";

const SAN = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "Ng5", "d5", "Nxf7", "Kxf7", "Qh5", "g6"];

/** Twelve plies, so the player (White) has six moves: plies 1, 3, 5, 7, 9, 11. */
function savedGame(): Game {
  const moves: Move[] = SAN.map((san, index) => ({
    ply: index + 1,
    san,
    uci: "e2e4",
    fen_before: index === 0 ? "fen-start" : `fen-after-${index}`,
    fen_after: `fen-after-${index + 1}`,
  }));

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
    eco: "C50",
    opening: "Italian Game",
    opening_source: "lichess",
    opening_matched_ply: 6,
    player_color: "white",
    player_result: "loss",
    moves,
  };
}

function moveFact(overrides: Partial<MoveQuality> & { ply: number }): MoveQuality {
  return {
    move_number: Math.ceil(overrides.ply / 2),
    side: overrides.ply % 2 === 1 ? "white" : "black",
    san: SAN[overrides.ply - 1],
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

/** One move of each grade, ascending in severity along the game. */
function gradedMoves(): MoveQuality[] {
  return [
    moveFact({ ply: 1, quality: "good", centipawn_loss: 5 }),
    moveFact({ ply: 3, quality: "good", centipawn_loss: 20, is_exact_best_move: false }),
    moveFact({ ply: 5, quality: "inaccuracy", centipawn_loss: 60 }),
    moveFact({ ply: 7, quality: "mistake", centipawn_loss: 140 }),
    moveFact({ ply: 9, quality: "blunder", centipawn_loss: 320 }),
    moveFact({
      ply: 11,
      quality: "critical",
      special_classification: "missed_mate",
      centipawn_loss: null,
      score_before: { kind: "mate_for", cp: null, mate_distance: 2 },
      score_after: { kind: "cp", cp: 150, mate_distance: null },
    }),
  ];
}

function completeQuality(): CompleteMoveQuality {
  return {
    status: "complete",
    game_id: GAME_ID,
    analysis_run_id: RUN_ID,
    job_id: null,
    player_color: "white",
    engine: {
      name: "Stockfish",
      version: "17",
      binary_sha256: null,
      requested_depth: 12,
      options: {},
    },
    move_quality_version: 1,
    summary: {
      total_player_moves: 6,
      graded_moves: 6,
      numeric_cpl_moves: 5,
      unscored_moves: 0,
      coverage_percent: 100,
      average_centipawn_loss: 109,
      median_centipawn_loss: 60,
      exact_best_move_count: 5,
      exact_best_move_comparable: 6,
      exact_best_move_percent: 83,
      inaccuracies: 1,
      mistakes: 1,
      blunders: 1,
      missed_mates: 1,
      allowed_mates: 0,
      mate_sequences: 0,
      critical_mistakes: 2,
    },
    moves: gradedMoves(),
  };
}

function evaluation(ply: number): PositionEvaluation {
  return {
    ply,
    score: { type: "cp", value: 20 },
    white_score: { type: "cp", value: 20 },
    best_move_uci: "e2e4",
    depth_reached: 12,
    nodes: 1000,
    time_ms: 5,
  };
}

function completeAnalysis(): GameAnalysis {
  return {
    status: "complete",
    game_id: GAME_ID,
    run_id: RUN_ID,
    job_id: null,
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
    evaluations: Array.from({ length: 13 }, (_, ply) => evaluation(ply)),
    can_cancel: false,
    can_retry: false,
  };
}

async function renderViewer() {
  const user = userEvent.setup();

  render(<ChessGameViewer game={savedGame()} gameId={GAME_ID} />);
  await screen.findByRole("heading", { name: "Move quality" }, { timeout: 4000 });

  return user;
}

/** The filtered issue list, which is what the step buttons navigate. */
function issueList() {
  return within(screen.getByRole("list", { name: "Moves to review" }));
}

function issueNames(): string[] {
  return issueList()
    .getAllByRole("button")
    .map((button) => button.textContent ?? "");
}

function boardPosition(): string | null {
  return screen.getByTestId("board").getAttribute("data-position");
}

async function chooseFilter(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("radio", { name }));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(analysisApi.getGameAnalysis).mockResolvedValue(completeAnalysis());
  vi.mocked(moveQualityApi.getGameMoveQuality).mockResolvedValue(completeQuality());
});

describe("filters", () => {
  it("opens on the moves most worth reviewing", async () => {
    await renderViewer();

    expect(screen.getByRole("radio", { name: "Critical mistakes" })).toBeChecked();
    expect(issueNames()).toEqual(["5. Nxf7, Blunder", "6. Qh5, Missed mate"]);
  });

  it("shows exactly the set each filter names", async () => {
    const user = await renderViewer();

    await chooseFilter(user, "All my moves");
    expect(issueNames()).toEqual([
      "1. e4, Acceptable",
      "2. Nf3, Acceptable",
      "3. Bc4, Inaccuracy",
      "4. Ng5, Mistake",
      "5. Nxf7, Blunder",
      "6. Qh5, Missed mate",
    ]);

    await chooseFilter(user, "Inaccuracies or worse");
    expect(issueNames()).toEqual([
      "3. Bc4, Inaccuracy",
      "4. Ng5, Mistake",
      "5. Nxf7, Blunder",
      "6. Qh5, Missed mate",
    ]);

    await chooseFilter(user, "Mistakes or worse");
    expect(issueNames()).toEqual([
      "4. Ng5, Mistake",
      "5. Nxf7, Blunder",
      "6. Qh5, Missed mate",
    ]);
  });

  it("agrees with the count on the summary card", async () => {
    // "2 critical mistakes" on the card and two rows in the list are the same
    // claim. If the filter and the aggregate ever disagree, one of them lies.
    await renderViewer();

    expect(issueNames()).toHaveLength(completeQuality().summary.critical_mistakes);
    // The card's own figure, rendered from the server's count rather than
    // from the list this test just measured.
    expect(screen.getByText(String(completeQuality().summary.critical_mistakes)))
      .toBeInTheDocument();
  });

  it("never lists an opponent move", async () => {
    const user = await renderViewer();

    await chooseFilter(user, "All my moves");

    for (const name of issueNames()) {
      expect(name).toMatch(/^\d+\./); // "1." is White; "1..." would be Black
      expect(name).not.toMatch(/\.\.\./);
    }
  });
});

describe("jumping to a move", () => {
  it("moves the board when an annotated move is clicked", async () => {
    const user = await renderViewer();
    expect(boardPosition()).toBe("fen-start");

    // The move-list entry, whose name carries the pawn figure the issue list
    // leaves off - so this is unambiguously the annotated move being clicked.
    await user.click(
      screen.getByRole("button", { name: "5. Nxf7, Blunder, lost 3.20 pawns" }),
    );

    expect(boardPosition()).toBe("fen-after-9");
  });

  it("moves the board when an issue in the list is clicked", async () => {
    const user = await renderViewer();

    await user.click(issueList().getByRole("button", { name: "6. Qh5, Missed mate" }));

    expect(boardPosition()).toBe("fen-after-11");
    expect(
      within(screen.getByRole("group", { name: "Current move" })).getByText("Missed mate"),
    ).toBeInTheDocument();
  });
});

describe("stepping through issues", () => {
  it("follows the active filter", async () => {
    const user = await renderViewer();

    // Critical filter: the mistake at ply 7 is skipped entirely.
    await user.click(screen.getByRole("button", { name: "Next issue" }));
    expect(boardPosition()).toBe("fen-after-9");

    await user.click(screen.getByRole("button", { name: "Next issue" }));
    expect(boardPosition()).toBe("fen-after-11");
  });

  it("visits the moves a wider filter adds", async () => {
    const user = await renderViewer();

    await chooseFilter(user, "Mistakes or worse");
    await user.click(screen.getByRole("button", { name: "Next issue" }));

    expect(boardPosition()).toBe("fen-after-7");
  });

  it("stops at both ends instead of wrapping", async () => {
    const user = await renderViewer();

    expect(screen.getByRole("button", { name: "Previous issue" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next issue" }));
    await user.click(screen.getByRole("button", { name: "Next issue" }));

    expect(boardPosition()).toBe("fen-after-11");
    expect(screen.getByRole("button", { name: "Next issue" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous issue" })).toBeEnabled();
  });

  it("steps backwards to the previous issue", async () => {
    const user = await renderViewer();

    await user.click(issueList().getByRole("button", { name: "6. Qh5, Missed mate" }));
    await user.click(screen.getByRole("button", { name: "Previous issue" }));

    expect(boardPosition()).toBe("fen-after-9");
  });
});

describe("keyboard access", () => {
  it("selects a filter and steps with the keyboard alone", async () => {
    const user = await renderViewer();

    const wider = screen.getByRole("radio", { name: "Mistakes or worse" });
    wider.focus();
    expect(wider).toHaveFocus();
    await user.keyboard(" ");
    expect(wider).toBeChecked();

    const next = screen.getByRole("button", { name: "Next issue" });
    next.focus();
    await user.keyboard("{Enter}");

    expect(boardPosition()).toBe("fen-after-7");
  });

  it("puts every issue on a real button, reachable by tab", async () => {
    const user = await renderViewer();

    const issue = issueList().getByRole("button", { name: "6. Qh5, Missed mate" });
    issue.focus();
    await user.keyboard("{Enter}");

    expect(boardPosition()).toBe("fen-after-11");
  });
});

describe("what CP4 and earlier already did", () => {
  it("leaves the board controls working", async () => {
    const user = await renderViewer();

    await user.click(screen.getByRole("button", { name: "End" }));
    expect(boardPosition()).toBe("fen-after-12");

    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(boardPosition()).toBe("fen-after-11");

    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(boardPosition()).toBe("fen-start");
  });

  it("keeps the evaluation graph rendered through a filter change", async () => {
    const user = await renderViewer();

    expect(screen.getByRole("img", { name: /Evaluation graph/ })).toBeInTheDocument();

    await chooseFilter(user, "All my moves");

    expect(screen.getByRole("img", { name: /Evaluation graph/ })).toBeInTheDocument();
    expect(screen.getByText(/Stockfish 17/)).toBeInTheDocument();
  });

  it("still marks mistakes in the move list", async () => {
    await renderViewer();

    expect(
      screen.getByRole("button", { name: "5. Nxf7, Blunder, lost 3.20 pawns" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "e5" })).toBeInTheDocument();
  });
});
