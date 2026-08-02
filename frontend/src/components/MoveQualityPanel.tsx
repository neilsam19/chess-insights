import type {
  CompleteMoveQuality,
  GameMoveQuality,
  MoveQuality,
  NotGradable,
} from "../types/moveQuality";
import {
  NO_VALUE,
  describeQuality,
  formatPlayerScore,
  moveAnnotation,
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
  error?: string | null;
}

export function MoveQualityPanel({
  moveQuality,
  currentPly,
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

  return <CompleteMoveQualityPanel moveQuality={moveQuality} currentPly={currentPly} />;
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
}

function CompleteMoveQualityPanel({ moveQuality, currentPly }: CompletePanelProps) {
  const summary = moveQuality.summary;
  const current = moveQuality.moves.find((move) => move.ply === currentPly) ?? null;

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
