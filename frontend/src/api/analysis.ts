import { LibraryError } from "./library";
import type {
  BatchCreated,
  BatchStatus,
  EngineStatus,
  GameAnalysis,
  JobStatus,
} from "../types/analysis";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8001";

async function throwForResponse(response: Response): Promise<never> {
  const body: unknown = await response.json().catch(() => null);

  let detail = `Request failed with status ${response.status}`;
  if (body && typeof body === "object" && "detail" in body) {
    const raw = (body as { detail: unknown }).detail;
    if (typeof raw === "string") {
      detail = raw;
    } else if (raw && typeof raw === "object" && "message" in raw) {
      detail = String((raw as { message: unknown }).message);
    }
  }

  throw new LibraryError(detail, response.status);
}

export async function getGameAnalysis(gameId: string): Promise<GameAnalysis> {
  const response = await fetch(`${API_BASE_URL}/api/games/${gameId}/analysis`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as GameAnalysis;
}

/**
 * Queues analysis of one saved game. Sends no body at all - positions, engine
 * identity, and depth are entirely server-side.
 *
 * Returns as soon as the job is durably recorded, so the response is normally
 * `queued` rather than a finished analysis. A separate worker does the work;
 * poll `getGameAnalysis` for the result.
 *
 * 409 is not treated specially here: the server returns the game's current
 * state alongside it, so "already queued" and "just queued" render the same.
 */
export async function analyzeGame(gameId: string): Promise<GameAnalysis> {
  const response = await fetch(`${API_BASE_URL}/api/games/${gameId}/analysis`, {
    method: "POST",
  });

  // 202 queued, 200 reused, 409 already active - all describe the same game
  // state and all carry a GameAnalysis body.
  if (!response.ok && response.status !== 409) {
    return throwForResponse(response);
  }

  return (await response.json()) as GameAnalysis;
}

/**
 * Queues every game in an upload that still needs analysing.
 *
 * Safe to call twice: games with a matching completed analysis are skipped and
 * games already queued are left alone. 200 means nothing needed doing.
 */
export async function analyzeUpload(uploadId: string): Promise<BatchCreated> {
  const response = await fetch(
    `${API_BASE_URL}/api/uploads/${uploadId}/analysis`,
    { method: "POST" },
  );

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as BatchCreated;
}

export async function getBatch(batchId: string): Promise<BatchStatus> {
  const response = await fetch(`${API_BASE_URL}/api/analysis/batches/${batchId}`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as BatchStatus;
}

/** Stops everything unfinished. Games already analysed keep their results. */
export async function cancelBatch(batchId: string): Promise<BatchStatus> {
  const response = await fetch(
    `${API_BASE_URL}/api/analysis/batches/${batchId}/cancel`,
    { method: "POST" },
  );

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as BatchStatus;
}

export async function getJob(jobId: string): Promise<JobStatus> {
  const response = await fetch(`${API_BASE_URL}/api/analysis/jobs/${jobId}`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as JobStatus;
}

/**
 * Asks a job to stop. A queued job is cancelled at once; a running one stops
 * at its next position boundary, so the reported status may briefly still be
 * `running` afterwards.
 */
export async function cancelJob(jobId: string): Promise<JobStatus> {
  const response = await fetch(
    `${API_BASE_URL}/api/analysis/jobs/${jobId}/cancel`,
    { method: "POST" },
  );

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as JobStatus;
}

/** Queues another attempt at a failed or cancelled job. */
export async function retryJob(jobId: string): Promise<JobStatus> {
  const response = await fetch(
    `${API_BASE_URL}/api/analysis/jobs/${jobId}/retry`,
    { method: "POST" },
  );

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as JobStatus;
}

export async function getEngineStatus(): Promise<EngineStatus> {
  const response = await fetch(`${API_BASE_URL}/api/engine/status`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as EngineStatus;
}
