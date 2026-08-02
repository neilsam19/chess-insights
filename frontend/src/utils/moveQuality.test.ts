import { describe, expect, it } from "vitest";
import type { MoveQuality, PlayerScore } from "../types/moveQuality";
import {
  NO_VALUE,
  annotationsByPly,
  describePlayerScore,
  describeQuality,
  filterMoves,
  formatPlayerScore,
  moveAnnotation,
  nextMovePly,
  previousMovePly,
} from "./moveQuality";

function cp(value: number): PlayerScore {
  return { kind: "cp", cp: value, mate_distance: null };
}

function mateFor(distance: number): PlayerScore {
  return { kind: "mate_for", cp: null, mate_distance: distance };
}

function mateAgainst(distance: number): PlayerScore {
  return { kind: "mate_against", cp: null, mate_distance: distance };
}

function move(overrides: Partial<MoveQuality> = {}): MoveQuality {
  return {
    ply: 1,
    move_number: 1,
    side: "white",
    san: "e4",
    played_move_uci: "e2e4",
    best_move_uci: "e2e4",
    is_exact_best_move: true,
    score_before: cp(20),
    score_after: cp(20),
    centipawn_loss: 0,
    quality: "good",
    special_classification: null,
    unscored_reason: null,
    ...overrides,
  };
}

describe("formatPlayerScore", () => {
  it("renders centipawns as signed pawns", () => {
    expect(formatPlayerScore(cp(74))).toBe("+0.74");
    expect(formatPlayerScore(cp(-132))).toBe("-1.32");
    expect(formatPlayerScore(cp(0))).toBe("+0.00");
  });

  it("distinguishes delivering checkmate from being checkmated", () => {
    // The whole reason this formatter exists. Both arrive as a signless
    // mate 0; only `kind` says which way round it went, so if these ever
    // render the same the player is told they won a game they lost.
    expect(formatPlayerScore(mateFor(0))).toBe("Checkmate");
    expect(formatPlayerScore(mateAgainst(0))).toBe("Checkmated");
    expect(formatPlayerScore(mateFor(0))).not.toBe(formatPlayerScore(mateAgainst(0)));
  });

  it("renders mate in n from the player's side", () => {
    expect(formatPlayerScore(mateFor(3))).toBe("M3");
    expect(formatPlayerScore(mateAgainst(2))).toBe("-M2");
  });

  it("never renders mate as a number of pawns", () => {
    for (const score of [mateFor(0), mateFor(4), mateAgainst(0), mateAgainst(4)]) {
      expect(formatPlayerScore(score)).not.toMatch(/\d+\.\d\d/);
    }
  });

  it("reports a missing score as a gap rather than zero", () => {
    expect(formatPlayerScore(null)).toBe(NO_VALUE);
    expect(formatPlayerScore({ kind: "cp", cp: null, mate_distance: null })).toBe(
      NO_VALUE,
    );
  });
});

describe("describePlayerScore", () => {
  it("spells out both mate directions", () => {
    expect(describePlayerScore(mateFor(0))).toBe("checkmate delivered");
    expect(describePlayerScore(mateAgainst(0))).toBe("checkmated");
    expect(describePlayerScore(mateFor(3))).toBe("mate in 3 for you");
    expect(describePlayerScore(mateAgainst(3))).toBe("mated in 3");
  });
});

describe("moveAnnotation", () => {
  it("leaves acceptable moves unmarked", () => {
    expect(moveAnnotation(move({ quality: "good", centipawn_loss: 12 }))).toBeNull();
  });

  it("does not mark a move merely for differing from the engine", () => {
    const notBest = move({
      quality: "good",
      centipawn_loss: 20,
      is_exact_best_move: false,
      best_move_uci: "d2d4",
    });

    expect(moveAnnotation(notBest)).toBeNull();
    expect(describeQuality(notBest)).toBe("Acceptable");
  });

  it("states the loss in pawns for each centipawn grade", () => {
    expect(moveAnnotation(move({ quality: "inaccuracy", centipawn_loss: 60 }))).toEqual({
      symbol: "?!",
      label: "Inaccuracy, lost 0.60 pawns",
      tone: "inaccuracy",
    });
    expect(moveAnnotation(move({ quality: "mistake", centipawn_loss: 140 }))).toEqual({
      symbol: "?",
      label: "Mistake, lost 1.40 pawns",
      tone: "mistake",
    });
    expect(moveAnnotation(move({ quality: "blunder", centipawn_loss: 320 }))).toEqual({
      symbol: "??",
      label: "Blunder, lost 3.20 pawns",
      tone: "blunder",
    });
  });

  it("marks mate errors without a centipawn figure", () => {
    const missed = moveAnnotation(
      move({ quality: "critical", special_classification: "missed_mate", centipawn_loss: null }),
    );
    const allowed = moveAnnotation(
      move({ quality: "critical", special_classification: "allowed_mate", centipawn_loss: null }),
    );

    expect(missed?.label).toBe("Missed mate");
    expect(allowed?.label).toBe("Allowed mate");
    for (const annotation of [missed, allowed]) {
      expect(annotation?.symbol).toBe("#");
      expect(annotation?.label).not.toMatch(/pawns/);
    }
  });

  it("does not mark converting a forced mate as an error", () => {
    const converting = move({
      quality: "unscored",
      special_classification: "mate_sequence",
      centipawn_loss: null,
    });

    expect(moveAnnotation(converting)).toBeNull();
    expect(describeQuality(converting)).toBe("Forced mate sequence");
  });
});

/** One move of each label, at ascending plies, in severity order. */
function oneOfEach(): MoveQuality[] {
  return [
    move({ ply: 1, quality: "good", centipawn_loss: 10 }),
    move({ ply: 3, quality: "unscored", special_classification: "mate_sequence" }),
    move({ ply: 5, quality: "inaccuracy", centipawn_loss: 60 }),
    move({ ply: 7, quality: "mistake", centipawn_loss: 140 }),
    move({ ply: 9, quality: "blunder", centipawn_loss: 320 }),
    move({ ply: 11, quality: "critical", special_classification: "missed_mate" }),
  ];
}

function plies(moves: MoveQuality[]): number[] {
  return moves.map((entry) => entry.ply);
}

describe("filterMoves", () => {
  it("shows every player move under the widest filter", () => {
    expect(plies(filterMoves(oneOfEach(), "all"))).toEqual([1, 3, 5, 7, 9, 11]);
  });

  it("includes everything at or below each threshold", () => {
    expect(plies(filterMoves(oneOfEach(), "inaccuracy"))).toEqual([5, 7, 9, 11]);
    expect(plies(filterMoves(oneOfEach(), "mistake"))).toEqual([7, 9, 11]);
  });

  it("counts blunders and mate errors together as critical", () => {
    // Deliberately the same definition as the summary card's
    // `critical_mistakes`. A reader told "2 critical mistakes" must find two
    // when they filter for them.
    expect(plies(filterMoves(oneOfEach(), "critical"))).toEqual([9, 11]);
  });

  it("never offers an ungradeable move as something to review", () => {
    for (const filter of ["inaccuracy", "mistake", "critical"] as const) {
      expect(plies(filterMoves(oneOfEach(), filter))).not.toContain(3);
    }
  });
});

describe("issue navigation", () => {
  it("steps to the nearest move in either direction", () => {
    const issues = filterMoves(oneOfEach(), "mistake");

    expect(nextMovePly(issues, 0)).toBe(7);
    expect(nextMovePly(issues, 7)).toBe(9);
    expect(previousMovePly(issues, 11)).toBe(9);
    expect(previousMovePly(issues, 8)).toBe(7);
  });

  it("stops at the ends rather than wrapping", () => {
    const issues = filterMoves(oneOfEach(), "mistake");

    expect(nextMovePly(issues, 11)).toBeNull();
    expect(previousMovePly(issues, 7)).toBeNull();
  });

  it("has nowhere to go in an empty set", () => {
    expect(nextMovePly([], 0)).toBeNull();
    expect(previousMovePly([], 99)).toBeNull();
  });

  it("skips moves the active filter excludes", () => {
    // From the start, the next *mistake* is ply 7 - not the inaccuracy at 5.
    expect(nextMovePly(filterMoves(oneOfEach(), "mistake"), 0)).toBe(7);
    expect(nextMovePly(filterMoves(oneOfEach(), "inaccuracy"), 0)).toBe(5);
  });
});

describe("annotationsByPly", () => {
  it("keys only the moves worth marking", () => {
    const byPly = annotationsByPly([
      move({ ply: 1, quality: "good" }),
      move({ ply: 3, quality: "blunder", centipawn_loss: 250 }),
      move({ ply: 5, quality: "unscored", special_classification: "mate_sequence" }),
    ]);

    expect([...byPly.keys()]).toEqual([3]);
  });
});
