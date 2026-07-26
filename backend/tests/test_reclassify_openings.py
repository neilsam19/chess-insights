from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.game import Game
from app.models.move import Move
from app.scripts import reclassify_openings
from app.scripts.reclassify_openings import reclassify_all
from app.services.opening_dataset import load_opening_index

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[2] / "sample-data" / "sample-games.pgn"
SAMPLE_PGN_BYTES = SAMPLE_DATA_PATH.read_bytes()


def _import(client: TestClient, player_name: str = "NeilSamant") -> dict:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return response.json()


def _unclassify(db_session, upload_id) -> list[Game]:
    """Return an upload's games to their pre-Sprint-3 state: a bare ECO
    header, no canonical name, no provenance."""
    games = db_session.execute(
        select(Game).where(Game.upload_id == upload_id).order_by(Game.game_index)
    ).scalars().all()
    for game in games:
        game.eco = "Z99"
        game.opening_name = None
        game.opening_source = None
        game.opening_matched_ply = None
        game.opening_dataset_version = None
    # Committed (a savepoint release under the test fixture) so the backfill's
    # own per-upload rollbacks can't undo this setup.
    db_session.commit()
    return games


def test_backfills_existing_games_without_reupload(client: TestClient, db_session) -> None:
    upload = _import(client)
    _unclassify(db_session, upload["id"])

    # Scoped to this test's upload - the dev database holds real analyses too.
    report = reclassify_all(db_session, upload_ids=[upload["id"]])

    assert report.games_updated == 3
    games = db_session.execute(
        select(Game).where(Game.upload_id == upload["id"]).order_by(Game.game_index)
    ).scalars().all()
    assert [g.opening_source for g in games] == ["lichess"] * 3
    assert games[0].opening_name == "Bishop's Opening"
    assert games[2].opening_name == "Barnes Opening: Fool's Mate"
    assert all(g.opening_dataset_version for g in games)


def test_backfill_is_idempotent(client: TestClient, db_session) -> None:
    upload = _import(client)
    _unclassify(db_session, upload["id"])

    scope = [upload["id"]]
    first = reclassify_all(db_session, upload_ids=scope)
    assert first.games_updated == 3

    def snapshot() -> list[tuple]:
        return [
            (g.eco, g.opening_name, g.opening_source, g.opening_matched_ply)
            for g in db_session.execute(
                select(Game).where(Game.upload_id == upload["id"]).order_by(Game.game_index)
            ).scalars()
        ]

    after_first = snapshot()

    second = reclassify_all(db_session, upload_ids=scope)
    third = reclassify_all(db_session, upload_ids=scope, force=True)

    assert second.games_updated == 0
    assert third.games_updated == 0  # force re-runs the work but writes nothing
    assert snapshot() == after_first


def test_backfill_does_not_touch_moves_results_or_players(
    client: TestClient, db_session
) -> None:
    upload = _import(client)
    upload_id = upload["id"]

    def game_snapshot() -> list[tuple]:
        return [
            (g.white_name, g.black_name, g.result, g.user_color, g.user_result, g.move_count)
            for g in db_session.execute(
                select(Game).where(Game.upload_id == upload_id).order_by(Game.game_index)
            ).scalars()
        ]

    game_ids = [
        g.id
        for g in db_session.execute(select(Game).where(Game.upload_id == upload_id)).scalars()
    ]
    moves_before = [
        (m.game_id, m.ply, m.san, m.fen_after)
        for m in db_session.execute(
            select(Move).where(Move.game_id.in_(game_ids)).order_by(Move.game_id, Move.ply)
        ).scalars()
    ]
    games_before = game_snapshot()
    upload_before = client.get(f"/api/uploads/{upload_id}").json()

    _unclassify(db_session, upload_id)
    reclassify_all(db_session, upload_ids=[upload_id])

    assert game_snapshot() == games_before
    moves_after = [
        (m.game_id, m.ply, m.san, m.fen_after)
        for m in db_session.execute(
            select(Move).where(Move.game_id.in_(game_ids)).order_by(Move.game_id, Move.ply)
        ).scalars()
    ]
    assert moves_after == moves_before
    assert client.get(f"/api/uploads/{upload_id}").json() == upload_before


def test_dry_run_reports_without_writing(client: TestClient, db_session) -> None:
    upload = _import(client)
    _unclassify(db_session, upload["id"])

    report = reclassify_all(db_session, upload_ids=[upload["id"]], dry_run=True)

    assert report.games_updated == 3
    # Rolled back - the rows are still unclassified.
    games = db_session.execute(
        select(Game).where(Game.upload_id == upload["id"])
    ).scalars().all()
    assert all(g.opening_source is None for g in games)


def test_failure_in_one_upload_leaves_it_untouched(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _import(client, "PlayerOne")
    second = _import(client, "PlayerTwo")
    _unclassify(db_session, first["id"])
    _unclassify(db_session, second["id"])

    # Blow up only while processing the second upload.
    real_apply = reclassify_openings.apply_classification
    failing_upload_id = second["id"]
    seen: dict = {}

    def flaky(game: Game, index):
        if str(game.upload_id) == str(failing_upload_id):
            seen["hit"] = True
            raise RuntimeError("simulated failure mid-upload")
        return real_apply(game, index)

    monkeypatch.setattr(reclassify_openings, "apply_classification", flaky)

    report = reclassify_all(db_session, upload_ids=[first["id"], second["id"]])

    assert seen.get("hit")
    assert report.uploads_failed == 1
    assert report.uploads_processed == 1

    # The healthy upload was committed...
    ok_games = db_session.execute(
        select(Game).where(Game.upload_id == first["id"])
    ).scalars().all()
    assert all(g.opening_source == "lichess" for g in ok_games)

    # ...and the failing one is entirely unchanged, not half-updated.
    bad_games = db_session.execute(
        select(Game).where(Game.upload_id == failing_upload_id)
    ).scalars().all()
    assert all(g.opening_source is None for g in bad_games)
    assert all(g.opening_name is None for g in bad_games)


def test_report_counts_sources(client: TestClient, db_session) -> None:
    upload = _import(client)
    _unclassify(db_session, upload["id"])

    report = reclassify_all(db_session, upload_ids=[upload["id"]])

    assert report.by_source["lichess"] == 3
    assert report.games_fallback == 0
    assert report.games_unknown == 0
    assert "games updated" in report.render()


def test_only_stale_games_are_reprocessed(client: TestClient, db_session) -> None:
    upload = _import(client)
    index = load_opening_index()

    # Freshly imported games already carry the current dataset version.
    games = db_session.execute(
        select(Game).where(Game.upload_id == upload["id"])
    ).scalars().all()
    assert all(g.opening_dataset_version == index.dataset_version for g in games)

    scope = [upload["id"]]
    report = reclassify_all(db_session, upload_ids=scope)
    assert report.games_updated == 0
    assert report.games_unchanged == 3

    # Simulate an older snapshot -> now considered stale.
    for game in games:
        game.opening_dataset_version = "older-snapshot"
    db_session.commit()

    restale = reclassify_all(db_session, upload_ids=scope)
    assert restale.games_updated == 3
