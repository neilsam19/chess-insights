import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DashboardSummary } from "../components/DashboardSummary";
import { GameList } from "../components/GameList";
import { ChessGameViewer } from "../components/ChessGameViewer";
import { LibraryError, getGame, getUpload, getUploadGames, getUploadStats } from "../api/library";
import { gameFromDetail, gameFromSummary } from "../utils/libraryAdapters";
import type { GameSummary, SavedUpload } from "../types/library";
import type { Game, UploadStats } from "../types/upload";

type Status = "loading" | "not-found" | "error" | "ready";

const PAGE_SIZE = 25;

export function SavedDashboardPage() {
  const { uploadId } = useParams<{ uploadId: string }>();

  const [status, setStatus] = useState<Status>("loading");
  const [upload, setUpload] = useState<SavedUpload | null>(null);
  const [stats, setStats] = useState<UploadStats | null>(null);
  const [gameSummaries, setGameSummaries] = useState<GameSummary[]>([]);
  const [page, setPage] = useState(1);
  const [totalGames, setTotalGames] = useState(0);

  const [selectedGameIndex, setSelectedGameIndex] = useState<number | null>(null);
  const [selectedGame, setSelectedGame] = useState<Game | null>(null);
  const [isLoadingGame, setIsLoadingGame] = useState(false);
  const [gameError, setGameError] = useState<string | null>(null);

  useEffect(() => {
    if (!uploadId) return;
    const currentUploadId = uploadId;
    let cancelled = false;

    async function load() {
      setStatus("loading");
      try {
        const [uploadData, statsData, gamesData] = await Promise.all([
          getUpload(currentUploadId),
          getUploadStats(currentUploadId),
          getUploadGames(currentUploadId, 1, PAGE_SIZE),
        ]);
        if (cancelled) return;

        setUpload(uploadData);
        setStats(statsData);
        setGameSummaries(gamesData.items);
        setTotalGames(gamesData.total);
        setPage(1);
        setStatus("ready");

        if (gamesData.items.length > 0) {
          await loadGameDetail(gamesData.items[0]);
        }
      } catch (err) {
        if (cancelled) return;
        setStatus(err instanceof LibraryError && err.status === 404 ? "not-found" : "error");
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [uploadId]);

  async function loadGameDetail(summary: GameSummary) {
    setSelectedGameIndex(summary.game_index);
    setIsLoadingGame(true);
    setGameError(null);
    try {
      const detail = await getGame(summary.id);
      setSelectedGame(gameFromDetail(detail));
    } catch {
      setGameError("Could not load this game.");
      setSelectedGame(null);
    } finally {
      setIsLoadingGame(false);
    }
  }

  async function loadGamesPage(newPage: number) {
    if (!uploadId) return;
    try {
      const gamesData = await getUploadGames(uploadId, newPage, PAGE_SIZE);
      setGameSummaries(gamesData.items);
      setTotalGames(gamesData.total);
      setPage(newPage);
    } catch {
      setGameError("Could not load that page of games.");
    }
  }

  function handleSelectGame(index: number) {
    const summary = gameSummaries.find((game) => game.game_index === index);
    if (summary) void loadGameDetail(summary);
  }

  if (status === "loading") {
    return (
      <main className="upload-page">
        <p>Loading analysis...</p>
      </main>
    );
  }

  if (status === "not-found") {
    return (
      <main className="upload-page">
        <Link to="/">&larr; Back to home</Link>
        <p className="upload-error" role="alert">
          This analysis could not be found - it may have been deleted.
        </p>
      </main>
    );
  }

  if (status === "error" || !upload || !stats) {
    return (
      <main className="upload-page">
        <Link to="/">&larr; Back to home</Link>
        <p className="upload-error" role="alert">
          Something went wrong loading this analysis. Please try again.
        </p>
      </main>
    );
  }

  const totalPages = Math.max(1, Math.ceil(totalGames / PAGE_SIZE));

  return (
    <main className="upload-page">
      <Link to="/">&larr; Back to home</Link>
      <h1>{upload.original_filename}</h1>
      <p className="upload-history-info">
        <span>{upload.player_name}</span>
        <span>{new Date(upload.uploaded_at).toLocaleDateString()}</span>
        <span>{upload.game_count} games</span>
      </p>

      {upload.warnings.length > 0 && (
        <ul className="upload-warnings">
          {upload.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      <DashboardSummary stats={stats} />

      <div className="games-section">
        <div>
          <GameList
            games={gameSummaries.map(gameFromSummary)}
            selectedIndex={selectedGameIndex}
            onSelect={handleSelectGame}
          />

          {totalPages > 1 && (
            <div className="board-controls">
              <button
                type="button"
                onClick={() => loadGamesPage(page - 1)}
                disabled={page <= 1}
              >
                Previous page
              </button>
              <span>
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => loadGamesPage(page + 1)}
                disabled={page >= totalPages}
              >
                Next page
              </button>
            </div>
          )}
        </div>

        {isLoadingGame && <p>Loading game...</p>}
        {gameError && (
          <p className="upload-error" role="alert">
            {gameError}
          </p>
        )}
        {!isLoadingGame && selectedGame && (
          <ChessGameViewer key={selectedGame.index} game={selectedGame} />
        )}
      </div>
    </main>
  );
}
