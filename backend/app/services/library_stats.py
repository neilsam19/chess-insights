from app.models.game import Game
from app.services.pgn_parser import ParsedGame
from app.services.statistics import UploadStats, compute_stats


def _game_to_parsed(game: Game) -> ParsedGame:
    """Adapts a persisted Game row back into the ParsedGame shape so the
    existing, already-tested statistics.compute_stats can be reused as-is
    instead of duplicating aggregation logic for DB-backed uploads."""
    return ParsedGame(
        white=game.white_name,
        black=game.black_name,
        white_elo=game.white_elo,
        black_elo=game.black_elo,
        date=game.played_date_text,
        event=game.event,
        site=game.site,
        round=game.round,
        result=game.result,
        eco=game.eco,
        opening=game.opening_name,
        moves=[],
        raw_pgn=game.raw_pgn,
    )


def compute_upload_stats(games: list[Game], player_name: str) -> UploadStats:
    parsed_games = [_game_to_parsed(game) for game in games]
    _, stats = compute_stats(parsed_games, player_name)
    return stats
