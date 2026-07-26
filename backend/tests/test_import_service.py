import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.game import Game
from app.models.move import Move
from app.models.upload import Upload
from app.services import import_service
from app.services.import_service import DuplicateUploadError, import_pgn

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[2] / "sample-data" / "sample-games.pgn"
SAMPLE_PGN_BYTES = SAMPLE_DATA_PATH.read_bytes()


def test_import_persists_upload_games_and_moves(client: TestClient, db_session) -> None:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["game_count"] == 3
    upload_id = body["id"]

    games = db_session.execute(
        select(Game).where(Game.upload_id == upload_id).order_by(Game.game_index)
    ).scalars().all()
    assert len(games) == 3
    assert [g.user_result for g in games] == ["win", "draw", "loss"]

    move_count = db_session.scalar(
        select(func.count()).select_from(Move).where(Move.game_id == games[0].id)
    )
    assert move_count == 7


def test_moves_are_stored_in_ply_order(client: TestClient, db_session) -> None:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    upload_id = response.json()["id"]

    first_game = db_session.execute(
        select(Game).where(Game.upload_id == upload_id, Game.game_index == 0)
    ).scalar_one()

    moves = db_session.execute(
        select(Move).where(Move.game_id == first_game.id).order_by(Move.ply)
    ).scalars().all()

    assert [m.ply for m in moves] == list(range(1, len(moves) + 1))
    assert [m.san for m in moves] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]


def test_duplicate_upload_returns_409_with_existing_id(client: TestClient) -> None:
    first = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    existing_id = first.json()["id"]

    second = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )

    assert second.status_code == 409
    assert second.json()["detail"]["existing_upload_id"] == existing_id


def test_same_file_different_player_succeeds(client: TestClient) -> None:
    first = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    second = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "OpponentA"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_blank_player_name_is_rejected(client: TestClient) -> None:
    # A single space, not "": httpx's multipart encoder drops empty-string
    # fields entirely, which would instead trip FastAPI's own required-field
    # 422 rather than exercising our server-side blank/whitespace check.
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": " "},
    )

    assert response.status_code == 400


def test_multiple_uploads_are_independent(client: TestClient, db_session) -> None:
    first = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    second = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "OpponentA"},
    )

    # Scoped to the ids this test created - a dev database with previously
    # saved analyses in it must not change the outcome.
    upload_ids = [first.json()["id"], second.json()["id"]]
    assert len(set(upload_ids)) == 2

    for upload_id in upload_ids:
        assert db_session.get(Upload, upload_id) is not None
        game_count = db_session.scalar(
            select(func.count()).select_from(Game).where(Game.upload_id == upload_id)
        )
        assert game_count == 3


def test_rollback_on_persistence_failure_leaves_no_partial_import(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure partway through import")

    monkeypatch.setattr(import_service, "classify_game", _boom)

    with pytest.raises(RuntimeError):
        import_pgn(
            db_session,
            original_filename="sample-games.pgn",
            raw_bytes=SAMPLE_PGN_BYTES,
            player_name="NeilSamant",
        )

    db_session.rollback()

    # Scoped to this file+player rather than a global row count, so pre-existing
    # rows in the dev database can't mask (or fake) a clean rollback.
    file_sha256 = hashlib.sha256(SAMPLE_PGN_BYTES).hexdigest()
    orphaned = db_session.execute(
        select(Upload).where(
            Upload.file_sha256 == file_sha256,
            Upload.normalized_player_name == "neilsamant",
        )
    ).scalars().all()
    assert orphaned == []


def test_duplicate_detected_at_service_level(db_session) -> None:
    import_pgn(
        db_session,
        original_filename="sample-games.pgn",
        raw_bytes=SAMPLE_PGN_BYTES,
        player_name="NeilSamant",
    )
    db_session.flush()

    with pytest.raises(DuplicateUploadError):
        import_pgn(
            db_session,
            original_filename="sample-games.pgn",
            raw_bytes=SAMPLE_PGN_BYTES,
            player_name="NeilSamant",
        )
