"""Cancellation, retry policy, and error classification.

The point of classifying errors is that "retry everything" and "retry nothing"
are both wrong. A crashed engine deserves another go; a game with no moves will
be exactly as broken next time. These tests pin down which is which, and prove
that a cancelled or failed attempt never leaves partial evaluations behind.
"""

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.analysis_job import (
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    AnalysisJob,
)
from app.models.analysis_run import (
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_FAILED,
    AnalysisRun,
)
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis_jobs as jobs_repo
from app.services import analysis_errors
from app.services.analysis_executor import GameNotFoundError, InvalidGameDataError
from app.services.engine import (
    AmbiguousScoreError,
    EngineCrashedError,
    EngineFailureError,
    EngineMisconfiguredError,
    EngineUnavailableError,
    InvalidPositionError,
)
from app.worker import runner
from app.worker.runner import CANCELLED, COMPLETED, FAILED, REQUEUED, WorkerConfig
from fake_engine import FakeEngine
from sample_data import SAMPLE_PGN_BYTES

WORKER = "test-worker"


@pytest.fixture
def session_factory(db_session):
    @contextmanager
    def _factory():
        yield db_session

    return _factory


@pytest.fixture
def config() -> WorkerConfig:
    return WorkerConfig(
        worker_id=WORKER,
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
    return jobs_repo.enqueue_job(
        db_session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=uuid.uuid4().hex,
        total_positions=game.move_count + 1,
        max_attempts=overrides.pop("max_attempts", 2),
    )


def _evaluation_count(db_session, job_id: uuid.UUID) -> int:
    return db_session.execute(
        select(func.count())
        .select_from(PositionEvaluation)
        .join(AnalysisRun, AnalysisRun.id == PositionEvaluation.analysis_run_id)
        .where(AnalysisRun.analysis_job_id == job_id)
    ).scalar_one()


class TestErrorClassification:
    @pytest.mark.parametrize(
        "exc,expected",
        [
            (EngineCrashedError("died"), analysis_errors.ENGINE_CRASHED),
            (InvalidPositionError("bad fen"), analysis_errors.INVALID_POSITION),
            (AmbiguousScoreError("no score"), analysis_errors.AMBIGUOUS_SCORE),
            (EngineUnavailableError("missing"), analysis_errors.ENGINE_UNAVAILABLE),
            (EngineMisconfiguredError("bad option"), analysis_errors.ENGINE_MISCONFIGURED),
            (InvalidGameDataError("no moves"), analysis_errors.INVALID_GAME_DATA),
            (GameNotFoundError("gone"), analysis_errors.INVALID_GAME_DATA),
            (EngineFailureError("generic"), analysis_errors.ENGINE_CRASHED),
            (RuntimeError("who knows"), analysis_errors.UNEXPECTED_ERROR),
        ],
    )
    def test_each_failure_maps_to_its_code(self, exc, expected) -> None:
        assert analysis_errors.classify(exc) == expected

    def test_misconfigured_is_distinguished_from_missing(self) -> None:
        """Both are 503s and both subclass unavailable, but a wrong option and
        a missing binary are fixed differently, so they must not collapse."""
        assert isinstance(EngineMisconfiguredError("x"), EngineUnavailableError)
        assert analysis_errors.classify(
            EngineMisconfiguredError("x")
        ) != analysis_errors.classify(EngineUnavailableError("x"))

    @pytest.mark.parametrize(
        "code",
        [
            analysis_errors.ENGINE_CRASHED,
            analysis_errors.WORKER_LEASE_EXPIRED,
            analysis_errors.DATABASE_UNAVAILABLE,
        ],
    )
    def test_transient_failures_are_retryable(self, code) -> None:
        assert analysis_errors.is_retryable(code)

    @pytest.mark.parametrize(
        "code",
        [
            analysis_errors.INVALID_GAME_DATA,
            analysis_errors.INVALID_POSITION,
            analysis_errors.ENGINE_UNAVAILABLE,
            analysis_errors.ENGINE_MISCONFIGURED,
            analysis_errors.AMBIGUOUS_SCORE,
            analysis_errors.EVALUATION_COUNT_MISMATCH,
            analysis_errors.CANCELLED_BY_USER,
            analysis_errors.UNEXPECTED_ERROR,
        ],
    )
    def test_deterministic_failures_are_not_retryable(self, code) -> None:
        assert not analysis_errors.is_retryable(code)

    def test_an_unrecognised_error_is_not_retried(self) -> None:
        """An error nobody anticipated is the last thing to repeat blindly."""
        assert not analysis_errors.is_retryable(
            analysis_errors.classify(RuntimeError("surprise"))
        )

    def test_messages_never_leak_internals(self) -> None:
        leaky = RuntimeError(r"C:\secret\engine.exe token=hunter2")

        message = analysis_errors.safe_message(leaky)

        assert message == "Analysis failed unexpectedly."
        assert "secret" not in message and "hunter2" not in message

    def test_engine_messages_are_preserved(self) -> None:
        """They are written by this application, so they are safe and useful."""
        assert "invalid position" in analysis_errors.safe_message(
            InvalidPositionError("Refusing to analyze an invalid position: bad")
        )


class TestRetryPolicy:
    def test_a_deterministic_failure_is_not_retried(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Invalid stored data would fail identically forever."""
        game = _game(client, db_session)
        job = _queue(db_session, game, max_attempts=3)
        game.move_count = game.move_count + 5
        db_session.flush()

        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        db_session.refresh(job)

        assert outcome.result == FAILED
        assert job.status == JOB_FAILED
        assert job.attempt_count == 1  # not retried despite attempts remaining

    def test_a_transient_failure_is_retried_until_attempts_run_out(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game, max_attempts=2)

        first = runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        db_session.refresh(job)
        assert first.result == REQUEUED
        assert job.status == JOB_QUEUED

        second = runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        db_session.refresh(job)

        assert second.result == FAILED
        assert job.status == JOB_FAILED
        assert job.attempt_count == 2

    def test_a_retried_job_can_succeed(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """The whole justification for retrying: the second attempt works."""
        game = _game(client, db_session)
        job = _queue(db_session, game)
        expected = game.move_count + 1

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        db_session.refresh(job)

        assert outcome.result == COMPLETED
        assert job.status == JOB_COMPLETE
        assert job.attempt_count == 2
        assert _evaluation_count(db_session, job.id) == expected

    def test_each_attempt_keeps_its_own_run(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        runs = db_session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.analysis_job_id == job.id)
            .order_by(AnalysisRun.attempt_number)
        ).scalars().all()

        assert [r.attempt_number for r in runs] == [1, 2]
        assert [r.status for r in runs] == [STATUS_FAILED, STATUS_COMPLETE]

    def test_a_retry_delay_holds_the_job_back(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)
        delayed = WorkerConfig(
            worker_id=WORKER,
            lease_seconds=60,
            poll_seconds=0.01,
            depth=8,
            heartbeat_seconds=0.0,
            retry_delay_seconds=300,
        )

        runner.run_once(
            config=delayed,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        db_session.refresh(job)
        assert job.status == JOB_QUEUED

        # Not claimable yet, so a crash loop cannot spin.
        assert (
            runner.run_once(
                config=delayed, session_factory=session_factory, engine_factory=FakeEngine
            ).result
            != COMPLETED
        )


class TestCancellation:
    def test_cancelling_a_queued_job_stops_it_ever_running(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        jobs_repo.cancel_job(db_session, job_id=job.id)

        def _fail() -> FakeEngine:  # pragma: no cover - must never run
            raise AssertionError("a cancelled job was claimed")

        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=_fail
        )
        db_session.refresh(job)

        assert outcome.result == "idle"
        assert job.status == JOB_CANCELLED
        assert _evaluation_count(db_session, job.id) == 0

    def test_cancelling_a_running_job_stops_at_a_position_boundary(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Stopping mid-position is impossible and stopping late is wasteful.
        The worker checks between positions, which is also what guarantees no
        half-written evaluation."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        def cancel_partway(call_number: int) -> None:
            if call_number == 2:
                jobs_repo.cancel_job(db_session, job_id=job.id)

        engine = FakeEngine(on_evaluate=cancel_partway)
        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=lambda: engine
        )
        db_session.refresh(job)

        assert outcome.result == CANCELLED
        assert job.status == JOB_CANCELLED
        assert engine.call_count < game.move_count + 1
        assert engine.closed is True

    def test_a_cancelled_run_stores_no_evaluations(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Invariant 5 again, for the cancellation path."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(
                on_evaluate=lambda call: (
                    jobs_repo.cancel_job(db_session, job_id=job.id) if call == 2 else None
                )
            ),
        )

        assert _evaluation_count(db_session, job.id) == 0

    def test_a_cancelled_run_is_recorded_as_cancelled_not_failed(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Nothing went wrong - calling it a failure would misinform the user."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(
                on_evaluate=lambda call: (
                    jobs_repo.cancel_job(db_session, job_id=job.id) if call == 2 else None
                )
            ),
        )
        run = jobs_repo.latest_run_for_job(db_session, job.id)

        assert run.status == STATUS_CANCELLED
        assert run.status != STATUS_FAILED
        assert run.completed_at is not None

    def test_cancellation_releases_the_lease(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(
                on_evaluate=lambda call: (
                    jobs_repo.cancel_job(db_session, job_id=job.id) if call == 2 else None
                )
            ),
        )
        db_session.refresh(job)

        assert job.claimed_by is None
        assert job.lease_expires_at is None
        assert job.completed_positions == 0

    def test_a_cancelled_job_is_not_retried_automatically(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Cancellation is a decision, not a fault. Only the user reverses it."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(
                on_evaluate=lambda call: (
                    jobs_repo.cancel_job(db_session, job_id=job.id) if call == 2 else None
                )
            ),
        )
        db_session.refresh(job)
        assert job.status == JOB_CANCELLED

        assert (
            runner.run_once(
                config=config, session_factory=session_factory, engine_factory=FakeEngine
            ).result
            == "idle"
        )


class TestManualRetry:
    def test_a_failed_job_can_be_retried_and_then_succeed(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game, max_attempts=1)
        expected = game.move_count + 1

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        db_session.refresh(job)
        assert job.status == JOB_FAILED

        jobs_repo.retry_job(db_session, job_id=job.id)
        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        db_session.refresh(job)

        assert outcome.result == COMPLETED
        assert job.status == JOB_COMPLETE
        assert _evaluation_count(db_session, job.id) == expected

    def test_a_cancelled_job_can_be_retried(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(
                on_evaluate=lambda call: (
                    jobs_repo.cancel_job(db_session, job_id=job.id) if call == 2 else None
                )
            ),
        )
        db_session.refresh(job)
        assert job.status == JOB_CANCELLED

        jobs_repo.retry_job(db_session, job_id=job.id)
        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        db_session.refresh(job)

        assert outcome.result == COMPLETED
        assert job.status == JOB_COMPLETE
        assert job.cancel_requested_at is None

    def test_manual_retry_does_not_rewrite_history(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Old attempts keep their own runs and statuses."""
        game = _game(client, db_session)
        job = _queue(db_session, game, max_attempts=1)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=2),
        )
        first_run = jobs_repo.latest_run_for_job(db_session, job.id)
        first_run_id = first_run.id

        jobs_repo.retry_job(db_session, job_id=job.id)
        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        original = db_session.get(AnalysisRun, first_run_id)
        assert original.status == STATUS_FAILED
        assert original.attempt_number == 1
        assert jobs_repo.latest_run_for_job(db_session, job.id).id != first_run_id
