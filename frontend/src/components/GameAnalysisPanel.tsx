import { useCallback, useEffect, useRef, useState } from "react";
import { analyzeGame, cancelJob, getGameAnalysis, retryJob } from "../api/analysis";
import { LibraryError } from "../api/library";
import type { GameAnalysis } from "../types/analysis";
import { isActive } from "../types/analysis";
import { describeScore, formatScore, plyLabel } from "../utils/evaluation";
import { EvaluationGraph } from "./EvaluationGraph";

/** How often to ask the server for progress while a job is active. */
const POLL_INTERVAL_MS = 1500;
/**
 * After a failed poll the interval doubles, up to this. A dropped connection
 * should not turn into a request storm, and analysis keeps running on the
 * server regardless of whether we are watching.
 */
const MAX_POLL_INTERVAL_MS = 15000;

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
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollingStalled, setPollingStalled] = useState(false);

  // Held in a ref rather than state: changing the backoff must not re-render,
  // and the poll loop needs the current value without re-subscribing.
  const backoffRef = useRef(POLL_INTERVAL_MS);

  const active = isActive(analysis);

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

  // Poll only while a worker might still change the outcome. Terminal states
  // stop the timer entirely rather than idling against the server.
  useEffect(() => {
    if (!active) {
      // Only the ref is reset here. Clearing `pollingStalled` would be a
      // synchronous setState inside an effect, which cascades renders; the
      // banner is gated on `active` when rendering instead, and the flag is
      // cleared by the action that starts new work.
      backoffRef.current = POLL_INTERVAL_MS;
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const result = await getGameAnalysis(gameId);
        if (cancelled) return;
        backoffRef.current = POLL_INTERVAL_MS;
        setPollingStalled(false);
        setAnalysis(result);
      } catch {
        if (cancelled) return;
        // A failed poll is a connection problem, never an analysis failure -
        // the job is still running on the server.
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_POLL_INTERVAL_MS);
        setPollingStalled(true);
      }
      if (!cancelled) {
        timer = setTimeout(poll, backoffRef.current);
      }
    };

    timer = setTimeout(poll, backoffRef.current);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [active, gameId]);

  const runCommand = useCallback(
    async (command: () => Promise<unknown>, failureMessage: string) => {
      setIsBusy(true);
      setError(null);
      setPollingStalled(false);
      try {
        await command();
        setAnalysis(await getGameAnalysis(gameId));
      } catch (err) {
        setError(err instanceof LibraryError ? err.message : failureMessage);
      } finally {
        setIsBusy(false);
      }
    },
    [gameId],
  );

  const startAnalysis = useCallback(
    () =>
      runCommand(
        () => analyzeGame(gameId),
        "Something went wrong while queueing this analysis.",
      ),
    [gameId, runCommand],
  );

  const stopAnalysis = useCallback(
    (jobId: string) =>
      runCommand(() => cancelJob(jobId), "Could not cancel this analysis."),
    [runCommand],
  );

  const retryAnalysis = useCallback(
    (jobId: string) =>
      runCommand(() => retryJob(jobId), "Could not retry this analysis."),
    [runCommand],
  );

  if (isLoading) {
    return (
      <section className="analysis-panel">
        <p>Loading analysis...</p>
      </section>
    );
  }

  return (
    <section className="analysis-panel">
      <h3>Engine analysis</h3>

      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {analysis?.status === "not_analyzed" && (
        <div className="analysis-status">
          <button type="button" onClick={startAnalysis} disabled={isBusy}>
            Analyze Game
          </button>
          <span className="analysis-note">
            Stockfish evaluates every position. This runs in the background, so
            you can keep browsing.
          </span>
        </div>
      )}

      {(analysis?.status === "queued" || analysis?.status === "running") && (
        <ActiveAnalysis
          analysis={analysis}
          isBusy={isBusy}
          stalled={active && pollingStalled}
          onCancel={stopAnalysis}
        />
      )}

      {analysis?.status === "failed" && (
        <div className="analysis-status">
          <p className="upload-error" role="alert">
            {analysis.error_message ?? "Analysis failed."}
          </p>
          {analysis.can_retry && analysis.job_id ? (
            <button
              type="button"
              onClick={() => retryAnalysis(analysis.job_id as string)}
              disabled={isBusy}
            >
              Retry Analysis
            </button>
          ) : (
            <button type="button" onClick={startAnalysis} disabled={isBusy}>
              Analyze Game
            </button>
          )}
        </div>
      )}

      {analysis?.status === "cancelled" && (
        <div className="analysis-status">
          <span role="status">Analysis cancelled.</span>
          <button
            type="button"
            onClick={() =>
              analysis.can_retry
                ? retryAnalysis(analysis.job_id)
                : startAnalysis()
            }
            disabled={isBusy}
          >
            Analyze Game
          </button>
        </div>
      )}

      {analysis?.status === "complete" && (
        <CompleteAnalysis
          analysis={analysis}
          currentPly={currentPly}
          onSelectPly={onSelectPly}
        />
      )}
    </section>
  );
}

interface ActiveAnalysisProps {
  analysis: Extract<GameAnalysis, { status: "queued" | "running" }>;
  isBusy: boolean;
  stalled: boolean;
  onCancel: (jobId: string) => void;
}

function ActiveAnalysis({
  analysis,
  isBusy,
  stalled,
  onCancel,
}: ActiveAnalysisProps) {
  const progress = analysis.progress;
  const queued = analysis.status === "queued";
  const cancelling = analysis.status === "running" && analysis.cancel_requested;

  return (
    <div className="analysis-status">
      <span className="analysis-spinner" aria-hidden="true" />
      <span role="status">
        {cancelling
          ? "Cancelling - finishing the current position..."
          : queued
            ? "Queued for analysis..."
            : `Analyzing position ${progress?.completed_positions ?? 0} of ${
                progress?.total_positions ?? 0
              }...`}
      </span>

      {progress && !queued && !cancelling && (
        <progress
          className="analysis-progress"
          value={progress.completed_positions}
          max={progress.total_positions}
          aria-label="Analysis progress"
        />
      )}

      {progress && progress.attempt > 1 && !cancelling && (
        <span className="analysis-note">
          Attempt {progress.attempt} of {progress.max_attempts}
        </span>
      )}

      {analysis.job_id && (
        <button
          type="button"
          onClick={() => onCancel(analysis.job_id as string)}
          // Stays visible but inert once cancellation is under way: removing
          // it would look like the click was lost, and asking twice achieves
          // nothing.
          disabled={isBusy || !analysis.can_cancel}
        >
          {cancelling ? "Cancelling..." : "Cancel"}
        </button>
      )}

      {stalled && (
        <span className="analysis-note">
          Lost contact with the server - still trying. Analysis continues in the
          background.
        </span>
      )}
    </div>
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
