import type { MoveAnnotation } from "../types/moveQuality";
import type { Move } from "../types/upload";

interface MoveListProps {
  moves: Move[];
  currentPly: number;
  onSelectPly: (ply: number) => void;
  /**
   * Optional, keyed by ply. Absent on the preview path, which has no saved
   * game and so no analysis to grade. Absent entries are the normal case even
   * for a graded game - only mistakes are marked.
   */
  annotations?: Map<number, MoveAnnotation>;
}

export function MoveList({
  moves,
  currentPly,
  onSelectPly,
  annotations,
}: MoveListProps) {
  return (
    <ol className="move-list">
      {moves.map((move) => {
        const annotation = annotations?.get(move.ply);

        return (
          <li key={move.ply}>
            <button
              type="button"
              className={move.ply === currentPly ? "move selected" : "move"}
              onClick={() => onSelectPly(move.ply)}
            >
              {move.ply % 2 === 1 ? `${Math.ceil(move.ply / 2)}. ` : ""}
              {move.san}
              {annotation && (
                // The symbol is decoration; the label is the actual statement,
                // so it lands in the button's accessible name and colour is
                // never the only thing carrying the meaning.
                <span
                  className={`move-annotation move-annotation-${annotation.tone}`}
                  title={annotation.label}
                >
                  <span aria-hidden="true">{annotation.symbol}</span>
                  {/* Leading comma, not a space: accessible-name computation
                      trims each element's text before joining, so a space
                      here would vanish and read as "Qh5Blunder". */}
                  <span className="visually-hidden">{`, ${annotation.label}`}</span>
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
