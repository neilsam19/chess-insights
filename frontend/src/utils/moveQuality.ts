import type {
  MoveAnnotation,
  MoveQuality,
  MoveQualityLabel,
  PlayerScore,
} from "../types/moveQuality";

/** Shown wherever a metric has no sample, so a gap never reads as a zero. */
export const NO_VALUE = "—";

/**
 * Short label for a player-relative score, e.g. "+0.74", "M3", "Checkmated".
 *
 * Mate never becomes a number here. `formatScore` can render mate 0 as
 * "Checkmate" but cannot say who was mated, because the stored value is
 * signless; `kind` carries that, so the two directions get different words.
 */
export function formatPlayerScore(score: PlayerScore | null): string {
  if (score === null) return NO_VALUE;

  if (score.kind === "mate_for") {
    return score.mate_distance === 0 ? "Checkmate" : `M${score.mate_distance}`;
  }
  if (score.kind === "mate_against") {
    return score.mate_distance === 0 ? "Checkmated" : `-M${score.mate_distance}`;
  }

  if (score.cp === null) return NO_VALUE;
  const pawns = score.cp / 100;
  return `${pawns >= 0 ? "+" : ""}${pawns.toFixed(2)}`;
}

/** Long form, for screen readers and hover text. */
export function describePlayerScore(score: PlayerScore | null): string {
  if (score === null) return "no evaluation";

  if (score.kind === "mate_for") {
    return score.mate_distance === 0
      ? "checkmate delivered"
      : `mate in ${score.mate_distance} for you`;
  }
  if (score.kind === "mate_against") {
    return score.mate_distance === 0
      ? "checkmated"
      : `mated in ${score.mate_distance}`;
  }

  return formatPlayerScore(score);
}

/** Centipawns as pawns, e.g. 140 -> "1.40". */
export function formatPawnLoss(centipawns: number): string {
  return (centipawns / 100).toFixed(2);
}

const QUALITY_TEXT: Record<MoveQualityLabel, string> = {
  good: "Acceptable",
  inaccuracy: "Inaccuracy",
  mistake: "Mistake",
  blunder: "Blunder",
  critical: "Critical mistake",
  unscored: "Not scored",
};

/**
 * Standard chess punctuation for the centipawn grades. Mate errors get "#"
 * instead of a third punctuation mark, so they read as a different kind of
 * event rather than a worse degree of the same one.
 */
const QUALITY_SYMBOL: Record<"inaccuracy" | "mistake" | "blunder", string> = {
  inaccuracy: "?!",
  mistake: "?",
  blunder: "??",
};

/** Plain-language quality, for the current-move readout. */
export function describeQuality(move: MoveQuality): string {
  if (move.special_classification === "missed_mate") return "Missed mate";
  if (move.special_classification === "allowed_mate") return "Allowed mate";
  if (move.special_classification === "mate_sequence") return "Forced mate sequence";
  return QUALITY_TEXT[move.quality];
}

/**
 * The markup for one move, or null when there is nothing to say.
 *
 * Only mistakes are marked. Annotating every acceptable move would bury the
 * few that matter, and an unmarked move already reads as unremarkable.
 *
 * `mate_sequence` is deliberately unmarked: both sides of that comparison are
 * mate in the player's favour, so the player was converting a forced win.
 */
export function moveAnnotation(move: MoveQuality): MoveAnnotation | null {
  if (move.special_classification === "missed_mate") {
    return { symbol: "#", label: "Missed mate", tone: "critical" };
  }
  if (move.special_classification === "allowed_mate") {
    return { symbol: "#", label: "Allowed mate", tone: "critical" };
  }

  if (
    move.quality !== "inaccuracy" &&
    move.quality !== "mistake" &&
    move.quality !== "blunder"
  ) {
    return null;
  }

  const text = QUALITY_TEXT[move.quality];
  return {
    symbol: QUALITY_SYMBOL[move.quality],
    label:
      move.centipawn_loss === null
        ? text
        : `${text}, lost ${formatPawnLoss(move.centipawn_loss)} pawns`,
    tone: move.quality,
  };
}

/**
 * Annotations keyed by ply, for the move list.
 *
 * Only the graded player's moves are present, because only their moves are in
 * `moves` - the opponent's are never annotated.
 */
export function annotationsByPly(moves: MoveQuality[]): Map<number, MoveAnnotation> {
  const byPly = new Map<number, MoveAnnotation>();

  for (const move of moves) {
    const annotation = moveAnnotation(move);
    if (annotation !== null) {
      byPly.set(move.ply, annotation);
    }
  }

  return byPly;
}
