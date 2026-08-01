"""Whole-upload analysis: batch creation, derived state, and cancellation.

Batch status is a pure function of its jobs, so it is tested exhaustively as a
function first and then through the API. Getting this wrong is how a dashboard
ends up claiming an upload failed when most of it succeeded.
"""

import itertools
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.analysis_batch import (
    BATCH_CANCELLED,
    BATCH_COMPLETE,
    BATCH_FAILED,
    BATCH_PARTIAL_FAILED,
    BATCH_QUEUED,
    BATCH_RUNNING,
    AnalysisBatch,
)
from app.models.analysis_job import (
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.models.upload import Upload
from app.repositories import analysis_jobs as jobs_repo
from app.services.analysis_batches import derive_batch_status
from app.worker import runner
from app.worker.runner import WorkerConfig
from fake_engine import FakeEngine
from sample_data import SAMPLE_PGN_BYTES


class TestDerivedStatus:
    """A pure function, so every combination can be checked directly."""

    def test_a_batch_with_no_jobs_is_complete(self) -> None:
        """Not degenerate: this is what a bulk request produces when every
        game already has a matching analysis."""
        assert derive_batch_status({}) == BATCH_COMPLETE

    def test_all_queued_is_queued(self) -> None:
        assert derive_batch_status({JOB_QUEUED: 3}) == BATCH_QUEUED

    def test_any_running_is_running(self) -> None:
        assert derive_batch_status({JOB_QUEUED: 2, JOB_RUNNING: 1}) == BATCH_RUNNING

    def test_partly_finished_work_is_still_running(self) -> None:
        """Queued work alongside finished work means the batch is in progress,
        even though nothing is executing at this instant."""
        assert derive_batch_status({JOB_QUEUED: 1, JOB_COMPLETE: 2}) == BATCH_RUNNING

    def test_all_complete_is_complete(self) -> None:
        assert derive_batch_status({JOB_COMPLETE: 5}) == BATCH_COMPLETE

    def test_some_failed_and_some_complete_is_partial(self) -> None:
        """Reporting this as `failed` would hide real results."""
        assert (
            derive_batch_status({JOB_COMPLETE: 4, JOB_FAILED: 1}) == BATCH_PARTIAL_FAILED
        )

    def test_all_failed_is_failed(self) -> None:
        assert derive_batch_status({JOB_FAILED: 3}) == BATCH_FAILED

    def test_failures_outrank_cancellations(self) -> None:
        """A failure is information the user needs; a cancellation is
        something they already know about."""
        assert derive_batch_status({JOB_FAILED: 1, JOB_CANCELLED: 2}) == BATCH_FAILED

    def test_all_cancelled_is_cancelled(self) -> None:
        assert derive_batch_status({JOB_CANCELLED: 4}) == BATCH_CANCELLED

    def test_cancelling_the_rest_after_some_finished_is_cancelled(self) -> None:
        assert (
            derive_batch_status({JOB_COMPLETE: 2, JOB_CANCELLED: 3}) == BATCH_CANCELLED
        )

    def test_every_combination_yields_a_valid_status(self) -> None:
        """Totality check: no mix of job states may fall through the rules."""
        valid = {
            BATCH_QUEUED,
            BATCH_RUNNING,
            BATCH_COMPLETE,
            BATCH_PARTIAL_FAILED,
            BATCH_FAILED,
            BATCH_CANCELLED,
        }
        statuses = [JOB_QUEUED, JOB_RUNNING, JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED]

        for combination in itertools.product([0, 1, 2], repeat=len(statuses)):
            counts = dict(zip(statuses, combination))
            assert derive_batch_status(counts) in valid, counts

    def test_a_batch_is_never_complete_while_work_remains(self) -> None:
        statuses = [JOB_QUEUED, JOB_RUNNING, JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED]

        for combination in itertools.product([0, 1, 2], repeat=len(statuses)):
            counts = dict(zip(statuses, combination))
            if counts[JOB_QUEUED] or counts[JOB_RUNNING]:
                assert derive_batch_status(counts) in {BATCH_QUEUED, BATCH_RUNNING}


@pytest.fixture
def session_factory(db_session):
    @contextmanager
    def _factory():
        yield db_session

    return _factory


@pytest.fixture
def engine_holder():
    return {"engine": FakeEngine()}


@pytest.fixture
def bulk_client(client: TestClient, monkeypatch):
    """Engine configuration that matches FakeEngine, as in the route tests."""
    from pathlib import Path

    from app.services import engine as engine_module

    identity = FakeEngine().identify()
    monkeypatch.setattr(
        engine_module.settings, "stockfish_path", str(Path(engine_module.__file__))
    )
    monkeypatch.setattr(engine_module.settings, "stockfish_threads", identity.options["Threads"])
    monkeypatch.setattr(engine_module.settings, "stockfish_hash_mb", identity.options["Hash"])
    monkeypatch.setattr(
        engine_module, "_cached_binary_hash", lambda path: identity.binary_sha256
    )
    return client


@pytest.fixture
def work(db_session, engine_holder):
    @contextmanager
    def _session_factory():
        yield db_session

    def _run():
        return runner.run_once(
            config=WorkerConfig(
                worker_id="test-worker",
                lease_seconds=60,
                poll_seconds=0.01,
                depth=12,
                heartbeat_seconds=0.0,
                retry_delay_seconds=0,
            ),
            session_factory=_session_factory,
            engine_factory=lambda: engine_holder["engine"],
        )

    return _run


def _upload(client: TestClient, db_session) -> Upload:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    assert response.status_code == 201
    return db_session.get(Upload, uuid.UUID(response.json()["id"]))


def _game_count(db_session, upload_id: uuid.UUID) -> int:
    return db_session.execute(
        select(func.count()).select_from(Game).where(Game.upload_id == upload_id)
    ).scalar_one()


def _job_count(db_session, upload_id: uuid.UUID) -> int:
    """Jobs for one upload's games only.

    Always scoped: the dev database holds real analyses from ordinary use, and
    a global count would read those as this test's rows.
    """
    return db_session.execute(
        select(func.count())
        .select_from(AnalysisJob)
        .join(Game, Game.id == AnalysisJob.game_id)
        .where(Game.upload_id == upload_id)
    ).scalar_one()


def _batch_count(db_session, upload_id: uuid.UUID) -> int:
    return db_session.execute(
        select(func.count())
        .select_from(AnalysisBatch)
        .where(AnalysisBatch.upload_id == upload_id)
    ).scalar_one()


class TestBulkEnqueue:
    def test_one_action_queues_every_game(
        self, bulk_client: TestClient, db_session
    ) -> None:
        upload = _upload(bulk_client, db_session)
        expected = _game_count(db_session, upload.id)

        response = bulk_client.post(f"/api/uploads/{upload.id}/analysis")

        assert response.status_code == 202
        body = response.json()
        assert body["queued"] == expected
        assert body["total_games"] == expected
        assert body["skipped_complete"] == 0
        assert body["status"] == "queued"

    def test_every_job_belongs_to_one_batch(
        self, bulk_client: TestClient, db_session
    ) -> None:
        upload = _upload(bulk_client, db_session)

        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        jobs = jobs_repo.list_jobs_for_batch(db_session, uuid.UUID(batch_id))
        assert len(jobs) == _game_count(db_session, upload.id)
        assert {job.status for job in jobs} == {JOB_QUEUED}

    def test_batch_creation_is_all_or_nothing(
        self, bulk_client: TestClient, db_session, monkeypatch
    ) -> None:
        """A failure part-way must leave nothing behind, not half an upload."""
        upload = _upload(bulk_client, db_session)
        before_batches = _batch_count(db_session, upload.id)

        from app.services import analysis_batches as batches_module

        real_enqueue = jobs_repo.enqueue_job
        calls = {"n": 0}

        def _explode_after_two(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("simulated failure part-way through")
            return real_enqueue(*args, **kwargs)

        monkeypatch.setattr(batches_module.jobs_repo, "enqueue_job", _explode_after_two)

        with pytest.raises(RuntimeError):
            bulk_client.post(f"/api/uploads/{upload.id}/analysis")

        db_session.rollback()
        assert _batch_count(db_session, upload.id) == before_batches
        assert _job_count(db_session, upload.id) == 0

    def test_missing_upload_returns_404(self, bulk_client: TestClient) -> None:
        assert (
            bulk_client.post(f"/api/uploads/{uuid.uuid4()}/analysis").status_code == 404
        )

    def test_an_unusable_engine_queues_nothing(
        self, bulk_client: TestClient, db_session, monkeypatch
    ) -> None:
        from app.services import engine as engine_module

        upload = _upload(bulk_client, db_session)
        monkeypatch.setattr(engine_module.settings, "stockfish_path", None)

        response = bulk_client.post(f"/api/uploads/{upload.id}/analysis")

        assert response.status_code == 503
        # Scoped to this upload's games: a global count would pick up whatever
        # is already in the developer's database.
        assert _job_count(db_session, upload.id) == 0


class TestIdempotency:
    def test_pressing_twice_queues_nothing_new(
        self, bulk_client: TestClient, db_session
    ) -> None:
        upload = _upload(bulk_client, db_session)
        first = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()

        second = bulk_client.post(f"/api/uploads/{upload.id}/analysis")

        assert second.status_code == 200
        body = second.json()
        assert body["queued"] == 0
        assert body["already_active"] == first["queued"]

    def test_completed_games_are_skipped_not_re_run(
        self, bulk_client: TestClient, db_session, engine_holder, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        bulk_client.post(f"/api/uploads/{upload.id}/analysis")

        work()  # finish exactly one game
        engine_holder["engine"] = FakeEngine()

        # Cancel the rest so only the completed one is left to consider.
        for job in db_session.execute(
            select(AnalysisJob).where(AnalysisJob.status == JOB_QUEUED)
        ).scalars():
            jobs_repo.cancel_job(db_session, job_id=job.id)

        again = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()

        assert again["skipped_complete"] == 1
        assert again["queued"] == _game_count(db_session, upload.id) - 1

    def test_no_empty_batch_is_created_when_nothing_needs_doing(
        self, bulk_client: TestClient, db_session
    ) -> None:
        """An empty batch would appear in the UI as a thing that happened."""
        upload = _upload(bulk_client, db_session)
        bulk_client.post(f"/api/uploads/{upload.id}/analysis")
        before = _batch_count(db_session, upload.id)

        body = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()

        assert body["batch_id"] is None
        assert _batch_count(db_session, upload.id) == before


class TestBatchProgress:
    def test_progress_aggregates_across_games(
        self, bulk_client: TestClient, db_session, work
    ) -> None:
        """One long game must not make the whole upload look stuck at zero."""
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        before = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()
        assert before["percent"] == 0
        assert before["total_positions"] > 0

        work()
        after = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()

        assert after["games_complete"] == 1
        assert after["completed_positions"] > 0
        assert after["percent"] > 0
        assert after["status"] == "running"

    def test_a_finished_batch_reports_complete(
        self, bulk_client: TestClient, db_session, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        while work().result != "idle":
            pass

        body = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()

        assert body["status"] == "complete"
        assert body["percent"] == 100
        assert body["games_complete"] == body["total_jobs"]
        assert body["can_cancel"] is False

    def test_failed_games_are_listed_for_the_ui(
        self, bulk_client: TestClient, db_session, engine_holder, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        engine_holder["engine"] = FakeEngine(fail_on_call=1)
        work()  # attempt 1 - requeued
        engine_holder["engine"] = FakeEngine(fail_on_call=1)
        work()  # attempt 2 - terminal

        body = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()

        assert body["counts"]["failed"] == 1
        assert len(body["failed_game_ids"]) == 1

    def test_a_mixed_batch_reports_partial_failure(
        self, bulk_client: TestClient, db_session, engine_holder, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        work()  # one succeeds
        engine_holder["engine"] = FakeEngine(fail_on_call=1)
        work()
        engine_holder["engine"] = FakeEngine(fail_on_call=1)
        work()  # the same job again, now terminal

        # Cancel whatever is left so the batch reaches a terminal state.
        bulk_client.post(f"/api/analysis/batches/{batch_id}/cancel")
        body = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()

        assert body["status"] in {"partial_failed", "cancelled"}
        assert body["counts"]["complete"] >= 1
        assert body["counts"]["failed"] >= 1

    def test_unknown_batch_returns_404(self, bulk_client: TestClient) -> None:
        assert (
            bulk_client.get(f"/api/analysis/batches/{uuid.uuid4()}").status_code == 404
        )


class TestBatchCancellation:
    def test_cancelling_stops_every_queued_game(
        self, bulk_client: TestClient, db_session, engine_holder, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        response = bulk_client.post(f"/api/analysis/batches/{batch_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert work().result == "idle"
        assert engine_holder["engine"].call_count == 0

    def test_cancelling_keeps_work_already_finished(
        self, bulk_client: TestClient, db_session, work
    ) -> None:
        """Results already produced are real - cancelling the rest is not a
        reason to throw them away."""
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]
        work()

        bulk_client.post(f"/api/analysis/batches/{batch_id}/cancel")
        body = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()

        assert body["counts"]["complete"] == 1
        assert body["status"] == "cancelled"
        assert body["cancel_requested"] is True

    def test_cancelling_an_already_finished_batch_is_harmless(
        self, bulk_client: TestClient, db_session, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]
        while work().result != "idle":
            pass

        response = bulk_client.post(f"/api/analysis/batches/{batch_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "complete"


class TestCascades:
    def test_deleting_an_upload_removes_its_batch_and_jobs(
        self, bulk_client: TestClient, db_session, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = uuid.UUID(
            bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]
        )
        work()
        db_session.commit()

        assert bulk_client.delete(f"/api/uploads/{upload.id}").status_code == 204

        assert db_session.get(AnalysisBatch, batch_id) is None
        assert (
            db_session.execute(
                select(func.count())
                .select_from(AnalysisJob)
                .where(AnalysisJob.batch_id == batch_id)
            ).scalar_one()
            == 0
        )
        assert (
            db_session.execute(
                select(func.count())
                .select_from(PositionEvaluation)
                .join(AnalysisRun, AnalysisRun.id == PositionEvaluation.analysis_run_id)
                .join(Game, Game.id == AnalysisRun.game_id)
                .where(Game.upload_id == upload.id)
            ).scalar_one()
            == 0
        )


class TestBoundedWork:
    def test_queueing_many_games_starts_no_engines(
        self, bulk_client: TestClient, db_session, engine_holder
    ) -> None:
        """The request writes rows; it does not launch anything. This is what
        stops a large upload from spawning a process per game."""
        upload = _upload(bulk_client, db_session)

        bulk_client.post(f"/api/uploads/{upload.id}/analysis")

        assert engine_holder["engine"].call_count == 0

    def test_one_worker_pass_analyzes_exactly_one_game(
        self, bulk_client: TestClient, db_session, work
    ) -> None:
        upload = _upload(bulk_client, db_session)
        batch_id = bulk_client.post(f"/api/uploads/{upload.id}/analysis").json()["batch_id"]

        work()

        body = bulk_client.get(f"/api/analysis/batches/{batch_id}").json()
        assert body["counts"]["complete"] == 1
        assert body["counts"]["running"] == 0
