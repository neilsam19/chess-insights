import hashlib
import uuid

from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.move import Move
from app.models.upload import Upload
from app.repositories.uploads import create_upload, find_by_file_and_player
from app.services.pgn_parser import ParsedGame, parse_pgn_text
from app.services.statistics import classify_game


class DuplicateUploadError(Exception):
    def __init__(self, existing_upload_id: uuid.UUID) -> None:
        self.existing_upload_id = existing_upload_id
        super().__init__(f"Upload already exists: {existing_upload_id}")


class NoValidGamesError(Exception):
    pass


class InvalidEncodingError(Exception):
    pass


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _game_hash(game: ParsedGame) -> str:
    payload = "|".join([game.white, game.black, game.result, str(len(game.moves)), game.raw_pgn])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def import_pgn(
    db: Session, *, original_filename: str, raw_bytes: bytes, player_name: str
) -> Upload:
    """Parse and persist a PGN upload + its games + moves in one transaction.

    Does not commit - the caller's session (request-scoped, see
    app.core.database.get_db) commits or rolls back at the request boundary,
    so a failure partway through never leaves a half-imported upload.
    """
    file_sha256 = _hash_bytes(raw_bytes)
    normalized_player_name = player_name.strip().lower()

    existing = find_by_file_and_player(db, file_sha256, normalized_player_name)
    if existing is not None:
        raise DuplicateUploadError(existing.id)

    try:
        pgn_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidEncodingError("File is not valid UTF-8 text.") from exc

    parse_result = parse_pgn_text(pgn_text)

    if not parse_result.games:
        raise NoValidGamesError("No valid games could be parsed from the file.")

    upload = create_upload(
        db,
        original_filename=original_filename,
        file_sha256=file_sha256,
        player_name=player_name,
        normalized_player_name=normalized_player_name,
        game_count=len(parse_result.games),
        warnings=parse_result.warnings,
    )

    for index, game in enumerate(parse_result.games):
        classification = classify_game(game, player_name)
        # Classified server-side during parsing - never taken from the client.
        opening = game.classification
        db.add(
            Game(
                upload_id=upload.id,
                game_index=index,
                event=game.event,
                site=game.site,
                round=game.round,
                played_date_text=game.date,
                white_name=game.white,
                black_name=game.black,
                white_elo=game.white_elo,
                black_elo=game.black_elo,
                result=game.result,
                user_color=classification.color,
                user_result=classification.result,
                eco=opening.eco if opening else game.eco,
                opening_name=opening.name if opening else game.opening,
                opening_source=opening.source if opening else None,
                opening_matched_ply=opening.matched_ply if opening else None,
                opening_dataset_version=opening.dataset_version if opening else None,
                move_count=len(game.moves),
                raw_pgn=game.raw_pgn,
                game_hash=_game_hash(game),
                moves=[
                    Move(
                        ply=move.ply,
                        san=move.san,
                        uci=move.uci,
                        fen_before=move.fen_before,
                        fen_after=move.fen_after,
                    )
                    for move in game.moves
                ],
            )
        )

    db.flush()
    return upload
