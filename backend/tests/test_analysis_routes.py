"""HTTP contract for durable analysis.

The endpoint no longer analyzes anything. It validates, writes a job, and
returns - so these tests drive the worker explicitly when a result is needed,
which is exactly what happens in production with a separate process.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.dependencies import get_engine_factory
from app.main import app
from app.models.analysis_job import JOB_QUEUED, AnalysisJob
from app.models.game import Game
from app.repositories import analysis as analysis_repo
from app.repositories import analysis_jobs as jobs_repo
from app.services import analysis_enqueue
from app.services.engine import EngineUnavailableError
from fake_engine import FakeEngine, cp, mate
from sample_data import SAMPLE_PGN_BYTES


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


class TestEnqueue:
    def test_queueing_a_game_returns_202_immediately(
        self, analysis_client: TestClient, db_session, engine_holder
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["job_id"]
        assert body["progress"]["total_positions"] == game.move_count + 1
        assert body["progress"]["completed_positions"] == 0
        assert body["can_cancel"] is True
        # The request did no analysis of its own.
        assert engine_holder["engine"].call_count == 0

    def test_a_queued_job_is_durable(
        self, analysis_client: TestClient, db_session
    ) -> None:
        """The point of the whole sprint: the job outlives the request."""
        game = _saved_game(analysis_client, db_session)

        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]

        stored = db_session.get(AnalysisJob, uuid.UUID(job_id))
        assert stored is not None
        assert stored.status == JOB_QUEUED

    def test_missing_game_returns_404(self, analysis_client: TestClient) -> None:
        assert (
            analysis_client.post(f"/api/games/{uuid.uuid4()}/analysis").status_code == 404
        )

    def test_a_second_request_conflicts_rather_than_duplicating(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        first = analysis_client.post(f"/api/games/{game.id}/analysis")

        second = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert second.status_code == 409
        assert second.json()["job_id"] == first.json()["job_id"]
        assert (
            db_session.execute(
                select(AnalysisJob).where(AnalysisJob.game_id == game.id)
            ).scalars().all().__len__()
            == 1
        )

    def test_a_completed_analysis_is_reused_with_200(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()

        engine_holder["engine"] = FakeEngine()  # must stay unused
        again = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert again.status_code == 200
        assert again.json()["status"] == "complete"
        assert engine_holder["engine"].call_count == 0

    def test_invalid_stored_data_returns_422_and_queues_nothing(
        self, analysis_client: TestClient, db_session
    ) -> None:
        """Rejected up front rather than queued and then failed - that would be
        a slower way of saying the same thing, with a failed record left over."""
        game = _saved_game(analysis_client, db_session)
        game.move_count = 999
        db_session.flush()

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 422
        assert jobs_repo.latest_job_for_game(db_session, game.id) is None

    def test_unusable_engine_returns_503_and_queues_nothing(
        self, analysis_client: TestClient, db_session, monkeypatch
    ) -> None:
        """A misconfigured server is not a failed analysis."""
        from app.services import engine as engine_module

        game = _saved_game(analysis_client, db_session)
        monkeypatch.setattr(engine_module.settings, "stockfish_path", None)

        response = analysis_client.post(f"/api/games/{game.id}/analysis")

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
        assert jobs_repo.latest_job_for_game(db_session, game.id) is None

    def test_the_client_cannot_influence_the_analysis(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        response = analysis_client.post(
            f"/api/games/{game.id}/analysis",
            json={
                "depth": 40,
                "engine": {"name": "AttackerFish"},
                "evaluations": [{"ply": 0, "score_cp_white": 99999}],
                "fens": ["8/8/8/8/8/8/8/8 w - -"],
                "status": "complete",
                "progress": {"completed_positions": 999},
            },
        )
        assert response.status_code == 202
        work()

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()
        assert body["engine"]["name"] == "FakeFish"
        assert body["engine"]["requested_depth"] == 12
        assert len(body["evaluations"]) == game.move_count + 1
        # Server-side positions were used, not the client's.
        assert engine_holder["engine"].evaluated_fens[0].startswith("rnbqkbnr/pppppppp")


class TestGetAnalysis:
    def test_missing_game_returns_404(self, analysis_client: TestClient) -> None:
        assert (
            analysis_client.get(f"/api/games/{uuid.uuid4()}/analysis").status_code == 404
        )

    def test_an_untouched_game_reports_not_analyzed(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "not_analyzed"
        assert "evaluations" not in body

    def test_progress_is_reported_while_running(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        job = jobs_repo.claim_next_job(db_session, worker_id="w", lease_seconds=60)
        jobs_repo.heartbeat_job(
            db_session, job_id=job.id, worker_id="w", lease_seconds=60, completed_positions=3
        )

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "running"
        assert body["progress"]["completed_positions"] == 3
        assert body["progress"]["total_positions"] == game.move_count + 1
        assert 0 < body["progress"]["percent"] < 100
        assert body["can_cancel"] is True

    def test_a_completed_analysis_is_returned_after_the_worker_runs(
        self, analysis_client: TestClient, db_session, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "complete"
        assert len(body["evaluations"]) == game.move_count + 1
        assert [e["ply"] for e in body["evaluations"]] == list(range(game.move_count + 1))
        assert body["can_retry"] is False

    def test_a_failed_job_reports_its_code_and_offers_retry(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")

        # A fresh engine per pass: a real worker starts a new process each
        # attempt, and reusing one instance would carry its call counter over
        # so the second attempt never reached the failure point.
        engine_holder["engine"] = FakeEngine(fail_on_call=2)
        work()  # attempt 1 - retryable, requeued
        engine_holder["engine"] = FakeEngine(fail_on_call=2)
        work()  # attempt 2 - attempts exhausted, terminal

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "failed"
        assert body["error_code"] == "engine_crashed"
        assert body["can_retry"] is True
        assert "Traceback" not in (body["error_message"] or "")

    def test_a_pending_cancellation_is_visible_while_still_running(
        self, analysis_client: TestClient, db_session
    ) -> None:
        """A running job only stops at a position boundary, so there is a gap
        between asking and stopping. Without surfacing it, the UI would keep
        showing normal progress and the button would look broken."""
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]
        jobs_repo.claim_next_job(db_session, worker_id="w", lease_seconds=60)

        analysis_client.post(f"/api/analysis/jobs/{job_id}/cancel")
        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "running"
        assert body["cancel_requested"] is True
        # Asking again achieves nothing - it is already stopping.
        assert body["can_cancel"] is False

    def test_a_running_job_is_not_marked_cancelling_by_default(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        jobs_repo.claim_next_job(db_session, worker_id="w", lease_seconds=60)

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["cancel_requested"] is False
        assert body["can_cancel"] is True

    def test_a_cancelled_job_is_reported_as_cancelled(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]
        analysis_client.post(f"/api/analysis/jobs/{job_id}/cancel")

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "cancelled"
        assert body["can_retry"] is True

    def test_results_survive_a_later_failed_retry(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        """Real results beat a more recent failure - hiding a good analysis
        behind an error would be worse than useless."""
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()
        completed = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        # Force a second, failing job at a different configuration.
        batch = jobs_repo.create_batch(
            db_session,
            upload_id=game.upload_id,
            engine_config_hash="other",
            requested_depth=20,
            engine_options={},
        )
        job = jobs_repo.enqueue_job(
            db_session,
            batch_id=batch.id,
            game_id=game.id,
            config_hash="other-config",
            total_positions=game.move_count + 1,
            max_attempts=1,
        )
        engine_holder["engine"] = FakeEngine(fail_on_call=1)
        work()
        db_session.refresh(job)

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()
        assert body["status"] == "complete"
        assert body["run_id"] == completed["run_id"]


class TestJobEndpoints:
    def test_a_job_can_be_read_on_its_own(
        self, analysis_client: TestClient, db_session
    ) -> None:
        """Polling must not re-download every evaluation each time."""
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]

        body = analysis_client.get(f"/api/analysis/jobs/{job_id}").json()

        assert body["status"] == "queued"
        assert body["game_id"] == str(game.id)
        assert body["progress"]["total_positions"] == game.move_count + 1
        assert "evaluations" not in body

    def test_unknown_job_returns_404(self, analysis_client: TestClient) -> None:
        assert (
            analysis_client.get(f"/api/analysis/jobs/{uuid.uuid4()}").status_code == 404
        )

    def test_cancelling_a_queued_job_stops_it_being_claimed(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]

        response = analysis_client.post(f"/api/analysis/jobs/{job_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert work().result == "idle"
        assert engine_holder["engine"].call_count == 0

    def test_cancelling_a_finished_job_is_not_an_error(
        self, analysis_client: TestClient, db_session, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]
        work()

        response = analysis_client.post(f"/api/analysis/jobs/{job_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "complete"

    def test_a_cancelled_job_can_be_retried_and_then_run(
        self, analysis_client: TestClient, db_session, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]
        analysis_client.post(f"/api/analysis/jobs/{job_id}/cancel")

        retried = analysis_client.post(f"/api/analysis/jobs/{job_id}/retry")
        assert retried.status_code == 200
        assert retried.json()["status"] == "queued"

        work()
        assert (
            analysis_client.get(f"/api/games/{game.id}/analysis").json()["status"]
            == "complete"
        )

    def test_a_completed_job_cannot_be_retried(
        self, analysis_client: TestClient, db_session, work
    ) -> None:
        """A completed run is immutable."""
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]
        work()

        response = analysis_client.post(f"/api/analysis/jobs/{job_id}/retry")

        assert response.status_code == 409
        assert "complete" in response.json()["detail"]

    def test_a_queued_job_cannot_be_retried(
        self, analysis_client: TestClient, db_session
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]

        assert (
            analysis_client.post(f"/api/analysis/jobs/{job_id}/retry").status_code == 409
        )


class TestScoreShapeAndPerspective:
    def test_cp_and_mate_stay_distinct_types(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        engine_holder["engine"] = FakeEngine(
            evaluations=[cp(80), mate(3)] + [cp(-25)] * (game.move_count + 1)
        )
        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["evaluations"][0]["score"] == {"type": "cp", "value": 80}
        # Mate must never be flattened into a huge centipawn number.
        assert body["evaluations"][1]["score"] == {"type": "mate", "value": 3}

    def test_black_player_scores_are_inverted(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
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
        analysis_client.post(f"/api/games/{black_game.id}/analysis")
        work()

        body = analysis_client.get(f"/api/games/{black_game.id}/analysis").json()

        assert body["perspective"] == "black"
        assert body["evaluations"][0]["score"] == {"type": "cp", "value": -150}
        assert body["evaluations"][1]["score"] == {"type": "mate", "value": -2}
        # The canonical White value is preserved alongside it.
        assert body["evaluations"][0]["white_score"] == {"type": "cp", "value": 150}

    def test_unknown_player_colour_falls_back_to_white(
        self, analysis_client: TestClient, db_session, engine_holder, work
    ) -> None:
        game = _saved_game(analysis_client, db_session, player_name="NotInThisFile")
        assert game.user_color == "unknown"
        engine_holder["engine"] = FakeEngine(evaluations=[cp(60)] * (game.move_count + 2))
        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()

        body = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["perspective"] == "white"
        assert body["evaluations"][0]["score"] == {"type": "cp", "value": 60}


class TestReuseAcrossConfigurations:
    def test_a_run_from_a_different_depth_is_not_reused(
        self, analysis_client: TestClient, db_session
    ) -> None:
        """Analysing the same game deeper is different work, not a duplicate."""
        game = _saved_game(analysis_client, db_session)
        run = analysis_repo.create_analysis_run(
            db_session,
            game_id=game.id,
            identity=FakeEngine().identify(),
            requested_depth=99,
        )
        analysis_repo.mark_analysis_run_complete(db_session, run)

        assert analysis_enqueue.find_reusable_run(db_session, game.id, "any-hash") is None

    def test_a_sprint_4_run_is_still_reusable(
        self, analysis_client: TestClient, db_session, monkeypatch
    ) -> None:
        """Runs that predate jobs have no config_hash. Identity is recomputed
        from what they recorded, so upgrading did not silently invalidate every
        stored evaluation."""
        from app.services.analysis_config import compute_engine_config_hash
        from app.services.engine import EngineConfig

        game = _saved_game(analysis_client, db_session)
        identity = FakeEngine().identify()
        run = analysis_repo.create_analysis_run(
            db_session, game_id=game.id, identity=identity, requested_depth=12
        )
        analysis_repo.mark_analysis_run_complete(db_session, run)
        assert run.analysis_job_id is None

        matching_hash = compute_engine_config_hash(
            EngineConfig(
                path="anywhere",
                binary_sha256=identity.binary_sha256,
                options=dict(identity.options),
                depth=12,
            )
        )

        found = analysis_enqueue.find_reusable_run(db_session, game.id, matching_hash)
        assert found is not None and found.id == run.id


class TestExistingEndpointsUnchanged:
    def test_list_endpoints_carry_no_move_or_evaluation_arrays(
        self, analysis_client: TestClient, db_session, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()

        summaries = analysis_client.get(
            f"/api/uploads/{game.upload_id}/games"
        ).json()["items"]
        uploads = analysis_client.get("/api/uploads").json()["items"]

        assert "moves" not in summaries[0]
        assert "evaluations" not in summaries[0]
        assert all("evaluations" not in item for item in uploads)

    def test_game_detail_is_unchanged_by_analysis(
        self, analysis_client: TestClient, db_session, work
    ) -> None:
        game = _saved_game(analysis_client, db_session)
        before = analysis_client.get(f"/api/games/{game.id}").json()

        analysis_client.post(f"/api/games/{game.id}/analysis")
        work()

        assert analysis_client.get(f"/api/games/{game.id}").json() == before

    def test_preview_still_offers_no_analysis(self, analysis_client: TestClient) -> None:
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
        app.dependency_overrides[get_engine_factory] = lambda: FakeEngine

        body = analysis_client.get("/api/engine/status").json()

        assert body["available"] is True
        assert body["engine_name"] == "FakeFish"
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
