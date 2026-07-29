import { LibraryError } from "./library";
import type { EngineStatus, GameAnalysis } from "../types/analysis";

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
 * Requests analysis of one saved game. Sends no body at all - positions,
 * engine identity, and depth are entirely server-side.
 *
 * Synchronous for Sprint 4: this request stays open for the duration of the
 * engine run.
 */
export async function analyzeGame(gameId: string): Promise<GameAnalysis> {
  const response = await fetch(`${API_BASE_URL}/api/games/${gameId}/analysis`, {
    method: "POST",
  });

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as GameAnalysis;
}

export async function getEngineStatus(): Promise<EngineStatus> {
  const response = await fetch(`${API_BASE_URL}/api/engine/status`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as EngineStatus;
}
