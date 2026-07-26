import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { deleteUpload, listUploads } from "../api/library";
import type { UploadListItem } from "../types/library";

export function UploadHistoryList() {
  const [items, setItems] = useState<UploadListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    listUploads()
      .then((response) => {
        if (!cancelled) setItems(response.items);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load recent analyses.");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  async function handleDelete(id: string) {
    setDeletingId(id);
    try {
      await deleteUpload(id);
      setItems((current) => current?.filter((item) => item.id !== id) ?? null);
    } catch {
      setError("Could not delete this analysis.");
    } finally {
      setDeletingId(null);
      setConfirmingId(null);
    }
  }

  return (
    <section className="upload-history">
      <h2>Recent Analyses</h2>

      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {items === null && !error && <p>Loading...</p>}
      {items !== null && items.length === 0 && <p>No saved analyses yet.</p>}

      {items !== null && items.length > 0 && (
        <ul className="upload-history-list">
          {items.map((item) => (
            <li key={item.id} className="upload-history-item">
              <div className="upload-history-info">
                <span className="upload-history-filename">{item.original_filename}</span>
                <span>{item.player_name}</span>
                <span>{new Date(item.uploaded_at).toLocaleDateString()}</span>
                <span>{item.game_count} games</span>
              </div>
              <div className="upload-history-actions">
                <button type="button" onClick={() => navigate(`/uploads/${item.id}`)}>
                  Open
                </button>
                {confirmingId === item.id ? (
                  <>
                    <button
                      type="button"
                      onClick={() => handleDelete(item.id)}
                      disabled={deletingId === item.id}
                    >
                      {deletingId === item.id ? "Deleting..." : "Confirm delete"}
                    </button>
                    <button type="button" onClick={() => setConfirmingId(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button type="button" onClick={() => setConfirmingId(item.id)}>
                    Delete
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
