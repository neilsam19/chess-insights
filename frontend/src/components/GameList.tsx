import type { Game } from "../types/upload";
import { OpeningLabel } from "./OpeningLabel";

interface GameListProps {
  games: Game[];
  selectedIndex: number | null;
  onSelect: (index: number) => void;
}

export function GameList({ games, selectedIndex, onSelect }: GameListProps) {
  return (
    <ul className="game-list">
      {games.map((game) => (
        <li key={game.index}>
          <button
            type="button"
            className={
              game.index === selectedIndex ? "game-list-item selected" : "game-list-item"
            }
            onClick={() => onSelect(game.index)}
          >
            <span className="game-list-players">
              {game.white} vs {game.black}
            </span>
            <OpeningLabel name={game.opening} eco={game.eco} />
            <span className="game-result">{game.result}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
