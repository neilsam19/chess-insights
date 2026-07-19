import type { Game } from "../types/upload";

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
            <span>
              {game.white} vs {game.black}
            </span>
            <span className="game-result">{game.result}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
