import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { DashboardSummary } from "../components/DashboardSummary";
import { GameList } from "../components/GameList";
import { ChessGameViewer } from "../components/ChessGameViewer";
import { TrainingInsights } from "../components/TrainingInsights";
import { UploadAnalysisPanel } from "../components/UploadAnalysisPanel";
import { LibraryError, getGame, getUpload, getUploadGames, getUploadStats } from "../api/library";
import { getUploadMoveQuality } from "../api/uploadMoveQuality";
import { gameFromDetail, gameFromSummary } from "../utils/libraryAdapters";
import type { GameSummary, SavedUpload } from "../types/library";
import type { Game, UploadStats } from "../types/upload";
import type { UploadMoveQuality } from "../types/uploadMoveQuality";

type Status = "loading" | "not-found" | "error" | "ready";

const PAGE_SIZE = 25;

/**
 * Deep-link parameters. Held in the URL rather than in component state so a
 * recommendation can be shared, bookmarked, and survive a reload - and so
 * resolving one never depends on which page of games happens to be loaded.
 */
const GAME_PARAM = "game";
const PLY_PARAM = "ply";

export function SavedDashboardPage() {
  const { uploadId } = useParams<{ uploadId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const linkedGameId = searchParams.get(GAME_PARAM);
  const linkedPly = Number.parseInt(searchParams.get(PLY_PARAM) ?? "", 10);

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

  const [defaultGameId, setDefaultGameId] = useState<string | null>(null);

  const [insights, setInsights] = useState<UploadMoveQuality | null>(null);
  const [insightsError, setInsightsError] = useState<string | null>(null);
  // Bumped when a batch finishes, so the figures follow newly analysed games
  // instead of sitting at whatever was true when the page opened.
  const [insightsRefresh, setInsightsRefresh] = useState(0);

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
        // Which game opens is decided by the effect below, from the URL and
        // this default. Selecting one here as well would race it.
        setDefaultGameId(gamesData.items[0]?.id ?? null);
        setStatus("ready");
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

  /**
   * Loads whichever game the URL names, falling back to the upload's first.
   *
   * Fetched by id rather than looked up in `gameSummaries`, which holds one
   * page of 25: a link to a game on page three would otherwise resolve to
   * nothing at all.
   */
  useEffect(() => {
    const gameId = linkedGameId ?? defaultGameId;
    if (!gameId) return;

    let cancelled = false;

    async function open() {
      setIsLoadingGame(true);
      setGameError(null);
      try {
        const detail = await getGame(gameId as string);
        if (cancelled) return;
        setSelectedGame(gameFromDetail(detail));
        setSelectedGameIndex(detail.game_index);
      } catch {
        if (cancelled) return;
        setGameError("Could not load this game.");
        setSelectedGame(null);
      } finally {
        if (!cancelled) setIsLoadingGame(false);
      }
    }

    void open();

    return () => {
      cancelled = true;
    };
  }, [linkedGameId, defaultGameId]);

  useEffect(() => {
    if (!uploadId || status !== "ready") return;
    let cancelled = false;

    async function load() {
      try {
        const result = await getUploadMoveQuality(uploadId as string);
        if (!cancelled) {
          setInsights(result);
          setInsightsError(null);
        }
      } catch {
        // Distinct from "nothing analysed yet", which is a property of the
        // library rather than of this request.
        if (!cancelled) setInsightsError("Could not load your training insights.");
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [uploadId, status, insightsRefresh]);

  /** Opens a position, through the URL, so the view can be shared or reloaded. */
  const openPosition = useCallback(
    (gameId: string, ply = 0) => {
      const next = new URLSearchParams(searchParams);
      next.set(GAME_PARAM, gameId);
      if (ply > 0) {
        next.set(PLY_PARAM, String(ply));
      } else {
        next.delete(PLY_PARAM);
      }
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );

  const refreshInsights = useCallback(() => setInsightsRefresh((n) => n + 1), []);

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
    if (summary) openPosition(summary.id);
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

      <UploadAnalysisPanel
        uploadId={upload.id}
        totalGames={upload.game_count}
        onBatchSettled={refreshInsights}
      />

      <TrainingInsights
        insights={insights}
        error={insightsError}
        onOpenPosition={openPosition}
      />

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
          // Keyed on the ply as well as the game: the viewer reads
          // `initialPly` once and owns ply thereafter, so jumping to a second
          // position in the same game has to remount to take effect.
          <ChessGameViewer
            key={`${selectedGame.id ?? selectedGame.index}:${linkedPly || 0}`}
            game={selectedGame}
            gameId={selectedGame.id}
            initialPly={Number.isFinite(linkedPly) ? linkedPly : 0}
          />
        )}
      </div>
    </main>
  );
}
