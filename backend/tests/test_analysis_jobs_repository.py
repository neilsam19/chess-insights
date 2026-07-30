"""Queue repository behaviour: eligibility, ownership, and transitions.

These use the ordinary rollback fixture. Genuine claim contention needs real
concurrent connections and lives in test_job_claiming_concurrency.py.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.analysis_job import (
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import STATUS_RUNNING
from app.models.game import Game
from app.repositories import analysis_jobs as jobs_repo
from fake_engine import FakeEngine
from sample_data import SAMPLE_PGN_BYTES

IDENTITY = FakeEngine().identify()
WORKER = "worker-1"
LEASE = 60


def _games(client: TestClient, db_session, count: int = 1) -> list[Game]:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    assert response.status_code == 201
    return db_session.execute(
        select(Game)
        .where(Game.upload_id == uuid.UUID(response.json()["id"]))
        .order_by(Game.game_index)
        .limit(count)
    ).scalars().all()


def _batch(db_session, game: Game):
    return jobs_repo.create_batch(
        db_session,
        upload_id=game.upload_id,
        engine_config_hash="e" * 64,
        requested_depth=12,
        engine_options={"Threads": 1, "Hash": 64},
    )


def _job(db_session, batch, game: Game, **overrides) -> AnalysisJob:
    """Enqueue a job, then force it into whatever state the test needs.

    Anything `enqueue_job` does not accept is applied afterwards as a plain
    attribute, so a test can start from `running` or `failed` without having
    to drive the whole lifecycle first.
    """
    job = jobs_repo.enqueue_job(
        db_session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=overrides.pop("config_hash", uuid.uuid4().hex),
        total_positions=overrides.pop("total_positions", game.move_count + 1),
        max_attempts=overrides.pop("max_attempts", 2),
    )
    for field, value in overrides.items():
        setattr(job, field, value)
    db_session.flush()
    return job


def _claim(db_session, worker: str = WORKER) -> AnalysisJob | None:
    return jobs_repo.claim_next_job(db_session, worker_id=worker, lease_seconds=LEASE)


class TestEnqueue:
    def test_a_new_job_starts_queued_with_no_owner(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        assert job.status == JOB_QUEUED
        assert job.attempt_count == 0
        assert job.claimed_by is None
        assert job.lease_expires_at is None
        assert job.completed_positions == 0
        assert job.total_positions == game.move_count + 1

    def test_find_active_job_ignores_terminal_jobs(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        batch = _batch(db_session, game)
        config = uuid.uuid4().hex

        _job(db_session, batch, game, config_hash=config, status=JOB_COMPLETE)

        assert jobs_repo.find_active_job(db_session, game_id=game.id, config_hash=config) is None

    def test_find_active_job_finds_a_running_one(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        batch = _batch(db_session, game)
        config = uuid.uuid4().hex
        job = _job(db_session, batch, game, config_hash=config, status=JOB_RUNNING)

        found = jobs_repo.find_active_job(db_session, game_id=game.id, config_hash=config)

        assert found is not None and found.id == job.id


class TestClaimEligibility:
    def test_claiming_marks_ownership_and_a_lease(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        claimed = _claim(db_session)

        assert claimed is not None and claimed.id == job.id
        assert claimed.status == JOB_RUNNING
        assert claimed.attempt_count == 1
        assert claimed.claimed_by == WORKER
        assert claimed.started_at is not None
        assert claimed.heartbeat_at is not None
        assert claimed.lease_expires_at is not None
        assert claimed.lease_expires_at > claimed.heartbeat_at

    def test_nothing_to_claim_returns_none(self, client: TestClient, db_session) -> None:
        assert _claim(db_session) is None

    def test_a_job_scheduled_for_later_is_not_claimed(
        self, client: TestClient, db_session
    ) -> None:
        """This is how a retry delay works."""
        game = _games(client, db_session)[0]
        _job(
            db_session,
            _batch(db_session, game),
            game,
            available_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        assert _claim(db_session) is None

    def test_a_cancelled_request_prevents_the_claim(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        _job(
            db_session,
            _batch(db_session, game),
            game,
            cancel_requested_at=datetime.now(timezone.utc),
        )

        assert _claim(db_session) is None

    def test_an_exhausted_job_is_not_claimed(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        _job(db_session, _batch(db_session, game), game, attempt_count=2, max_attempts=2)

        assert _claim(db_session) is None

    def test_the_oldest_eligible_job_is_claimed_first(
        self, client: TestClient, db_session
    ) -> None:
        games = _games(client, db_session, count=2)
        batch = _batch(db_session, games[0])
        now = datetime.now(timezone.utc)

        newer = _job(db_session, batch, games[0], available_at=now)
        older = _job(db_session, batch, games[1], available_at=now - timedelta(minutes=10))

        claimed = _claim(db_session)

        assert claimed is not None
        assert claimed.id == older.id
        assert claimed.id != newer.id

    def test_reclaiming_resets_progress_but_not_attempts(
        self, client: TestClient, db_session
    ) -> None:
        """A restarted game begins again at ply 0 - resumability is
        game-level - while attempt_count keeps counting so retries terminate."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        _claim(db_session)
        jobs_repo.heartbeat_job(
            db_session, job_id=job.id, worker_id=WORKER, lease_seconds=LEASE, completed_positions=7
        )
        jobs_repo.requeue_job(
            db_session, job_id=job.id, worker_id=WORKER, error_code="engine_crashed"
        )

        reclaimed = _claim(db_session, worker="worker-2")

        assert reclaimed is not None
        assert reclaimed.completed_positions == 0
        assert reclaimed.attempt_count == 2
        assert reclaimed.error_code is None


class TestRunLinkage:
    def test_a_run_records_the_attempt_it_belongs_to(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        claimed = _claim(db_session)

        run = jobs_repo.start_run_for_job(
            db_session, job=claimed, identity=IDENTITY, requested_depth=12
        )

        assert run.analysis_job_id == job.id
        assert run.attempt_number == 1
        assert run.status == STATUS_RUNNING
        assert run.engine_name == IDENTITY.name

    def test_latest_run_is_the_newest_attempt(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        claimed = _claim(db_session)
        first = jobs_repo.start_run_for_job(
            db_session, job=claimed, identity=IDENTITY, requested_depth=12
        )
        jobs_repo.requeue_job(
            db_session, job_id=job.id, worker_id=WORKER, error_code="engine_crashed"
        )
        reclaimed = _claim(db_session)
        second = jobs_repo.start_run_for_job(
            db_session, job=reclaimed, identity=IDENTITY, requested_depth=12
        )

        latest = jobs_repo.latest_run_for_job(db_session, job.id)

        assert latest is not None
        assert latest.id == second.id
        assert latest.id != first.id
        assert latest.attempt_number == 2


class TestOwnership:
    def test_heartbeat_renews_the_lease_for_the_owner(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        assert jobs_repo.heartbeat_job(
            db_session, job_id=job.id, worker_id=WORKER, lease_seconds=LEASE
        )

    def test_heartbeat_from_a_stranger_is_rejected(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        assert (
            jobs_repo.heartbeat_job(
                db_session, job_id=job.id, worker_id="someone-else", lease_seconds=LEASE
            )
            is False
        )

    def test_progress_never_goes_backwards(self, client: TestClient, db_session) -> None:
        """Invariant 7. An out-of-order heartbeat must not rewind the bar."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game, total_positions=40)
        _claim(db_session)

        jobs_repo.heartbeat_job(
            db_session, job_id=job.id, worker_id=WORKER, lease_seconds=LEASE, completed_positions=9
        )
        jobs_repo.heartbeat_job(
            db_session, job_id=job.id, worker_id=WORKER, lease_seconds=LEASE, completed_positions=4
        )
        db_session.refresh(job)

        assert job.completed_positions == 9

    def test_over_reported_progress_is_clamped_not_rejected(
        self, client: TestClient, db_session
    ) -> None:
        """A heartbeat must never be the thing that fails. If the worker
        miscounts, clamp - raising here would kill the lease and cause a
        spurious retry of a run that was going fine."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game, total_positions=10)
        _claim(db_session)

        assert jobs_repo.heartbeat_job(
            db_session,
            job_id=job.id,
            worker_id=WORKER,
            lease_seconds=LEASE,
            completed_positions=999,
        )
        db_session.refresh(job)

        assert job.completed_positions == 10

    def test_only_the_owner_may_complete(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        assert jobs_repo.complete_job(db_session, job_id=job.id, worker_id="imposter") is False
        assert jobs_repo.complete_job(db_session, job_id=job.id, worker_id=WORKER) is True

    def test_completing_sets_full_progress_and_releases_the_lease(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        jobs_repo.complete_job(db_session, job_id=job.id, worker_id=WORKER)
        db_session.refresh(job)

        assert job.status == JOB_COMPLETE
        assert job.completed_positions == job.total_positions
        assert job.completed_at is not None
        assert job.claimed_by is None
        assert job.lease_expires_at is None

    def test_a_queued_job_cannot_be_completed(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        assert jobs_repo.complete_job(db_session, job_id=job.id, worker_id=WORKER) is False

    def test_failing_records_the_code_and_releases_the_lease(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        assert jobs_repo.fail_job(
            db_session,
            job_id=job.id,
            worker_id=WORKER,
            error_code="invalid_game_data",
            error_message="This game has no moves to analyze.",
        )
        db_session.refresh(job)

        assert job.status == JOB_FAILED
        assert job.error_code == "invalid_game_data"
        assert job.claimed_by is None

    def test_the_sweep_may_fail_a_job_it_does_not_own(
        self, client: TestClient, db_session
    ) -> None:
        """Stale recovery works precisely because it is not the owner."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        assert jobs_repo.fail_job(
            db_session, job_id=job.id, worker_id=None, error_code="worker_lease_expired"
        )


class TestStaleDetection:
    def test_a_live_lease_is_not_stale(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        assert job.id not in {stale.id for stale in jobs_repo.find_stale_jobs(db_session)}

    def test_an_expired_lease_is_stale(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.flush()

        assert job.id in {stale.id for stale in jobs_repo.find_stale_jobs(db_session)}

    def test_a_queued_job_is_never_stale(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        assert job.id not in {stale.id for stale in jobs_repo.find_stale_jobs(db_session)}


class TestCancellation:
    def test_a_queued_job_is_cancelled_immediately(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        outcome = jobs_repo.cancel_job(db_session, job_id=job.id)
        db_session.refresh(job)

        assert outcome.cancelled_now is True
        assert job.status == JOB_CANCELLED

    def test_a_cancelled_job_cannot_then_be_claimed(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        jobs_repo.cancel_job(db_session, job_id=job.id)

        assert _claim(db_session) is None

    def test_a_running_job_is_only_asked_to_stop(self, client: TestClient, db_session) -> None:
        """The worker performs the transition itself, at a position boundary -
        that is what keeps partial evaluations from ever being stored."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        outcome = jobs_repo.cancel_job(db_session, job_id=job.id)
        db_session.refresh(job)

        assert outcome.cancelled_now is False
        assert outcome.requested is True
        assert job.status == JOB_RUNNING
        assert job.cancel_requested_at is not None

    def test_the_worker_stands_down_when_asked(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)
        jobs_repo.cancel_job(db_session, job_id=job.id)

        assert jobs_repo.mark_job_cancelled(db_session, job_id=job.id, worker_id=WORKER)
        db_session.refresh(job)

        assert job.status == JOB_CANCELLED
        assert job.completed_positions == 0
        assert job.claimed_by is None

    def test_cancelling_a_finished_job_changes_nothing(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game, status=JOB_COMPLETE)

        outcome = jobs_repo.cancel_job(db_session, job_id=job.id)
        db_session.refresh(job)

        assert outcome.already_terminal is True
        assert job.status == JOB_COMPLETE

    def test_cancelling_an_unknown_job_returns_none(self, db_session) -> None:
        assert jobs_repo.cancel_job(db_session, job_id=uuid.uuid4()) is None


class TestTransitionRules:
    @pytest.mark.parametrize("terminal", [JOB_FAILED, JOB_CANCELLED])
    def test_retry_requeues_a_terminal_job(
        self, client: TestClient, db_session, terminal
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(
            db_session,
            _batch(db_session, game),
            game,
            status=terminal,
            attempt_count=2,
            max_attempts=2,
            error_code="engine_crashed",
        )

        retried = jobs_repo.retry_job(db_session, job_id=job.id)

        assert retried.status == JOB_QUEUED
        assert retried.error_code is None
        assert retried.completed_positions == 0
        # History is preserved; headroom is added instead.
        assert retried.attempt_count == 2
        assert retried.max_attempts == 3

    def test_a_completed_job_cannot_be_retried(self, client: TestClient, db_session) -> None:
        """Invariant 6: a completed run is immutable. Re-analysing means a new
        job under a different configuration, never reopening this one."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game, status=JOB_COMPLETE)

        with pytest.raises(jobs_repo.InvalidJobTransition):
            jobs_repo.retry_job(db_session, job_id=job.id)

    def test_a_running_job_cannot_be_retried(self, client: TestClient, db_session) -> None:
        """Stricter than the transition table on purpose. `running -> queued`
        exists for the stale sweep, which owns the lease; a user who wants a
        running job to stop wants cancel, not retry."""
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)
        _claim(db_session)

        with pytest.raises(jobs_repo.InvalidJobTransition):
            jobs_repo.retry_job(db_session, job_id=job.id)

    def test_a_queued_job_cannot_be_retried(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game)

        with pytest.raises(jobs_repo.InvalidJobTransition):
            jobs_repo.retry_job(db_session, job_id=job.id)

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (JOB_QUEUED, JOB_COMPLETE),
            (JOB_QUEUED, JOB_FAILED),
            (JOB_COMPLETE, JOB_QUEUED),
            (JOB_COMPLETE, JOB_RUNNING),
            (JOB_FAILED, JOB_RUNNING),
            (JOB_CANCELLED, JOB_RUNNING),
        ],
    )
    def test_transitions_outside_the_state_machine_are_rejected(
        self, client: TestClient, db_session, from_status, to_status
    ) -> None:
        game = _games(client, db_session)[0]
        job = _job(db_session, _batch(db_session, game), game, status=from_status)

        with pytest.raises(jobs_repo.InvalidJobTransition):
            jobs_repo.assert_transition(job, to_status)

    def test_every_documented_transition_is_permitted(
        self, client: TestClient, db_session
    ) -> None:
        game = _games(client, db_session)[0]
        batch = _batch(db_session, game)
        documented = [
            (JOB_QUEUED, JOB_RUNNING),
            (JOB_QUEUED, JOB_CANCELLED),
            (JOB_RUNNING, JOB_COMPLETE),
            (JOB_RUNNING, JOB_FAILED),
            (JOB_RUNNING, JOB_CANCELLED),
            (JOB_RUNNING, JOB_QUEUED),
            (JOB_FAILED, JOB_QUEUED),
            (JOB_CANCELLED, JOB_QUEUED),
        ]

        for from_status, to_status in documented:
            job = _job(db_session, batch, game, status=from_status)
            jobs_repo.assert_transition(job, to_status)  # must not raise


class TestBatchCounts:
    def test_counts_group_by_status(self, client: TestClient, db_session) -> None:
        games = _games(client, db_session, count=2)
        batch = _batch(db_session, games[0])

        _job(db_session, batch, games[0], status=JOB_COMPLETE)
        _job(db_session, batch, games[1], status=JOB_FAILED)
        _job(db_session, batch, games[0], status=JOB_QUEUED)

        counts = jobs_repo.count_jobs_by_status(db_session, batch.id)

        assert counts == {JOB_COMPLETE: 1, JOB_FAILED: 1, JOB_QUEUED: 1}

    def test_an_empty_batch_has_no_counts(self, client: TestClient, db_session) -> None:
        game = _games(client, db_session)[0]
        batch = _batch(db_session, game)

        assert jobs_repo.count_jobs_by_status(db_session, batch.id) == {}
