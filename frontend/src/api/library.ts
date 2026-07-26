import type {
  GameDetail,
  PaginatedGameSummaries,
  PaginatedUploads,
  SavedUpload,
} from "../types/library";
import type { UploadStats } from "../types/upload";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8001";

export class LibraryError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export class DuplicateUploadError extends LibraryError {
  readonly existingUploadId: string;

  constructor(message: string, status: number, existingUploadId: string) {
    super(message, status);
    this.existingUploadId = existingUploadId;
  }
}

async function throwForResponse(response: Response): Promise<never> {
  const body: unknown = await response.json().catch(() => null);

  if (response.status === 409 && body && typeof body === "object" && "detail" in body) {
    const detail = body.detail;
    if (detail && typeof detail === "object" && "existing_upload_id" in detail) {
      const message =
        "message" in detail && typeof detail.message === "string"
          ? detail.message
          : "This file has already been imported for this player.";
      throw new DuplicateUploadError(
        message,
        response.status,
        String(detail.existing_upload_id),
      );
    }
  }

  const detail =
    body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
      ? body.detail
      : `Request failed with status ${response.status}`;
  throw new LibraryError(detail, response.status);
}

export async function saveUpload(file: File, playerName: string): Promise<SavedUpload> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("player_name", playerName);

  const response = await fetch(`${API_BASE_URL}/api/uploads`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as SavedUpload;
}

export async function listUploads(page = 1, pageSize = 25): Promise<PaginatedUploads> {
  const response = await fetch(`${API_BASE_URL}/api/uploads?page=${page}&page_size=${pageSize}`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as PaginatedUploads;
}

export async function getUpload(uploadId: string): Promise<SavedUpload> {
  const response = await fetch(`${API_BASE_URL}/api/uploads/${uploadId}`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as SavedUpload;
}

export async function getUploadStats(uploadId: string): Promise<UploadStats> {
  const response = await fetch(`${API_BASE_URL}/api/uploads/${uploadId}/stats`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as UploadStats;
}

export async function getUploadGames(
  uploadId: string,
  page = 1,
  pageSize = 25,
): Promise<PaginatedGameSummaries> {
  const response = await fetch(
    `${API_BASE_URL}/api/uploads/${uploadId}/games?page=${page}&page_size=${pageSize}`,
  );

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as PaginatedGameSummaries;
}

export async function getGame(gameId: string): Promise<GameDetail> {
  const response = await fetch(`${API_BASE_URL}/api/games/${gameId}`);

  if (!response.ok) {
    return throwForResponse(response);
  }

  return (await response.json()) as GameDetail;
}

export async function deleteUpload(uploadId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/uploads/${uploadId}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    await throwForResponse(response);
  }
}
