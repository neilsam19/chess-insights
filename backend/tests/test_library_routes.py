import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.game import Game
from app.models.move import Move
from app.models.upload import Upload

from sample_data import SAMPLE_PGN_BYTES


def _import(client: TestClient, player_name: str) -> dict:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return response.json()


def test_list_uploads_is_paginated(client: TestClient) -> None:
    # Baseline first: a dev database may already hold previously saved
    # analyses, so this test asserts on the delta it causes, not on absolutes.
    baseline_total = client.get("/api/uploads", params={"page_size": 1}).json()["total"]

    new_ids = {
        _import(client, "PlayerOne")["id"],
        _import(client, "PlayerTwo")["id"],
        _import(client, "PlayerThree")["id"],
    }
    assert len(new_ids) == 3

    page1 = client.get("/api/uploads", params={"page": 1, "page_size": 2})
    page2 = client.get("/api/uploads", params={"page": 2, "page_size": 2})

    assert page1.status_code == 200
    assert page2.status_code == 200
    assert page1.json()["total"] == baseline_total + 3
    assert page2.json()["total"] == baseline_total + 3

    # Full page, then the remainder - and the list is newest-first, so the three
    # just-imported uploads are the first three rows across those two pages.
    assert len(page1.json()["items"]) == 2
    assert len(page2.json()["items"]) == min(2, baseline_total + 1)

    paged_ids = [item["id"] for item in page1.json()["items"] + page2.json()["items"]]
    assert len(paged_ids) == len(set(paged_ids))  # no row repeats across pages
    assert set(paged_ids[:3]) == new_ids


def test_get_upload_returns_metadata(client: TestClient) -> None:
    upload = _import(client, "NeilSamant")

    response = client.get(f"/api/uploads/{upload['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == upload["id"]
    assert body["player_name"] == "NeilSamant"
    assert body["game_count"] == 3


def test_get_missing_upload_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/uploads/{uuid.uuid4()}")

    assert response.status_code == 404


def test_get_upload_stats_matches_v1_calculation(client: TestClient) -> None:
    upload = _import(client, "NeilSamant")

    response = client.get(f"/api/uploads/{upload['id']}/stats")

    assert response.status_code == 200
    stats = response.json()
    assert stats["total_games"] == 3
    assert stats["wins"] == 1
    assert stats["draws"] == 1
    assert stats["losses"] == 1
    assert stats["win_percentage"] == 33.3
    assert stats["score_percentage"] == 50.0
    assert stats["games_as_white"] == 2
    assert stats["games_as_black"] == 1
    assert len(stats["openings"]) == 3


def test_stats_for_missing_upload_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/uploads/{uuid.uuid4()}/stats")

    assert response.status_code == 404


def test_upload_games_are_paginated_and_summary_only(client: TestClient) -> None:
    upload = _import(client, "NeilSamant")

    response = client.get(
        f"/api/uploads/{upload['id']}/games", params={"page": 1, "page_size": 2}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert "moves" not in body["items"][0]


def test_get_game_returns_full_moves_in_order(client: TestClient) -> None:
    upload = _import(client, "NeilSamant")
    games_page = client.get(f"/api/uploads/{upload['id']}/games").json()
    first_game_id = games_page["items"][0]["id"]

    response = client.get(f"/api/games/{first_game_id}")

    assert response.status_code == 200
    body = response.json()
    assert [m["ply"] for m in body["moves"]] == list(range(1, len(body["moves"]) + 1))
    assert body["moves"][0]["san"] == "e4"


def test_get_missing_game_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/games/{uuid.uuid4()}")

    assert response.status_code == 404


def test_delete_upload_cascades_to_games_and_moves(client: TestClient, db_session) -> None:
    upload = _import(client, "NeilSamant")
    upload_id = upload["id"]

    game_ids = [
        row
        for row in db_session.execute(
            select(Game.id).where(Game.upload_id == upload_id)
        ).scalars()
    ]
    assert len(game_ids) == 3

    moves_before = db_session.scalar(
        select(func.count()).select_from(Move).where(Move.game_id.in_(game_ids))
    )
    assert moves_before > 0

    response = client.delete(f"/api/uploads/{upload_id}")
    assert response.status_code == 204

    assert db_session.get(Upload, upload_id) is None
    games_after = db_session.scalar(
        select(func.count()).select_from(Game).where(Game.upload_id == upload_id)
    )
    assert games_after == 0
    # Scoped to this upload's own moves - unrelated rows already in the dev
    # database must survive, and must not mask a failed cascade either.
    moves_after = db_session.scalar(
        select(func.count()).select_from(Move).where(Move.game_id.in_(game_ids))
    )
    assert moves_after == 0

    # Both the upload and its (now-deleted) games should read back as missing.
    assert client.get(f"/api/uploads/{upload_id}").status_code == 404


def test_delete_missing_upload_returns_404(client: TestClient) -> None:
    response = client.delete(f"/api/uploads/{uuid.uuid4()}")

    assert response.status_code == 404
