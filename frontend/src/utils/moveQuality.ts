import type {
  MoveAnnotation,
  MoveQuality,
  MoveQualityLabel,
  PlayerScore,
  SpecialClassification,
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

/**
 * Plain-language quality from the two fields that determine it.
 *
 * Takes the fields rather than a whole move, because upload-level review
 * positions carry the same two facts in a different shape and must read
 * identically - "Blunder" here and "blunder" there would look like two
 * different classifications of the same move.
 */
export function qualityLabel(
  quality: MoveQualityLabel,
  special: SpecialClassification | null,
): string {
  if (special === "missed_mate") return "Missed mate";
  if (special === "allowed_mate") return "Allowed mate";
  if (special === "mate_sequence") return "Forced mate sequence";
  return QUALITY_TEXT[quality];
}

/** Plain-language quality, for the current-move readout. */
export function describeQuality(move: MoveQuality): string {
  return qualityLabel(move.quality, move.special_classification);
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

export const MOVE_FILTERS = ["all", "inaccuracy", "mistake", "critical"] as const;

export type MoveFilter = (typeof MOVE_FILTERS)[number];

export const FILTER_LABEL: Record<MoveFilter, string> = {
  all: "All my moves",
  inaccuracy: "Inaccuracies or worse",
  mistake: "Mistakes or worse",
  critical: "Critical mistakes",
};

/**
 * Ordering for "or worse". `unscored` sits with `good` at zero: a move the
 * server could not grade is not evidence of a weak one, so it is never
 * presented as something to review.
 */
const SEVERITY: Record<MoveQualityLabel, number> = {
  good: 0,
  unscored: 0,
  inaccuracy: 1,
  mistake: 2,
  blunder: 3,
  critical: 3,
};

/**
 * "Critical" is blunders *and* mate errors, deliberately matching the
 * `critical_mistakes` figure on the summary card. The two labels are disjoint
 * at severity 3 so nothing is double counted, and a reader who sees "4
 * critical mistakes" finds exactly four when they filter for them.
 */
const FILTER_FLOOR: Record<MoveFilter, number> = {
  all: 0,
  inaccuracy: 1,
  mistake: 2,
  critical: 3,
};

export function filterMoves(moves: MoveQuality[], filter: MoveFilter): MoveQuality[] {
  const floor = FILTER_FLOOR[filter];
  if (floor === 0) return moves;
  return moves.filter((move) => SEVERITY[move.quality] >= floor);
}

/**
 * The next move in `moves` after `ply`, or null at the end.
 *
 * Takes an already-filtered list, so navigation and the visible list can never
 * disagree about what counts as the next issue.
 */
export function nextMovePly(moves: MoveQuality[], ply: number): number | null {
  const later = moves.filter((move) => move.ply > ply).map((move) => move.ply);
  return later.length === 0 ? null : Math.min(...later);
}

export function previousMovePly(moves: MoveQuality[], ply: number): number | null {
  const earlier = moves.filter((move) => move.ply < ply).map((move) => move.ply);
  return earlier.length === 0 ? null : Math.max(...earlier);
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
