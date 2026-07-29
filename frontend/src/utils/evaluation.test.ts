import { describe, expect, it } from "vitest";
import {
  MATE_GRAPH_BAND,
  MAX_GRAPH_PAWNS,
  describeScore,
  formatScore,
  plyLabel,
  toGraphValues,
} from "./evaluation";
import type { PositionEvaluation } from "../types/analysis";

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

describe("formatScore", () => {
  it("renders centipawns as pawn units with an explicit sign", () => {
    expect(formatScore({ type: "cp", value: 74 })).toBe("+0.74");
    expect(formatScore({ type: "cp", value: -132 })).toBe("-1.32");
    expect(formatScore({ type: "cp", value: 0 })).toBe("+0.00");
  });

  it("renders mate differently from centipawns", () => {
    expect(formatScore({ type: "mate", value: 3 })).toBe("M3");
    expect(formatScore({ type: "mate", value: -2 })).toBe("-M2");
  });

  it("renders mate in zero as checkmate rather than M0", () => {
    // Real Stockfish output for a mated position - "mate in 0" is meaningless.
    expect(formatScore({ type: "mate", value: 0 })).toBe("Checkmate");
  });

  it("never produces a huge fake centipawn number for mate", () => {
    const label = formatScore({ type: "mate", value: 3 });
    expect(label).not.toContain("100000");
    expect(label).not.toContain("999");
  });
});

describe("describeScore", () => {
  it("spells out mate for tooltips", () => {
    expect(describeScore({ type: "mate", value: 3 })).toBe("Mate in 3");
    expect(describeScore({ type: "mate", value: -2 })).toBe("Mated in 2");
    expect(describeScore({ type: "mate", value: 0 })).toBe("Checkmate");
    expect(describeScore({ type: "cp", value: 74 })).toBe("+0.74");
  });
});

describe("toGraphValues", () => {
  it("converts centipawns to pawn units", () => {
    const values = toGraphValues([
      evaluation(0, { type: "cp", value: 74 }),
      evaluation(1, { type: "cp", value: -132 }),
    ]);

    expect(values).toEqual([0.74, -1.32]);
  });

  it("clamps extreme centipawn values so the rest of the game stays readable", () => {
    const values = toGraphValues([
      evaluation(0, { type: "cp", value: 90000 }),
      evaluation(1, { type: "cp", value: -90000 }),
    ]);

    expect(values).toEqual([MAX_GRAPH_PAWNS, -MAX_GRAPH_PAWNS]);
  });

  it("pins mate to a band beyond the centipawn range", () => {
    const values = toGraphValues([
      evaluation(0, { type: "mate", value: 3 }),
      evaluation(1, { type: "mate", value: -2 }),
    ]);

    expect(values).toEqual([MATE_GRAPH_BAND, -MATE_GRAPH_BAND]);
    expect(MATE_GRAPH_BAND).toBeGreaterThan(MAX_GRAPH_PAWNS);
  });

  it("gives checkmate the direction the game was already heading", () => {
    // White mating: ... M1 then Checkmate -> both at the top band.
    const whiteMates = toGraphValues([
      evaluation(0, { type: "cp", value: 31 }),
      evaluation(1, { type: "mate", value: 1 }),
      evaluation(2, { type: "mate", value: 0 }),
    ]);
    expect(whiteMates[2]).toBe(MATE_GRAPH_BAND);

    // Player being mated: ... -M1 then Checkmate -> both at the bottom band.
    const playerMated = toGraphValues([
      evaluation(0, { type: "cp", value: -71 }),
      evaluation(1, { type: "mate", value: -1 }),
      evaluation(2, { type: "mate", value: 0 }),
    ]);
    expect(playerMated[2]).toBe(-MATE_GRAPH_BAND);
  });

  it("does not modify the underlying evaluations", () => {
    const evaluations = [evaluation(0, { type: "cp", value: 90000 })];
    const snapshot = JSON.parse(JSON.stringify(evaluations));

    toGraphValues(evaluations);

    expect(evaluations).toEqual(snapshot);
  });
});

describe("plyLabel", () => {
  it("labels the starting position and both colours", () => {
    expect(plyLabel(0)).toBe("Start");
    expect(plyLabel(1)).toBe("1.");
    expect(plyLabel(2)).toBe("1...");
    expect(plyLabel(7)).toBe("4.");
  });
});
