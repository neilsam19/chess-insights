import type { Move } from "../types/upload";

interface MoveListProps {
  moves: Move[];
  currentPly: number;
  onSelectPly: (ply: number) => void;
}

export function MoveList({ moves, currentPly, onSelectPly }: MoveListProps) {
  return (
    <ol className="move-list">
      {moves.map((move) => (
        <li key={move.ply}>
          <button
            type="button"
            className={move.ply === currentPly ? "move selected" : "move"}
            onClick={() => onSelectPly(move.ply)}
          >
            {move.ply % 2 === 1 ? `${Math.ceil(move.ply / 2)}. ` : ""}
            {move.san}
          </button>
        </li>
      ))}
    </ol>
  );
}
