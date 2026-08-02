import { useState } from "react";
import type {
  CompleteMoveQuality,
  GameMoveQuality,
  MoveQuality,
  NotGradable,
} from "../types/moveQuality";
import type { MoveFilter } from "../utils/moveQuality";
import {
  FILTER_LABEL,
  MOVE_FILTERS,
  NO_VALUE,
  describeQuality,
  filterMoves,
  formatPlayerScore,
  moveAnnotation,
  nextMovePly,
  previousMovePly,
} from "../utils/moveQuality";
import { StatCard } from "./StatCard";

interface MoveQualityPanelProps {
  /**
   * Null until analysis is complete. Rendering nothing is deliberate: a game
   * that has not been analysed has no metrics, and showing zeroes would claim
   * flawless play rather than absent data.
   */
  moveQuality: GameMoveQuality | null;
  currentPly: number;
  /** Absent for a read-only render; supplied by the viewer, which owns ply. */
  onSelectPly?: (ply: number) => void;
  error?: string | null;
}

export function MoveQualityPanel({
  moveQuality,
  currentPly,
  onSelectPly,
  error = null,
}: MoveQualityPanelProps) {
  if (error) {
    return (
      <section className="move-quality">
        <h3>Move quality</h3>
        <p className="upload-error" role="alert">
          {error}
        </p>
      </section>
    );
  }

  if (moveQuality === null) return null;
  if (moveQuality.status === "not_gradable") return <NotGradableNotice detail={moveQuality} />;
  if (moveQuality.status !== "complete") return null;

  return (
    <CompleteMoveQualityPanel
      moveQuality={moveQuality}
      currentPly={currentPly}
      onSelectPly={onSelectPly}
    />
  );
}

function NotGradableNotice({ detail }: { detail: NotGradable }) {
  return (
    <section className="move-quality">
      <h3>Move quality</h3>
      <p className="analysis-note">{detail.detail}</p>
    </section>
  );
}

interface CompletePanelProps {
  moveQuality: CompleteMoveQuality;
  currentPly: number;
  onSelectPly?: (ply: number) => void;
}

function CompleteMoveQualityPanel({
  moveQuality,
  currentPly,
  onSelectPly,
}: CompletePanelProps) {
  const [filter, setFilter] = useState<MoveFilter>("critical");

  const summary = moveQuality.summary;
  const current = moveQuality.moves.find((move) => move.ply === currentPly) ?? null;
  const shown = filterMoves(moveQuality.moves, filter);

  return (
    <section className="move-quality">
      <h3>Move quality</h3>
      <p className="analysis-note">
        {`Your moves as ${moveQuality.player_color}, graded against the stored engine evaluations.`}
      </p>

      <div className="stat-grid">
        <StatCard
          label="Average centipawn loss"
          value={summary.average_centipawn_loss ?? NO_VALUE}
        />
        <StatCard
          label="Median centipawn loss"
          value={summary.median_centipawn_loss ?? NO_VALUE}
        />
        <StatCard
          label="Engine's top move"
          value={
            summary.exact_best_move_percent === null
              ? NO_VALUE
              : `${summary.exact_best_move_percent}%`
          }
        />
        <StatCard label="Critical mistakes" value={summary.critical_mistakes} />
      </div>

      <ul className="move-quality-counts">
        <li>{`Inaccuracies: ${summary.inaccuracies}`}</li>
        <li>{`Mistakes: ${summary.mistakes}`}</li>
        <li>{`Blunders: ${summary.blunders}`}</li>
        <li>{`Missed mates: ${summary.missed_mates}`}</li>
        <li>{`Allowed mates: ${summary.allowed_mates}`}</li>
      </ul>

      <CurrentMove move={current} />

      <ReviewControls
        moves={shown}
        filter={filter}
        onChangeFilter={setFilter}
        currentPly={currentPly}
        onSelectPly={onSelectPly}
      />

      {/* Numerator and denominator for both samples. "40 games" must never be
          able to render as "79", and the same rule applies to moves. */}
      <p className="analysis-note">
        {`Averaged over ${summary.numeric_cpl_moves} of your ${summary.total_player_moves} moves. `}
        {`Engine's top move counted over ${summary.exact_best_move_comparable}.`}
      </p>

      {summary.unscored_moves > 0 && (
        <p className="analysis-note">
          {`${summary.unscored_moves} ${
            summary.unscored_moves === 1 ? "move was" : "moves were"
          } not scored, usually because the position was already a forced mate.`}
        </p>
      )}

      <p className="analysis-note">
        Centipawn loss is how far the engine&apos;s evaluation dropped after
        your move - 100 centipawns is one pawn, and lower is better.
        &quot;Engine&apos;s top move&quot; counts the moves that exactly matched
        the engine&apos;s first choice; several moves are often nearly as
        strong, so a lower figure is not itself a mistake.
      </p>
    </section>
  );
}

interface ReviewControlsProps {
  /** Already filtered, so the list and the buttons cannot disagree. */
  moves: MoveQuality[];
  filter: MoveFilter;
  onChangeFilter: (filter: MoveFilter) => void;
  currentPly: number;
  onSelectPly?: (ply: number) => void;
}

function ReviewControls({
  moves,
  filter,
  onChangeFilter,
  currentPly,
  onSelectPly,
}: ReviewControlsProps) {
  const previous = previousMovePly(moves, currentPly);
  const next = nextMovePly(moves, currentPly);

  return (
    <div className="move-quality-review">
      <fieldset className="move-quality-filters">
        <legend>Show</legend>
        {MOVE_FILTERS.map((option) => (
          <label key={option}>
            <input
              type="radio"
              name="move-quality-filter"
              value={option}
              checked={filter === option}
              onChange={() => onChangeFilter(option)}
            />
            {FILTER_LABEL[option]}
          </label>
        ))}
      </fieldset>

      <div className="move-quality-steps">
        <button
          type="button"
          onClick={() => previous !== null && onSelectPly?.(previous)}
          // Disabled at the ends rather than wrapping: silently jumping from
          // the last mistake back to the first reads as a lost click.
          disabled={previous === null}
        >
          Previous issue
        </button>
        <button
          type="button"
          onClick={() => next !== null && onSelectPly?.(next)}
          disabled={next === null}
        >
          Next issue
        </button>
        <span className="analysis-note" role="status">
          {moves.length === 1 ? "1 move" : `${moves.length} moves`}
        </span>
      </div>

      {moves.length === 0 ? (
        <p className="analysis-note">No moves match this filter.</p>
      ) : (
        <ol className="move-quality-issues" aria-label="Moves to review">
          {moves.map((move) => (
            <li key={move.ply}>
              <button
                type="button"
                className={move.ply === currentPly ? "selected" : undefined}
                onClick={() => onSelectPly?.(move.ply)}
              >
                <span className="move-quality-move">
                  {`${move.side === "white" ? `${move.move_number}.` : `${move.move_number}...`} ${move.san}`}
                </span>
                {/* Leading comma for the same reason as the move-list
                    annotation: accessible-name computation trims each
                    element, so a space here would not survive the join. */}
                <span className="move-quality-issue-verdict">
                  {`, ${describeQuality(move)}`}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function CurrentMove({ move }: { move: MoveQuality | null }) {
  if (move === null) {
    return (
      <p className="analysis-note">
        Select one of your moves to see how it was assessed.
      </p>
    );
  }

  const annotation = moveAnnotation(move);
  const tone = annotation?.tone ?? "good";
  const prefix = move.side === "white" ? `${move.move_number}.` : `${move.move_number}...`;

  return (
    <div className="move-quality-current" role="group" aria-label="Current move">
      <span className="move-quality-move">{`${prefix} ${move.san}`}</span>
      <span className={`move-quality-verdict move-quality-verdict-${tone}`}>
        {describeQuality(move)}
      </span>
      {move.centipawn_loss !== null && (
        <span className="analysis-note">{`${move.centipawn_loss} centipawns lost`}</span>
      )}
      {/* Separate from the verdict on purpose: matching the engine is
          evidence, not a grade, and failing to match is not a mistake. */}
      {move.is_exact_best_move === true && (
        <span className="move-quality-badge">Matched the engine</span>
      )}
      <span className="analysis-note">
        {`${formatPlayerScore(move.score_before)} to ${formatPlayerScore(move.score_after)}`}
      </span>
    </div>
  );
}
