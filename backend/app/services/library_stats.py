from app.models.game import Game
from app.services.opening_classifier import OpeningClassification
from app.services.pgn_parser import ParsedGame
from app.services.statistics import UploadStats, compute_stats


def _stored_classification(game: Game) -> OpeningClassification | None:
    """Rebuild the classification from its persisted columns.

    Returns None for rows imported before Sprint 3 (or not yet backfilled),
    which makes `statistics.opening_label` fall back to the stored ECO
    exactly as it did before - so a partially backfilled database still
    aggregates sensibly instead of collapsing to "Unknown".
    """
    if game.opening_source is None:
        return None
    return OpeningClassification(
        eco=game.eco,
        name=game.opening_name,
        matched_ply=game.opening_matched_ply,
        source=game.opening_source,
        dataset_version=game.opening_dataset_version,
    )


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
        classification=_stored_classification(game),
    )


def compute_upload_stats(games: list[Game], player_name: str) -> UploadStats:
    parsed_games = [_game_to_parsed(game) for game in games]
    _, stats = compute_stats(parsed_games, player_name)
    return stats
