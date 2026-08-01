import { useCallback, useEffect, useRef, useState } from "react";
import { analyzeUpload, cancelBatch, getBatch } from "../api/analysis";
import { LibraryError } from "../api/library";
import type { BatchCreated, BatchStatus } from "../types/analysis";
import { isBatchActive } from "../types/analysis";

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_INTERVAL_MS = 20000;

interface UploadAnalysisPanelProps {
  uploadId: string;
  totalGames: number;
}

/**
 * Queue an entire upload for analysis and watch it work through.
 *
 * Deliberately does not remember a batch across page loads: a batch id is not
 * a property of the upload, and inventing a "current batch" would be wrong the
 * moment a second one exists. Reopening the page shows the action again, and
 * pressing it is harmless because the server skips everything already done.
 */
export function UploadAnalysisPanel({
  uploadId,
  totalGames,
}: UploadAnalysisPanelProps) {
  const [batch, setBatch] = useState<BatchStatus | null>(null);
  const [summary, setSummary] = useState<BatchCreated | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const backoffRef = useRef(POLL_INTERVAL_MS);
  const active = isBatchActive(batch);
  const batchId = batch?.batch_id ?? null;

  useEffect(() => {
    if (!active || !batchId) {
      backoffRef.current = POLL_INTERVAL_MS;
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const next = await getBatch(batchId);
        if (cancelled) return;
        backoffRef.current = POLL_INTERVAL_MS;
        setBatch(next);
      } catch {
        // A dropped connection is not a failed batch - the worker carries on
        // regardless of whether anyone is watching.
        if (cancelled) return;
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_POLL_INTERVAL_MS);
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
  }, [active, batchId]);

  const start = useCallback(async () => {
    setIsBusy(true);
    setError(null);
    try {
      const created = await analyzeUpload(uploadId);
      setSummary(created);
      setBatch(created.batch_id ? await getBatch(created.batch_id) : null);
    } catch (err) {
      setError(
        err instanceof LibraryError
          ? err.message
          : "Something went wrong queueing this upload.",
      );
    } finally {
      setIsBusy(false);
    }
  }, [uploadId]);

  const stop = useCallback(async () => {
    if (!batchId) return;
    setIsBusy(true);
    setError(null);
    try {
      setBatch(await cancelBatch(batchId));
    } catch (err) {
      setError(
        err instanceof LibraryError ? err.message : "Could not cancel this batch.",
      );
    } finally {
      setIsBusy(false);
    }
  }, [batchId]);

  return (
    <section className="upload-analysis">
      <div className="analysis-status">
        <button type="button" onClick={start} disabled={isBusy || active}>
          Analyze All Games
        </button>

        {!batch && !summary && (
          <span className="analysis-note">
            Queues all {totalGames} games. They are analysed one at a time in the
            background, so this never floods your machine.
          </span>
        )}

        {batch && active && (
          <>
            <span className="analysis-spinner" aria-hidden="true" />
            <span role="status">
              {batch.games_complete} of {batch.total_jobs} games analysed
              {batch.cancel_requested ? " - cancelling..." : ""}
            </span>
            <progress
              className="analysis-progress"
              value={batch.completed_positions}
              max={batch.total_positions}
              aria-label="Upload analysis progress"
            />
          </>
        )}

        {batch && active && batch.can_cancel && (
          <button type="button" onClick={stop} disabled={isBusy}>
            Cancel All
          </button>
        )}
      </div>

      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {summary && <EnqueueSummary summary={summary} />}
      {batch && !active && <FinishedBatch batch={batch} />}
      {batch && <BatchCounts batch={batch} />}
    </section>
  );
}

function EnqueueSummary({ summary }: { summary: BatchCreated }) {
  // What was *not* queued matters as much as what was: it is the difference
  // between "nothing happened" and "nothing needed to happen".
  const parts: string[] = [];
  if (summary.queued) parts.push(`${summary.queued} queued`);
  if (summary.skipped_complete)
    parts.push(`${summary.skipped_complete} already analysed`);
  if (summary.already_active) parts.push(`${summary.already_active} already queued`);
  if (summary.ineligible) parts.push(`${summary.ineligible} with no moves`);

  return (
    <p className="analysis-note">
      {parts.length ? parts.join(" · ") : "Nothing to analyse."}
      {` · depth ${summary.engine.requested_depth}`}
    </p>
  );
}

function FinishedBatch({ batch }: { batch: BatchStatus }) {
  const message: Record<string, string> = {
    complete: "All games analysed.",
    partial_failed: "Finished, but some games failed.",
    failed: "Every game failed.",
    cancelled: "Cancelled. Games already analysed were kept.",
  };

  return (
    <p role="status" className="analysis-note">
      {message[batch.status] ?? "Finished."}
    </p>
  );
}

function BatchCounts({ batch }: { batch: BatchStatus }) {
  const entries: Array<[string, number]> = [
    ["complete", batch.counts.complete],
    ["running", batch.counts.running],
    ["queued", batch.counts.queued],
    ["failed", batch.counts.failed],
    ["cancelled", batch.counts.cancelled],
  ];

  return (
    <ul className="batch-counts">
      {entries
        .filter(([, count]) => count > 0)
        .map(([label, count]) => (
          <li key={label}>
            <strong>{count}</strong> {label}
          </li>
        ))}
    </ul>
  );
}
