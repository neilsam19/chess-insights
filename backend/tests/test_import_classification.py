from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.game import Game

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[2] / "sample-data" / "sample-games.pgn"
SAMPLE_PGN_BYTES = SAMPLE_DATA_PATH.read_bytes()

WRONG_HEADERS = """[Event "Casual Game"]
[White "NeilSamant"]
[Black "OpponentA"]
[Result "*"]
[ECO "A00"]
[Opening "Grob Opening"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 *
"""


def _import(client: TestClient, pgn: bytes, player_name: str = "NeilSamant") -> dict:
    response = client.post(
        "/api/uploads",
        files={"file": ("games.pgn", pgn, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return response.json()


def _preview(client: TestClient, pgn: bytes, player_name: str = "NeilSamant") -> dict:
    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", pgn, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 200
    return response.json()


def test_import_persists_canonical_classification(client: TestClient, db_session) -> None:
    upload = _import(client, SAMPLE_PGN_BYTES)

    games = db_session.execute(
        select(Game).where(Game.upload_id == upload["id"]).order_by(Game.game_index)
    ).scalars().all()

    assert [g.opening_source for g in games] == ["lichess"] * 3
    assert games[0].opening_name == "Bishop's Opening"
    assert games[0].opening_matched_ply == 3
    assert games[2].opening_name == "Barnes Opening: Fool's Mate"
    assert all(g.opening_dataset_version for g in games)


def test_import_stores_classified_eco_not_the_header(client: TestClient, db_session) -> None:
    upload = _import(client, SAMPLE_PGN_BYTES)

    qgd = db_session.execute(
        select(Game).where(Game.upload_id == upload["id"], Game.game_index == 1)
    ).scalar_one()

    # PGN header said D30; the position says D53.
    assert qgd.eco == "D53"
    # The original header survives verbatim inside raw_pgn.
    assert '[ECO "D30"]' in qgd.raw_pgn


def test_import_ignores_client_supplied_classification(client: TestClient, db_session) -> None:
    """Classification is computed server-side; extra form fields claiming an
    opening must have no effect."""
    response = client.post(
        "/api/uploads",
        files={"file": ("games.pgn", WRONG_HEADERS.encode("utf-8"), "application/x-chess-pgn")},
        data={
            "player_name": "NeilSamant",
            "opening_name": "Attacker Supplied Opening",
            "eco": "Z99",
            "opening_source": "spoofed",
        },
    )
    assert response.status_code == 201

    game = db_session.execute(
        select(Game).where(Game.upload_id == response.json()["id"])
    ).scalar_one()

    assert game.opening_name == "Sicilian Defense: Najdorf Variation"
    assert game.eco == "B90"
    assert game.opening_source == "lichess"


def test_preview_and_save_produce_identical_classifications(client: TestClient) -> None:
    previewed = _preview(client, SAMPLE_PGN_BYTES)
    upload = _import(client, SAMPLE_PGN_BYTES)

    saved = client.get(f"/api/uploads/{upload['id']}/games").json()["items"]
    saved_by_index = {g["game_index"]: g for g in saved}

    assert len(previewed["games"]) == len(saved)
    for game in previewed["games"]:
        stored = saved_by_index[game["index"]]
        assert stored["eco"] == game["eco"]
        assert stored["opening_name"] == game["opening"]
        assert stored["opening_source"] == game["opening_source"]
        assert stored["opening_matched_ply"] == game["opening_matched_ply"]


def test_saved_stats_use_canonical_opening_names(client: TestClient) -> None:
    upload = _import(client, SAMPLE_PGN_BYTES)

    stats = client.get(f"/api/uploads/{upload['id']}/stats").json()
    names = {opening["name"] for opening in stats["openings"]}

    assert "Barnes Opening: Fool's Mate" in names
    assert "Barnes Opening" not in names


def test_saved_stats_match_preview_stats(client: TestClient) -> None:
    previewed = _preview(client, SAMPLE_PGN_BYTES)["stats"]
    upload = _import(client, SAMPLE_PGN_BYTES)
    saved = client.get(f"/api/uploads/{upload['id']}/stats").json()

    assert {o["name"] for o in saved["openings"]} == {
        o["name"] for o in previewed["openings"]
    }
    assert saved["wins"] == previewed["wins"]
    assert saved["score_percentage"] == previewed["score_percentage"]


def test_game_detail_exposes_classification(client: TestClient) -> None:
    upload = _import(client, SAMPLE_PGN_BYTES)
    summaries = client.get(f"/api/uploads/{upload['id']}/games").json()["items"]

    detail = client.get(f"/api/games/{summaries[0]['id']}").json()

    assert detail["opening_name"] == "Bishop's Opening"
    assert detail["opening_source"] == "lichess"
    assert detail["opening_matched_ply"] == 3
    # Summary and detail must agree - same opening shown in both views.
    assert detail["opening_name"] == summaries[0]["opening_name"]


def test_game_summaries_still_exclude_moves(client: TestClient) -> None:
    upload = _import(client, SAMPLE_PGN_BYTES)

    summaries = client.get(f"/api/uploads/{upload['id']}/games").json()["items"]

    assert "moves" not in summaries[0]
    assert "fen_before" not in summaries[0]
