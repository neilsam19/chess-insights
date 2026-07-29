import type { PositionEvaluation } from "../types/analysis";
import {
  MATE_GRAPH_BAND,
  describeScore,
  plyLabel,
  toGraphValues,
} from "../utils/evaluation";

const WIDTH = 600;
const HEIGHT = 140;
const PADDING_Y = 8;

interface EvaluationGraphProps {
  evaluations: PositionEvaluation[];
  currentPly: number;
  onSelectPly?: (ply: number) => void;
}

/**
 * A small hand-rolled SVG chart - deliberately no charting library, which
 * keeps the bundle small and makes ply alignment exact rather than
 * approximate.
 *
 * The y-axis is the saved player's perspective: above the zero line means
 * the player is better.
 */
export function EvaluationGraph({
  evaluations,
  currentPly,
  onSelectPly,
}: EvaluationGraphProps) {
  if (evaluations.length === 0) return null;

  const values = toGraphValues(evaluations);
  const usableHeight = HEIGHT / 2 - PADDING_Y;

  const x = (index: number) =>
    evaluations.length === 1 ? WIDTH / 2 : (index / (evaluations.length - 1)) * WIDTH;
  const y = (value: number) => HEIGHT / 2 - (value / MATE_GRAPH_BAND) * usableHeight;

  const linePoints = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const areaPoints = `0,${HEIGHT / 2} ${linePoints} ${WIDTH},${HEIGHT / 2}`;

  const currentIndex = evaluations.findIndex((e) => e.ply === currentPly);
  const current = currentIndex >= 0 ? evaluations[currentIndex] : null;

  return (
    <svg
      className="evaluation-graph"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label={`Evaluation graph across ${evaluations.length} positions, positive favours White`}
    >
      <polygon className="evaluation-graph-area" points={areaPoints} />

      {/* Zero line - equal position. */}
      <line
        className="evaluation-graph-zero"
        x1={0}
        y1={HEIGHT / 2}
        x2={WIDTH}
        y2={HEIGHT / 2}
      />

      <polyline className="evaluation-graph-line" points={linePoints} />

      {current && (
        <line
          className="evaluation-graph-cursor"
          x1={x(currentIndex)}
          y1={0}
          x2={x(currentIndex)}
          y2={HEIGHT}
        />
      )}

      {current && (
        <circle
          className="evaluation-graph-point"
          cx={x(currentIndex)}
          cy={y(values[currentIndex])}
          r={4}
        />
      )}

      {/* Invisible hit targets, one per ply, so clicking jumps the board. */}
      {evaluations.map((evaluation, index) => (
        <rect
          key={evaluation.ply}
          className="evaluation-graph-hit"
          x={index === 0 ? 0 : x(index) - WIDTH / (evaluations.length - 1) / 2}
          y={0}
          width={WIDTH / Math.max(1, evaluations.length - 1)}
          height={HEIGHT}
          onClick={() => onSelectPly?.(evaluation.ply)}
        >
          <title>{`${plyLabel(evaluation.ply)} ${describeScore(evaluation.white_score)}`}</title>
        </rect>
      ))}
    </svg>
  );
}
