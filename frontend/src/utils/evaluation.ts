import type { EvaluationScore, PositionEvaluation } from "../types/analysis";

/** Centipawn magnitude beyond which the graph stops growing. */
export const MAX_GRAPH_PAWNS = 6;
/** Fixed band mate is drawn at - visual only, never persisted. */
export const MATE_GRAPH_BAND = 7;

/**
 * Short label for a score, e.g. "+0.74", "-1.32", "M3", "-M2".
 *
 * A mate value of 0 means checkmate is on the board rather than "mate in
 * zero moves", so it gets a word instead of a number.
 */
export function formatScore(score: EvaluationScore): string {
  if (score.type === "mate") {
    if (score.value === 0) return "Checkmate";
    return score.value > 0 ? `M${score.value}` : `-M${Math.abs(score.value)}`;
  }

  const pawns = score.value / 100;
  return `${pawns >= 0 ? "+" : ""}${pawns.toFixed(2)}`;
}

/** Long form for tooltips and screen readers. */
export function describeScore(score: EvaluationScore): string {
  if (score.type === "mate") {
    if (score.value === 0) return "Checkmate";
    return score.value > 0 ? `Mate in ${score.value}` : `Mated in ${Math.abs(score.value)}`;
  }
  return formatScore(score);
}

/**
 * Y values for plotting, in pawn units.
 *
 * Uses `white_score`, the canonical engine convention: positive favours
 * White regardless of which side the saved player was. This matches every
 * other chess tool, so a number means the same thing in every game. The
 * player-relative `score` stays on the wire for later move-quality work.
 *
 * Centipawn scores are clamped so one lost queen doesn't flatten the rest of
 * the game; mate is pinned to a band beyond that range so it reads as
 * categorically different rather than just "very large".
 *
 * Real values are never modified - this is display geometry only, and the
 * underlying evaluations keep their exact numbers.
 */
export function toGraphValues(evaluations: PositionEvaluation[]): number[] {
  let previous = 0;

  return evaluations.map((evaluation) => {
    const score = evaluation.white_score;
    let value: number;

    if (score.type === "mate") {
      if (score.value === 0) {
        // Checkmate on the board carries no sign - python-chess reports Mate(0)
        // identically whoever was mated - so continue the direction the
        // position was already heading.
        value = previous >= 0 ? MATE_GRAPH_BAND : -MATE_GRAPH_BAND;
      } else {
        value = score.value > 0 ? MATE_GRAPH_BAND : -MATE_GRAPH_BAND;
      }
    } else {
      const pawns = score.value / 100;
      value = Math.max(-MAX_GRAPH_PAWNS, Math.min(MAX_GRAPH_PAWNS, pawns));
    }

    previous = value;
    return value;
  });
}

/** "12. Nf3" style label for a ply, for tooltips. */
export function plyLabel(ply: number): string {
  if (ply === 0) return "Start";
  const moveNumber = Math.ceil(ply / 2);
  return ply % 2 === 1 ? `${moveNumber}.` : `${moveNumber}...`;
}
