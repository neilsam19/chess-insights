import { LibraryError } from "./library";
import type { UploadMoveQuality } from "../types/uploadMoveQuality";

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
 * Move quality across a whole upload, plus the recommendations it supports.
 *
 * Read-only and derived on read. Fetched once when the dashboard opens: the
 * figures cannot change until more analysis finishes, and the analysis panel
 * already reports when that happens.
 */
export async function getUploadMoveQuality(
  uploadId: string,
): Promise<UploadMoveQuality> {
  const response = await fetch(
    `${API_BASE_URL}/api/uploads/${uploadId}/move-quality`,
  );

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as UploadMoveQuality;
}
