import type {
  ColorMoveQuality,
  MoveQualityMetrics,
  OpeningMoveQuality,
  ReviewMove,
  TrainingRecommendation,
  UploadCoverage,
  UploadMoveQuality,
} from "../types/uploadMoveQuality";
import { gamesWithoutAnalysis } from "../types/uploadMoveQuality";
import {
  NO_VALUE,
  formatPawnLoss,
  formatPlayerScore,
  qualityLabel,
} from "../utils/moveQuality";
import { formatOpeningStatName } from "../utils/openings";
import { StatCard } from "./StatCard";

interface TrainingInsightsProps {
  /** Null while loading, or when the request failed - see `error`. */
  insights: UploadMoveQuality | null;
  error?: string | null;
  isLoading?: boolean;
  /** Opens a position. Absent renders the evidence as text rather than links. */
  onOpenPosition?: (gameId: string, ply: number) => void;
}

export function TrainingInsights({
  insights,
  error = null,
  isLoading = false,
  onOpenPosition,
}: TrainingInsightsProps) {
  if (error) {
    return (
      <section className="training-insights" aria-labelledby="training-insights-heading">
        <h2 id="training-insights-heading">Training insights</h2>
        {/* Distinct from "nothing analysed yet": one is a state of the
            library, the other is a state of this page. Conflating them would
            tell the reader to run analysis they have already run. */}
        <p className="upload-error" role="alert">
          {error}
        </p>
      </section>
    );
  }

  if (isLoading || insights === null) {
    return (
      <section className="training-insights" aria-labelledby="training-insights-heading">
        <h2 id="training-insights-heading">Training insights</h2>
        <p role="status">Working out your training insights...</p>
      </section>
    );
  }

  const { coverage, overall } = insights;

  return (
    <section className="training-insights" aria-labelledby="training-insights-heading">
      <h2 id="training-insights-heading">Training insights</h2>

      <CoverageLine coverage={coverage} />

      {coverage.complete_games === 0 ? (
        <p className="analysis-note">
          Nothing has been analysed yet. Run the analysis above and these
          figures will appear.
        </p>
      ) : (
        <>
          <Metrics metrics={overall} />
          <ByColor rows={insights.by_color} />
          <ByOpening rows={insights.by_opening} />
          <Recommendations
            recommendations={insights.recommendations}
            onOpenPosition={onOpenPosition}
          />
          <TopPositions
            moves={insights.top_review_moves}
            onOpenPosition={onOpenPosition}
          />
        </>
      )}
    </section>
  );
}

function CoverageLine({ coverage }: { coverage: UploadCoverage }) {
  const outstanding = gamesWithoutAnalysis(coverage);
  const inFlight = coverage.queued_games + coverage.running_games;

  return (
    <div className="training-coverage">
      {/* The denominator is never dropped. Everything below this line is
          computed over `complete_games`, and saying so once, prominently, is
          what stops 40 analysed games from reading as 79. */}
      <p className="training-coverage-headline">
        {`Based on ${coverage.complete_games} of ${coverage.total_games} games`}
      </p>
      <p className="analysis-note">
        {`${coverage.graded_player_moves} of your moves graded.`}
        {inFlight > 0 ? ` ${inFlight} games still being analysed.` : ""}
        {outstanding > 0 ? ` ${outstanding} games not analysed yet.` : ""}
        {coverage.not_gradable_games > 0
          ? ` ${coverage.not_gradable_games} games were saved under a name that matched neither player and cannot be graded.`
          : ""}
      </p>
    </div>
  );
}

function Metrics({ metrics }: { metrics: MoveQualityMetrics }) {
  return (
    <>
      <div className="stat-grid">
        <StatCard
          label="Average centipawn loss"
          value={metrics.average_centipawn_loss ?? NO_VALUE}
        />
        <StatCard
          label="Median centipawn loss"
          value={metrics.median_centipawn_loss ?? NO_VALUE}
        />
        <StatCard
          label="Engine's top move"
          value={
            metrics.exact_best_move_percent === null
              ? NO_VALUE
              : `${metrics.exact_best_move_percent}%`
          }
        />
        <StatCard label="Critical mistakes" value={metrics.critical_mistakes} />
      </div>

      <ul className="move-quality-counts">
        <li>{`Inaccuracies: ${metrics.inaccuracies}`}</li>
        <li>{`Mistakes: ${metrics.mistakes}`}</li>
        <li>{`Blunders: ${metrics.blunders}`}</li>
        <li>{`Missed mates: ${metrics.missed_mates}`}</li>
        <li>{`Allowed mates: ${metrics.allowed_mates}`}</li>
      </ul>
    </>
  );
}

/** Sample sizes sit beside every figure, never behind a tooltip. */
function MetricColumns({ metrics }: { metrics: MoveQualityMetrics }) {
  return (
    <>
      <td>{metrics.games}</td>
      <td>{metrics.graded_moves}</td>
      <td>{metrics.average_centipawn_loss ?? NO_VALUE}</td>
      <td>{metrics.critical_mistakes}</td>
    </>
  );
}

/**
 * The headings matching `MetricColumns`, as cells rather than a row.
 *
 * A `<tr>` here would be nested inside the caller's `<tr>`, which browsers
 * repair by hoisting the cells out - leaving the headings and the values in
 * different rows and every column one place out.
 */
function MetricHeadings() {
  return (
    <>
      <th>Games</th>
      <th>Graded moves</th>
      <th>Average loss</th>
      <th>Critical</th>
    </>
  );
}

function ByColor({ rows }: { rows: ColorMoveQuality[] }) {
  if (rows.length === 0) return null;

  return (
    <table className="opening-table" aria-label="By colour">
      <thead>
        <tr>
          <th>Colour</th>
          <MetricHeadings />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.color}>
            <td>{row.color === "white" ? "White" : "Black"}</td>
            <MetricColumns metrics={row.metrics} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ByOpening({ rows }: { rows: OpeningMoveQuality[] }) {
  if (rows.length === 0) return null;

  return (
    <table className="opening-table" aria-label="By opening">
      <thead>
        <tr>
          <th>Opening</th>
          <MetricHeadings />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.opening ?? "__unclassified__"}>
            <td>
              {row.opening === null
                ? "Not classified"
                : formatOpeningStatName(row.opening)}
            </td>
            <MetricColumns metrics={row.metrics} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface RecommendationsProps {
  recommendations: TrainingRecommendation[];
  onOpenPosition?: (gameId: string, ply: number) => void;
}

function Recommendations({ recommendations, onOpenPosition }: RecommendationsProps) {
  if (recommendations.length === 0) {
    return (
      <p className="analysis-note">
        There is not yet enough analysed data to suggest what to work on.
        Analysing more games will change that.
      </p>
    );
  }

  return (
    <ol className="recommendation-list" aria-label="What to work on">
      {recommendations.map((recommendation) => (
        <li key={recommendation.type}>
          <article className="recommendation">
            <h3>{recommendation.title}</h3>
            <p>{recommendation.explanation}</p>
            <p className="analysis-note">{recommendation.evidence_summary}</p>
            <PositionList
              moves={recommendation.evidence_items}
              label={`Evidence for ${recommendation.title}`}
              onOpenPosition={onOpenPosition}
            />
            {/* Shown on every card, not once per page: a reader who scrolls
                to one recommendation must still see the sample behind it. */}
            <p className="analysis-note">{recommendation.coverage_note}</p>
          </article>
        </li>
      ))}
    </ol>
  );
}

interface PositionListProps {
  moves: ReviewMove[];
  label: string;
  onOpenPosition?: (gameId: string, ply: number) => void;
}

function PositionList({ moves, label, onOpenPosition }: PositionListProps) {
  if (moves.length === 0) return null;

  return (
    <ol className="position-list" aria-label={label}>
      {moves.map((move) => (
        <li key={`${move.game_id}-${move.ply}`}>
          <PositionRow move={move} onOpenPosition={onOpenPosition} />
        </li>
      ))}
    </ol>
  );
}

function positionText(move: ReviewMove): string {
  const number =
    move.side === "white" ? `${move.move_number}.` : `${move.move_number}...`;
  const label = qualityLabel(move.quality, move.special_classification);
  // Mate errors carry no centipawn figure, here as everywhere else.
  const verdict =
    move.centipawn_loss === null
      ? label
      : `${label}, lost ${formatPawnLoss(move.centipawn_loss)} pawns`;

  // Games are numbered from zero in storage and from one for a reader.
  return `Game ${move.game_index + 1}, ${number} ${move.san}, ${verdict}`;
}

function PositionRow({
  move,
  onOpenPosition,
}: {
  move: ReviewMove;
  onOpenPosition?: (gameId: string, ply: number) => void;
}) {
  const detail = (
    <span className="analysis-note">
      {`${formatPlayerScore(move.score_before)} to ${formatPlayerScore(move.score_after)}`}
    </span>
  );

  if (!onOpenPosition) {
    return (
      <span className="position-row">
        <span>{positionText(move)}</span>
        {detail}
      </span>
    );
  }

  return (
    <button
      type="button"
      className="position-row"
      onClick={() => onOpenPosition(move.game_id, move.ply)}
    >
      <span>{positionText(move)}</span>
      {detail}
    </button>
  );
}

function TopPositions({
  moves,
  onOpenPosition,
}: {
  moves: ReviewMove[];
  onOpenPosition?: (gameId: string, ply: number) => void;
}) {
  if (moves.length === 0) {
    return (
      <p className="analysis-note">
        No move in your analysed games crossed the mistake threshold.
      </p>
    );
  }

  return (
    <div className="top-positions">
      <h3>Positions to review</h3>
      <PositionList
        moves={moves}
        label="Positions to review"
        onOpenPosition={onOpenPosition}
      />
    </div>
  );
}
