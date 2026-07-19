import type { UploadPreviewResponse } from "../types/upload";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8001";

export class UploadError extends Error {}

export async function postUploadPreview(
  file: File,
  playerName: string,
): Promise<UploadPreviewResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("player_name", playerName);

  const response = await fetch(`${API_BASE_URL}/api/uploads/preview`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : `Upload failed with status ${response.status}`;
    throw new UploadError(detail);
  }

  return (await response.json()) as UploadPreviewResponse;
}
