import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.dependencies import get_engine_factory, get_session_factory
from app.main import app
from app.models.game import Game
from app.repositories import analysis as analysis_repo
from app.services.engine import EngineUnavailableError
from app.services.game_analysis_service import analyze_game
from fake_engine import FakeEngine, cp, mate
from sample_data import SAMPLE_PGN_BYTES


@pytest.fixture
def engine_holder():
    """Mutable holder so a test can swap the engine between requests."""
    return {"engine": FakeEngine()}


@pytest.fixture
def analysis_client(client: TestClient, db_session, engine_holder):
    """TestClient whose analysis dependencies are overridden.

    Without this the route would build real `SessionLocal` sessions and try to
    launch Stockfish - writing rows outside the test transaction and failing
    on machines with no engine installed.
    """

    @contextmanager
    def _session_factory():
        yield db_session

    app.dependency_overrides[get_session_factory] = lambda: _session_factory
    app.dependency_overrides[get_engine_factory] = lambda: (lambda: engine_holder["engine"])
    yield client
    app.dependency_overrides.pop(get_session_factory, None)
    app.dependency_overrides.pop(get_engine_factory, None)


def _saved_game(client: TestClient, db_session, player_name: str = "NeilSamant") -> Game:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return db_session.execute(
        select(Game).where(Game.upload_id == response.json()["id"], Game.game_index == 0)
    ).scalar_one()


class TestGetAnalysis:
    def test_missing_game_returns_404(self, analysis_client: TestClient) -> None:
        response = analysis_client.get(f"/api/games/{uuid.uuid4()}/analysis")

        assert response.status_code == 404

    def test_unanalyzed_game_reports_not_analyzed(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "not_analyzed"
        assert body["game_id"] == str(game.id)
        assert "evaluations" not in body

    def test_running_run_reports_running(self, analysis_client: TestClient, db_session) -> None:
        game = _saved_game(analysis_client, db_session)
        run = analysis_repo.create_analysis_run(
            db_session, game_id=game.id, identity=FakeEngine().identify(), requested_depth=12
        )
        db_session.commit()

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "running"
        assert body["run_id"] == str(run.id)
        assert body["started_at"]

    def test_failed_run_reports_sanitized_error(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        run = analysis_repo.create_analysis_run(
            db_session, game_id=game.id, identity=FakeEngine().identify(), requested_depth=12
        )
        analysis_repo.mark_analysis_run_failed(
            db_session, run, "Stockfish exited unexpectedly."
        )
        db_session.commit()

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "failed"
        assert body["error_message"] == "Stockfish exited unexpectedly."
        assert "Traceback" not in body["error_message"]


class TestPostAnalysis:
    def test_analyzes_a_saved_game(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "complete"
        assert len(body["evaluations"]) == game.move_count + 1
        assert [e["ply"] for e in body["evaluations"]] == list(range(game.move_count + 1))
        assert body["engine"]["name"] == "FakeFish"
        assert body["engine"]["requested_depth"] == 12
        assert body["perspective"] == "white"

    def test_missing_game_returns_404(self, analysis_client: TestClient) -> None:
        response = analysis_client.post(f"/api/games/{uuid.uuid4()}/analysis")

        assert response.status_code == 404

    def test_conflict_when_already_running(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        run = analysis_repo.create_analysis_run(
            db_session, game_id=game.id, identity=FakeEngine().identify(), requested_depth=12
        )
        db_session.commit()

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 409
        assert response.json()["detail"]["run_id"] == str(run.id)
        assert engine_holder["engine"].call_count == 0

    def test_completed_game_returns_existing_run_with_200(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        first = analysis_client.post(f"/api/games/{game.id}/analysis")
        assert first.status_code == 201

        engine_holder["engine"] = FakeEngine()  # a fresh engine that must stay unused
        second = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert second.status_code == 200
        assert second.json()["run_id"] == first.json()["run_id"]
        assert engine_holder["engine"].call_count == 0

    def test_engine_unavailable_returns_503(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        def _unavailable():
            raise EngineUnavailableError("Engine analysis is not configured.")

        app.dependency_overrides[get_engine_factory] = lambda: _unavailable

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
        # A misconfigured server is not a failed analysis.
        assert analysis_repo.get_latest_analysis_run_for_game(db_session, game.id) is None

    def test_invalid_stored_data_returns_422(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        game.move_count = 999
        db_session.commit()

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 422
        assert engine_holder["engine"].call_count == 0

    def test_client_cannot_supply_engine_or_evaluation_data(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        response = analysis_client.post(
            f"/api/games/{game.id}/analysis",
            json={
                "depth": 40,
                "engine": {"name": "AttackerFish", "version": "9.9"},
                "evaluations": [{"ply": 0, "score_cp_white": 99999}],
                "fens": ["8/8/8/8/8/8/8/8 w - -"],
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["engine"]["name"] == "FakeFish"
        assert body["engine"]["requested_depth"] == 12
        assert len(body["evaluations"]) == game.move_count + 1
        # Server-side positions were used, not the client's.
        assert engine_holder["engine"].evaluated_fens[0].startswith("rnbqkbnr/pppppppp")


class TestScoreShapeAndPerspective:
    def test_cp_and_mate_serialize_as_distinct_types(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        engine_holder["engine"] = FakeEngine(
            evaluations=[cp(80), mate(3)] + [cp(-25)] * (game.move_count + 1)
        )

        body = analysis_client.post(f"/api/games/{game.id}/analysis").json()

        assert body["evaluations"][0]["score"] == {"type": "cp", "value": 80}
        assert body["evaluations"][1]["score"] == {"type": "mate", "value": 3}
        # Mate must never be flattened into a huge centipawn number.
        assert body["evaluations"][1]["score"]["value"] == 3

    def test_black_player_scores_are_inverted(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        # In sample-games.pgn the player is Black in game_index 1.
        response = analysis_client.post(
            "/api/uploads",
            files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
            data={"player_name": "NeilSamant"},
        )
        black_game = db_session.execute(
            select(Game).where(
                Game.upload_id == response.json()["id"], Game.user_color == "black"
            )
        ).scalars().first()
        assert black_game is not None

        engine_holder["engine"] = FakeEngine(
            evaluations=[cp(150), mate(2)] + [cp(10)] * (black_game.move_count + 1)
        )

        body = analysis_client.post(f"/api/games/{black_game.id}/analysis").json()

        assert body["perspective"] == "black"
        # White is +150, so the Black player is -150...
        assert body["evaluations"][0]["score"] == {"type": "cp", "value": -150}
        assert body["evaluations"][1]["score"] == {"type": "mate", "value": -2}
        # ...while the canonical White value is preserved alongside it.
        assert body["evaluations"][0]["white_score"] == {"type": "cp", "value": 150}
        assert body["evaluations"][1]["white_score"] == {"type": "mate", "value": 2}

    def test_unknown_player_colour_falls_back_to_white(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        # Importing under a name absent from the file leaves user_color unknown.
        game = _saved_game(analysis_client, db_session, player_name="NotInThisFile")
        assert game.user_color == "unknown"

        engine_holder["engine"] = FakeEngine(
            evaluations=[cp(60)] * (game.move_count + 2)
        )

        body = analysis_client.post(f"/api/games/{game.id}/analysis").json()

        assert body["perspective"] == "white"
        assert body["evaluations"][0]["score"] == {"type": "cp", "value": 60}


class TestPersistenceAcrossRequests:
    def test_completed_analysis_is_returned_by_a_later_get(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        posted = analysis_client.post(f"/api/games/{game.id}/analysis").json()

        fetched = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert fetched["status"] == "complete"
        assert fetched["run_id"] == posted["run_id"]
        assert fetched["evaluations"] == posted["evaluations"]

    def test_failed_analysis_is_reported_by_a_later_get(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        engine_holder["engine"] = FakeEngine(fail_on_call=2)

        with pytest.raises(Exception):
            analysis_client.post(f"/api/games/{game.id}/analysis")

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()
        assert body["status"] == "failed"
        assert body["error_message"]


class TestExistingEndpointsUnchanged:
    def test_list_endpoints_carry_no_move_or_evaluation_arrays(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")

        summaries = analysis_client.get(
            f"/api/uploads/{game.upload_id}/games"
        ).json()["items"]
        uploads = analysis_client.get("/api/uploads").json()["items"]

        assert "moves" not in summaries[0]
        assert "evaluations" not in summaries[0]
        assert "analysis" not in summaries[0]
        assert all("evaluations" not in item for item in uploads)

    def test_game_detail_is_unchanged_by_analysis(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        before = analysis_client.get(f"/api/games/{game.id}").json()

        analysis_client.post(f"/api/games/{game.id}/analysis")
        after = analysis_client.get(f"/api/games/{game.id}").json()

        assert after == before
        assert "evaluations" not in after

    def test_preview_still_writes_nothing_and_offers_no_analysis(
        self, analysis_client: TestClient
    ) -> None:
        body = analysis_client.post(
            "/api/uploads/preview",
            files={"file": ("games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
            data={"player_name": "NeilSamant"},
        ).json()

        # Preview games have no persistent id, so they cannot be analyzed.
        assert "id" not in body["games"][0]
        assert "analysis" not in body["games"][0]


class TestEngineStatus:
    def test_reports_available_engine(self, analysis_client: TestClient) -> None:
        body = analysis_client.get("/api/engine/status").json()

        assert body["available"] is True
        assert body["engine_name"] == "FakeFish"
        assert body["engine_version"] == "1.0"
        assert body["configured_depth"] == 12

    def test_reports_unavailable_engine_without_raising(
        self, analysis_client: TestClient
    ) -> None:
        def _unavailable():
            raise EngineUnavailableError("Engine analysis is not configured.")

        app.dependency_overrides[get_engine_factory] = lambda: _unavailable

        response = analysis_client.get("/api/engine/status")

        assert response.status_code == 200
        assert response.json()["available"] is False
        assert "not configured" in response.json()["detail"]
