"""Lease expiry and recovery of work abandoned by a dead worker.

This is the checkpoint that removes Sprint 4's worst defect: a killed process
left a `running` record that could never be retried through the application.

Leases are expired here by writing `lease_expires_at` into the past, which is
what a dead worker's row looks like a minute after it stopped renewing.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.analysis_job import (
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import STATUS_COMPLETE, STATUS_FAILED, STATUS_RUNNING
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis_jobs as jobs_repo
from app.worker import runner, sweeper
from app.worker.runner import COMPLETED, WorkerConfig
from app.worker.sweeper import EXHAUSTED, LEASE_EXPIRED_CODE, REQUEUED, recover_stale_jobs
from fake_engine import FakeEngine
from sample_data import SAMPLE_PGN_BYTES

DEAD_WORKER = "worker-that-died"
LIVE_WORKER = "worker-still-here"


@pytest.fixture
def session_factory(db_session):
    @contextmanager
    def _factory():
        yield db_session

    return _factory


@pytest.fixture
def config() -> WorkerConfig:
    # heartbeat_seconds=0: the shared test Session cannot be touched from a
    # background thread. The threaded path is covered in
    # test_worker_concurrency.py, where connections are independent.
    return WorkerConfig(
        worker_id=LIVE_WORKER,
        lease_seconds=60,
        poll_seconds=0.01,
        depth=8,
        heartbeat_seconds=0.0,
        retry_delay_seconds=0,
    )


def _game(client: TestClient, db_session) -> Game:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    assert response.status_code == 201
    games = db_session.execute(
        select(Game).where(Game.upload_id == uuid.UUID(response.json()["id"]))
    ).scalars().all()
    return min(games, key=lambda g: g.move_count)


def _queue(db_session, game: Game, **overrides) -> AnalysisJob:
    batch = jobs_repo.create_batch(
        db_session,
        upload_id=game.upload_id,
        engine_config_hash="e" * 64,
        requested_depth=8,
        engine_options={"Threads": 1},
    )
    job = jobs_repo.enqueue_job(
        db_session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=uuid.uuid4().hex,
        total_positions=game.move_count + 1,
        max_attempts=overrides.pop("max_attempts", 2),
    )
    for field, value in overrides.items():
        setattr(job, field, value)
    db_session.flush()
    return job


def _abandon(db_session, job: AnalysisJob, *, attempt: int = 1, worker: str = DEAD_WORKER):
    """Make the job look exactly like one whose worker was killed."""
    job.status = JOB_RUNNING
    job.claimed_by = worker
    job.attempt_count = attempt
    job.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    job.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_session.flush()
    return job


class TestRecovery:
    def test_an_abandoned_job_with_attempts_left_is_requeued(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _game(client, db_session)
        job = _abandon(db_session, _queue(db_session, game, max_attempts=2), attempt=1)

        recoveries = recover_stale_jobs(session_factory, retry_delay_seconds=0)
        db_session.refresh(job)

        assert [r.job_id for r in recoveries] == [job.id]
        assert recoveries[0].result == REQUEUED
        assert job.status == JOB_QUEUED
        assert job.error_code == LEASE_EXPIRED_CODE
        assert job.claimed_by is None
        assert job.lease_expires_at is None

    def test_an_abandoned_final_attempt_fails(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _game(client, db_session)
        job = _abandon(db_session, _queue(db_session, game, max_attempts=2), attempt=2)

        recoveries = recover_stale_jobs(session_factory, retry_delay_seconds=0)
        db_session.refresh(job)

        assert recoveries[0].result == EXHAUSTED
        assert job.status == JOB_FAILED
        assert job.error_code == LEASE_EXPIRED_CODE
        assert job.claimed_by is None

    def test_a_live_lease_is_left_alone(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        """The whole mechanism rests on this: a working worker is never
        interrupted just because a sweep happened to run."""
        game = _game(client, db_session)
        job = _queue(db_session, game)
        runner.run_once(
            config=WorkerConfig(
                worker_id=LIVE_WORKER, lease_seconds=600, poll_seconds=0.01, depth=8
            ),
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        # Put it back into a genuinely running state with a valid lease.
        job.status = JOB_RUNNING
        job.claimed_by = LIVE_WORKER
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        db_session.flush()

        assert recover_stale_jobs(session_factory, retry_delay_seconds=0) == []

        db_session.refresh(job)
        assert job.status == JOB_RUNNING
        assert job.claimed_by == LIVE_WORKER

    def test_a_queued_job_is_never_swept(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        assert recover_stale_jobs(session_factory, retry_delay_seconds=0) == []

        db_session.refresh(job)
        assert job.status == JOB_QUEUED

    def test_recovery_is_idempotent(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        """Two sweepers, or one run twice, must not double-count attempts."""
        game = _game(client, db_session)
        job = _abandon(db_session, _queue(db_session, game), attempt=1)

        first = recover_stale_jobs(session_factory, retry_delay_seconds=0)
        second = recover_stale_jobs(session_factory, retry_delay_seconds=0)
        db_session.refresh(job)

        assert len(first) == 1
        assert second == []
        assert job.attempt_count == 1


class TestAbandonedRuns:
    def test_the_abandoned_run_is_failed_not_left_running(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        """The stuck `running` run is the visible symptom of the Sprint 4
        defect. Recovery has to close it, not just the job."""
        game = _game(client, db_session)
        job = _queue(db_session, game)
        claimed = jobs_repo.claim_next_job(
            db_session, worker_id=DEAD_WORKER, lease_seconds=60
        )
        run = jobs_repo.start_run_for_job(
            db_session, job=claimed, identity=FakeEngine().identify(), requested_depth=8
        )
        _abandon(db_session, job, attempt=1)

        recover_stale_jobs(session_factory, retry_delay_seconds=0)
        db_session.refresh(run)

        assert run.status == STATUS_FAILED
        assert run.completed_at is not None
        assert "stopped responding" in run.error_message

    def test_an_abandoned_run_keeps_no_evaluations(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)
        claimed = jobs_repo.claim_next_job(
            db_session, worker_id=DEAD_WORKER, lease_seconds=60
        )
        run = jobs_repo.start_run_for_job(
            db_session, job=claimed, identity=FakeEngine().identify(), requested_depth=8
        )
        _abandon(db_session, job, attempt=1)

        recover_stale_jobs(session_factory, retry_delay_seconds=0)

        stored = db_session.execute(
            select(func.count())
            .select_from(PositionEvaluation)
            .where(PositionEvaluation.analysis_run_id == run.id)
        ).scalar_one()
        assert stored == 0

    def test_a_completed_run_is_not_touched(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """A worker that finished but whose job row was somehow left running
        must not have its good results marked failed."""
        game = _game(client, db_session)
        job = _queue(db_session, game)
        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        run = jobs_repo.latest_run_for_job(db_session, job.id)
        assert run.status == STATUS_COMPLETE

        _abandon(db_session, job, attempt=1)
        recover_stale_jobs(session_factory, retry_delay_seconds=0)
        db_session.refresh(run)

        assert run.status == STATUS_COMPLETE


class TestRecoveredWorkRunsAgain:
    def test_a_recovered_job_is_analyzed_from_ply_zero(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Resumability is game-level by design: the retry re-analyzes the
        whole game, because partial evaluations are never persisted."""
        game = _game(client, db_session)
        job = _queue(db_session, game)
        expected = game.move_count + 1

        # First attempt gets partway, then its worker dies.
        claimed = jobs_repo.claim_next_job(
            db_session, worker_id=DEAD_WORKER, lease_seconds=60
        )
        jobs_repo.start_run_for_job(
            db_session, job=claimed, identity=FakeEngine().identify(), requested_depth=8
        )
        jobs_repo.heartbeat_job(
            db_session,
            job_id=job.id,
            worker_id=DEAD_WORKER,
            lease_seconds=60,
            completed_positions=4,
        )
        _abandon(db_session, job, attempt=1)

        recover_stale_jobs(session_factory, retry_delay_seconds=0)
        db_session.refresh(job)
        assert job.completed_positions == 0

        engine = FakeEngine()
        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=lambda: engine
        )
        db_session.refresh(job)

        assert outcome.result == COMPLETED
        assert job.status == JOB_COMPLETE
        assert job.attempt_count == 2
        assert engine.call_count == expected

        run = jobs_repo.latest_run_for_job(db_session, job.id)
        assert run.attempt_number == 2
        assert len(run.evaluations) == expected

    def test_a_retry_delay_holds_the_job_back(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """A job that reliably kills its worker should not spin in a tight
        claim-crash-claim loop."""
        game = _game(client, db_session)
        job = _abandon(db_session, _queue(db_session, game), attempt=1)

        recover_stale_jobs(session_factory, retry_delay_seconds=300)
        db_session.refresh(job)

        assert job.status == JOB_QUEUED
        assert (
            runner.run_once(
                config=config, session_factory=session_factory, engine_factory=FakeEngine
            ).result
            != COMPLETED
        )

    def test_attempt_history_survives_recovery(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Each attempt keeps its own run, so provenance is not overwritten."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        claimed = jobs_repo.claim_next_job(
            db_session, worker_id=DEAD_WORKER, lease_seconds=60
        )
        first_run = jobs_repo.start_run_for_job(
            db_session, job=claimed, identity=FakeEngine().identify(), requested_depth=8
        )
        _abandon(db_session, job, attempt=1)
        recover_stale_jobs(session_factory, retry_delay_seconds=0)

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        runs = db_session.execute(
            select(jobs_repo.AnalysisRun)
            .where(jobs_repo.AnalysisRun.analysis_job_id == job.id)
            .order_by(jobs_repo.AnalysisRun.attempt_number)
        ).scalars().all()

        assert [r.attempt_number for r in runs] == [1, 2]
        assert runs[0].id == first_run.id
        assert runs[0].status == STATUS_FAILED
        assert runs[1].status == STATUS_COMPLETE


class TestSweeperResilience:
    def test_one_unrecoverable_job_does_not_stop_the_sweep(
        self, client: TestClient, db_session, session_factory, monkeypatch
    ) -> None:
        game = _game(client, db_session)
        first = _abandon(db_session, _queue(db_session, game), attempt=1)
        second = _abandon(db_session, _queue(db_session, game), attempt=1)

        original = sweeper._recover_one
        calls = {"n": 0}

        def _explode_once(db, job_id, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated failure recovering the first job")
            return original(db, job_id, **kwargs)

        monkeypatch.setattr(sweeper, "_recover_one", _explode_once)

        recoveries = recover_stale_jobs(session_factory, retry_delay_seconds=0)

        assert len(recoveries) == 1
        assert {first.id, second.id} >= {recoveries[0].job_id}
