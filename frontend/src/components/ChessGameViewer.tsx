import { useEffect, useMemo, useState } from "react";
import { Chessboard } from "react-chessboard";
import { getGameMoveQuality } from "../api/moveQuality";
import type { GameAnalysis } from "../types/analysis";
import type { GameMoveQuality } from "../types/moveQuality";
import type { Game } from "../types/upload";
import { annotationsByPly } from "../utils/moveQuality";
import { GameAnalysisPanel } from "./GameAnalysisPanel";
import { MoveList } from "./MoveList";
import { MoveQualityPanel } from "./MoveQualityPanel";
import { OpeningLabel } from "./OpeningLabel";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

interface FetchedMoveQuality {
  gameId: string;
  data: GameMoveQuality | null;
  error: string | null;
}

interface ChessGameViewerProps {
  game: Game;
  /**
   * Persisted game id. Supplied only for saved games - preview games have no
   * id, so the analysis section never appears there and preview stays
   * entirely stateless.
   */
  gameId?: string;
}

export function ChessGameViewer({ game, gameId }: ChessGameViewerProps) {
  const [currentPly, setCurrentPly] = useState(0);
  const [orientation, setOrientation] = useState<"white" | "black">(
    game.player_color === "black" ? "black" : "white",
  );
  const [analysisStatus, setAnalysisStatus] = useState<GameAnalysis["status"] | null>(
    null,
  );
  // Tagged with the game it describes, so a result that arrives after the
  // viewer has moved on is ignored rather than shown against another game.
  const [fetched, setFetched] = useState<FetchedMoveQuality | null>(null);

  // Fetched once, and only once analysis is known to be complete. Move quality
  // is derived from evaluations that are already stored, so it cannot change
  // while a job is running - there is nothing here worth polling for.
  useEffect(() => {
    if (!gameId || analysisStatus !== "complete") return;

    let cancelled = false;

    getGameMoveQuality(gameId)
      .then((data) => {
        if (!cancelled) setFetched({ gameId, data, error: null });
      })
      .catch(() => {
        if (!cancelled) {
          setFetched({
            gameId,
            data: null,
            error: "Could not load move quality for this game.",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [gameId, analysisStatus]);

  // Derived rather than cleared in the effect: clearing would be a synchronous
  // setState inside an effect, which cascades renders. A stale result simply
  // stops being current once the game or the analysis state moves on.
  const current =
    fetched !== null && fetched.gameId === gameId && analysisStatus === "complete"
      ? fetched
      : null;
  const moveQuality = current?.data ?? null;

  // Held here rather than in the panel because the move list needs them too,
  // for the same reason currentPly lives here: two children share the state.
  const annotations = useMemo(
    () =>
      moveQuality?.status === "complete"
        ? annotationsByPly(moveQuality.moves)
        : undefined,
    [moveQuality],
  );

  const positions = [
    game.moves[0]?.fen_before ?? START_FEN,
    ...game.moves.map((move) => move.fen_after),
  ];
  const lastPly = game.moves.length;
  const position = positions[currentPly] ?? START_FEN;

  return (
    <div className="game-viewer">
      <div className="game-header">
        <span>{game.white}</span>
        <span>vs</span>
        <span>{game.black}</span>
        <span className="game-result">{game.result}</span>
      </div>
      <OpeningLabel name={game.opening} eco={game.eco} className="game-header-opening" />

      <div className="board-and-moves">
        <div className="board-wrapper">
          <Chessboard
            options={{
              position,
              allowDragging: false,
              boardOrientation: orientation,
            }}
          />
        </div>
        <MoveList
          moves={game.moves}
          currentPly={currentPly}
          onSelectPly={setCurrentPly}
          annotations={annotations}
        />
      </div>

      <div className="board-controls">
        <button type="button" onClick={() => setCurrentPly(0)} disabled={currentPly === 0}>
          Start
        </button>
        <button
          type="button"
          onClick={() => setCurrentPly((ply) => Math.max(0, ply - 1))}
          disabled={currentPly === 0}
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => setCurrentPly((ply) => Math.min(lastPly, ply + 1))}
          disabled={currentPly === lastPly}
        >
          Next
        </button>
        <button
          type="button"
          onClick={() => setCurrentPly(lastPly)}
          disabled={currentPly === lastPly}
        >
          End
        </button>
        <button
          type="button"
          onClick={() => setOrientation((current) => (current === "white" ? "black" : "white"))}
        >
          Flip board
        </button>
      </div>

      {/* Rendered inside the viewer so the graph shares currentPly directly,
          rather than lifting board state into every page that shows a game. */}
      {gameId && (
        <GameAnalysisPanel
          key={gameId}
          gameId={gameId}
          currentPly={currentPly}
          onSelectPly={setCurrentPly}
          onStatusChange={setAnalysisStatus}
        />
      )}

      {gameId && (
        <MoveQualityPanel
          moveQuality={moveQuality}
          currentPly={currentPly}
          onSelectPly={setCurrentPly}
          error={current?.error ?? null}
        />
      )}
    </div>
  );
}
