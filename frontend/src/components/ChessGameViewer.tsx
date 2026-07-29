import { useState } from "react";
import { Chessboard } from "react-chessboard";
import type { Game } from "../types/upload";
import { GameAnalysisPanel } from "./GameAnalysisPanel";
import { MoveList } from "./MoveList";
import { OpeningLabel } from "./OpeningLabel";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

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
        <MoveList moves={game.moves} currentPly={currentPly} onSelectPly={setCurrentPly} />
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
        />
      )}
    </div>
  );
}
