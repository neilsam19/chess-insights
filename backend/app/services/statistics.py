from dataclasses import dataclass, field

from app.services.pgn_parser import ParsedGame


@dataclass
class GameClassification:
    color: str  # "white" | "black" | "unknown"
    result: str  # "win" | "draw" | "loss" | "unknown"


@dataclass
class OpeningStat:
    name: str
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def score_percentage(self) -> float:
        known = self.wins + self.draws + self.losses
        if known == 0:
            return 0.0
        return round((self.wins + 0.5 * self.draws) / known * 100, 1)


@dataclass
class UploadStats:
    total_games: int
    wins: int
    draws: int
    losses: int
    unknown_results: int
    win_percentage: float
    score_percentage: float
    games_as_white: int
    games_as_black: int
    openings: list[OpeningStat] = field(default_factory=list)


def _normalize(name: str) -> str:
    return name.strip().lower()


def classify_game(game: ParsedGame, player_name: str) -> GameClassification:
    normalized_player = _normalize(player_name)

    if _normalize(game.white) == normalized_player:
        color = "white"
    elif _normalize(game.black) == normalized_player:
        color = "black"
    else:
        color = "unknown"

    if color == "unknown":
        return GameClassification(color=color, result="unknown")

    if game.result == "1/2-1/2":
        result = "draw"
    elif game.result == "1-0":
        result = "win" if color == "white" else "loss"
    elif game.result == "0-1":
        result = "win" if color == "black" else "loss"
    else:
        result = "unknown"

    return GameClassification(color=color, result=result)


def compute_stats(
    games: list[ParsedGame], player_name: str
) -> tuple[list[GameClassification], UploadStats]:
    classifications = [classify_game(game, player_name) for game in games]

    wins = sum(1 for c in classifications if c.result == "win")
    draws = sum(1 for c in classifications if c.result == "draw")
    losses = sum(1 for c in classifications if c.result == "loss")
    unknown = sum(1 for c in classifications if c.result == "unknown")

    known = wins + draws + losses
    win_percentage = round(wins / known * 100, 1) if known else 0.0
    score_percentage = round((wins + 0.5 * draws) / known * 100, 1) if known else 0.0

    games_as_white = sum(1 for c in classifications if c.color == "white")
    games_as_black = sum(1 for c in classifications if c.color == "black")

    stats = UploadStats(
        total_games=len(games),
        wins=wins,
        draws=draws,
        losses=losses,
        unknown_results=unknown,
        win_percentage=win_percentage,
        score_percentage=score_percentage,
        games_as_white=games_as_white,
        games_as_black=games_as_black,
        openings=_aggregate_openings(games, classifications),
    )
    return classifications, stats


def opening_label(game: ParsedGame) -> str:
    """The name an opening is grouped and displayed under.

    Canonical dataset classification wins; the PGN's own header is only a
    fallback, so an upload whose headers are wrong or missing still
    aggregates under real opening names.
    """
    opening = game.classification
    if opening is not None and opening.name:
        return opening.name
    if opening is not None and opening.eco:
        return opening.eco
    return game.opening or game.eco or "Unknown"


def _aggregate_openings(
    games: list[ParsedGame], classifications: list[GameClassification]
) -> list[OpeningStat]:
    by_name: dict[str, OpeningStat] = {}

    for game, classification in zip(games, classifications):
        name = opening_label(game)
        stat = by_name.setdefault(name, OpeningStat(name=name))
        stat.games += 1
        if classification.result == "win":
            stat.wins += 1
        elif classification.result == "draw":
            stat.draws += 1
        elif classification.result == "loss":
            stat.losses += 1

    return sorted(by_name.values(), key=lambda s: s.games, reverse=True)
