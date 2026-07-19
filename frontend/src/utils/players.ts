import type { Game } from "../types/upload";

export function getUniquePlayerNames(games: Game[]): string[] {
  const names = new Set<string>();
  for (const game of games) {
    names.add(game.white);
    names.add(game.black);
  }
  return Array.from(names).sort((a, b) => a.localeCompare(b));
}

export function getMostFrequentPlayerName(games: Game[]): string {
  const counts = new Map<string, number>();
  for (const game of games) {
    counts.set(game.white, (counts.get(game.white) ?? 0) + 1);
    counts.set(game.black, (counts.get(game.black) ?? 0) + 1);
  }

  let best = "";
  let bestCount = -1;
  for (const [name, count] of counts) {
    if (count > bestCount) {
      best = name;
      bestCount = count;
    }
  }
  return best;
}
