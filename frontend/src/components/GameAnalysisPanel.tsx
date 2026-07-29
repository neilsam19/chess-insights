import { useCallback, useEffect, useState } from "react";
import { analyzeGame, getGameAnalysis } from "../api/analysis";
import { LibraryError } from "../api/library";
import type { GameAnalysis } from "../types/analysis";
import { describeScore, formatScore, plyLabel } from "../utils/evaluation";
import { EvaluationGraph } from "./EvaluationGraph";

interface GameAnalysisPanelProps {
  /** Persisted game id. Only saved games can be analyzed. */
  gameId: string;
  currentPly: number;
  onSelectPly?: (ply: number) => void;
}

export function GameAnalysisPanel({
  gameId,
  currentPly,
  onSelectPly,
}: GameAnalysisPanelProps) {
  const [analysis, setAnalysis] = useState<GameAnalysis | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The panel is keyed by gameId where it is mounted, so selecting a
  // different game remounts it with fresh state. That is why this effect
  // never has to reset state synchronously on the way in.
  useEffect(() => {
    let cancelled = false;

    getGameAnalysis(gameId)
      .then((result) => {
        if (!cancelled) setAnalysis(result);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load analysis for this game.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [gameId]);

  const runAnalysis = useCallback(async () => {
    setIsAnalyzing(true);
    setError(null);
    try {
      setAnalysis(await analyzeGame(gameId));
    } catch (err) {
      setError(
        err instanceof LibraryError
          ? err.message
          : "Something went wrong while analyzing this game.",
      );
    } finally {
      setIsAnalyzing(false);
    }
  }, [gameId]);

  if (isLoading) {
    return (
      <section className="analysis-panel">
        <p>Loading analysis...</p>
      </section>
    );
  }

  // The request is synchronous, so this covers both "we are waiting" and a
  // run left behind by a previous session.
  const showAnalyzing = isAnalyzing || analysis?.status === "running";

  return (
    <section className="analysis-panel">
      <h3>Engine analysis</h3>

      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {showAnalyzing && (
        <div className="analysis-status">
          <span className="analysis-spinner" aria-hidden="true" />
          <span role="status">Analyzing game...</span>
          <button type="button" disabled>
            Analyzing...
          </button>
        </div>
      )}

      {!showAnalyzing && analysis?.status === "not_analyzed" && (
        <div className="analysis-status">
          <button type="button" onClick={runAnalysis}>
            Analyze Game
          </button>
          <span className="analysis-note">
            Stockfish evaluates every position, which can take a moment.
          </span>
        </div>
      )}

      {!showAnalyzing && analysis?.status === "failed" && (
        <div className="analysis-status">
          <p className="upload-error" role="alert">
            {analysis.error_message ?? "Analysis failed."}
          </p>
          <button type="button" onClick={runAnalysis}>
            Retry Analysis
          </button>
        </div>
      )}

      {!showAnalyzing && analysis?.status === "complete" && (
        <CompleteAnalysis
          analysis={analysis}
          currentPly={currentPly}
          onSelectPly={onSelectPly}
        />
      )}
    </section>
  );
}

interface CompleteAnalysisProps {
  analysis: Extract<GameAnalysis, { status: "complete" }>;
  currentPly: number;
  onSelectPly?: (ply: number) => void;
}

function CompleteAnalysis({
  analysis,
  currentPly,
  onSelectPly,
}: CompleteAnalysisProps) {
  const current =
    analysis.evaluations.find((e) => e.ply === currentPly) ?? analysis.evaluations[0];

  return (
    <>
      {/* Canonical White-perspective values, matching standard engine
          convention - the player-relative `score` is left for Sprint 6's
          move-quality work. */}
      <div className="analysis-current">
        <span className="analysis-score">
          {current ? formatScore(current.white_score) : "-"}
        </span>
        <span className="analysis-note">
          {current ? describeScore(current.white_score) : ""}
          {current ? ` at ${plyLabel(current.ply)}` : ""}
        </span>
      </div>

      <EvaluationGraph
        evaluations={analysis.evaluations}
        currentPly={currentPly}
        onSelectPly={onSelectPly}
      />

      <p className="analysis-meta">
        {analysis.engine.name}
        {analysis.engine.version ? ` ${analysis.engine.version}` : ""}
        {` · depth ${analysis.engine.requested_depth}`}
        {" · standard engine convention (+ favours White)"}
      </p>
    </>
  );
}
