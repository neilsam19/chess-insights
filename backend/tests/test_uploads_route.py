from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import uploads as uploads_module
from app.main import app

client = TestClient(app)

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[2] / "sample-data" / "sample-games.pgn"

VALID_PGN = """[Event "Casual Game"]
[White "NeilSamant"]
[Black "OpponentA"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""


def test_valid_upload_returns_expected_shape() -> None:
    pgn_bytes = SAMPLE_DATA_PATH.read_bytes()

    response = client.post(
        "/api/uploads/preview",
        files={"file": ("sample-games.pgn", pgn_bytes, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )

    assert response.status_code == 200
    body = response.json()

    assert body["warnings"] == []
    assert len(body["games"]) == 3

    stats = body["stats"]
    assert stats["total_games"] == 3
    assert stats["wins"] == 1
    assert stats["draws"] == 1
    assert stats["losses"] == 1
    assert stats["games_as_white"] == 2
    assert stats["games_as_black"] == 1

    first_game = body["games"][0]
    assert first_game["player_color"] == "white"
    assert first_game["player_result"] == "win"
    assert first_game["moves"][0]["san"] == "e4"


def test_missing_file_is_rejected() -> None:
    response = client.post("/api/uploads/preview", data={"player_name": "NeilSamant"})

    assert response.status_code == 422  # FastAPI's own required-field validation


def test_omitted_player_name_returns_unknown_classification_for_all_games() -> None:
    # Supports the "pick your name from the file" frontend flow: the games list
    # (and thus the set of player names to choose from) is fetched before the
    # user has told us which name is theirs.
    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", VALID_PGN.encode("utf-8"), "application/x-chess-pgn")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["games"][0]["player_color"] == "unknown"
    assert body["games"][0]["player_result"] == "unknown"
    assert body["stats"]["unknown_results"] == 1


def test_empty_file_is_rejected() -> None:
    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", b"", "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_oversized_file_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploads_module.settings, "max_upload_size_bytes", 10)

    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", VALID_PGN.encode("utf-8"), "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )

    assert response.status_code == 413


def test_unparseable_file_returns_422() -> None:
    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", b"this is not a pgn file at all", "text/plain")},
        data={"player_name": "NeilSamant"},
    )

    assert response.status_code == 422


def test_valid_upload_with_unknown_player_name() -> None:
    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", VALID_PGN.encode("utf-8"), "application/x-chess-pgn")},
        data={"player_name": "SomeoneNotInTheFile"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["games"][0]["player_color"] == "unknown"
    assert body["games"][0]["player_result"] == "unknown"
    assert body["stats"]["unknown_results"] == 1
