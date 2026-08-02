import { LibraryError } from "./library";
import type { GameMoveQuality } from "../types/moveQuality";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8001";

async function throwForResponse(response: Response): Promise<never> {
  const body: unknown = await response.json().catch(() => null);

  const detail =
    body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
      ? body.detail
      : `Request failed with status ${response.status}`;

  throw new LibraryError(detail, response.status);
}

/**
 * How well the saved player played, derived from stored engine evaluations.
 *
 * Read-only, and deliberately not folded into `getGameAnalysis`: that endpoint
 * is polled every 1.5 s while a job runs, and this answer cannot change until
 * the job finishes. Call it once, when analysis is known to be complete.
 *
 * The non-complete states are returned in the same shapes the analysis
 * endpoint uses, so a caller can render them without a second vocabulary.
 */
export async function getGameMoveQuality(gameId: string): Promise<GameMoveQuality> {
  const response = await fetch(`${API_BASE_URL}/api/games/${gameId}/move-quality`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as GameMoveQuality;
}
