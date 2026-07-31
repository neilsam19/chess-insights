"""Worker behaviour: one claim, one game, one committed result.

Uses the rollback fixture with an injected session factory, so nothing is left
in the developer's database. The tests that need genuinely independent
connections live in test_worker_concurrency.py.
"""

import uuid
from contextlib import contextmanager

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
from app.models.analysis_run import STATUS_COMPLETE, STATUS_FAILED, AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis_jobs as jobs_repo
from app.services.engine import EngineUnavailableError
from app.worker import runner
from app.worker.runner import (
    CLAIMED_NOTHING,
    COMPLETED,
    DISCARDED,
    FAILED,
    REQUEUED,
    WorkerConfig,
)
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
    return WorkerConfig(worker_id=WORKER, lease_seconds=60, poll_seconds=0.01, depth=8)


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
    # The shortest game keeps these tests fast without weakening them.
    return min(games, key=lambda g: g.move_count)


def _queue(db_session, game: Game, **overrides) -> AnalysisJob:
    batch = jobs_repo.create_batch(
        db_session,
        upload_id=game.upload_id,
        engine_config_hash="e" * 64,
        requested_depth=8,
        engine_options={"Threads": 1, "Hash": 64},
    )
    return jobs_repo.enqueue_job(
        db_session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=uuid.uuid4().hex,
        total_positions=game.move_count + 1,
        max_attempts=overrides.pop("max_attempts", 2),
    )


def _evaluations(db_session, run_id: uuid.UUID) -> int:
    return db_session.execute(
        select(func.count())
        .select_from(PositionEvaluation)
        .where(PositionEvaluation.analysis_run_id == run_id)
    ).scalar_one()


def _run_for(db_session, job_id: uuid.UUID) -> AnalysisRun | None:
    return jobs_repo.latest_run_for_job(db_session, job_id)


class TestEmptyQueue:
    def test_nothing_queued_is_not_an_error(self, session_factory, config) -> None:
        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        assert outcome.result == CLAIMED_NOTHING
        assert outcome.job_id is None

    def test_an_empty_queue_never_starts_an_engine(self, session_factory, config) -> None:
        """Polling must be cheap - the engine is only started for real work."""

        def _fail() -> FakeEngine:  # pragma: no cover - must never run
            raise AssertionError("the worker started an engine with nothing queued")

        assert (
            runner.run_once(
                config=config, session_factory=session_factory, engine_factory=_fail
            ).result
            == CLAIMED_NOTHING
        )


class TestSuccess:
    def test_a_job_produces_one_evaluation_per_position(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)
        expected = game.move_count + 1

        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        assert outcome.result == COMPLETED
        assert outcome.job_id == job.id

        db_session.refresh(job)
        assert job.status == JOB_COMPLETE
        assert job.completed_positions == expected
        assert job.completed_at is not None

        run = _run_for(db_session, job.id)
        assert run is not None
        assert run.status == STATUS_COMPLETE
        assert run.attempt_number == 1
        assert _evaluations(db_session, run.id) == expected

    def test_evaluations_are_stored_in_ply_order(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        run = _run_for(db_session, job.id)
        plies = db_session.execute(
            select(PositionEvaluation.ply)
            .where(PositionEvaluation.analysis_run_id == run.id)
            .order_by(PositionEvaluation.ply)
        ).scalars().all()

        assert plies == list(range(game.move_count + 1))

    def test_the_lease_is_released_on_completion(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        db_session.refresh(job)

        assert job.claimed_by is None
        assert job.lease_expires_at is None

    def test_the_configured_depth_reaches_the_engine(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        _queue(db_session, game)
        engine = FakeEngine()

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=lambda: engine
        )

        assert set(engine.depths) == {config.depth}

    def test_the_engine_is_closed_afterwards(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        _queue(db_session, game)
        engine = FakeEngine()

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=lambda: engine
        )

        assert engine.closed is True

    def test_a_second_pass_finds_nothing_left(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        _queue(db_session, game)

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )
        second = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=FakeEngine
        )

        assert second.result == CLAIMED_NOTHING


class TestFailure:
    def test_a_mid_game_engine_failure_stores_no_evaluations(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Invariant 5. A failed attempt must leave nothing that could be
        mistaken for usable analysis - whether or not it will be retried."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        outcome = runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=3),
        )

        # A crashed engine is transient, so with attempts left this requeues
        # rather than failing outright.
        assert outcome.result == REQUEUED
        assert outcome.error_code == "engine_crashed"

        db_session.refresh(job)
        assert job.status == JOB_QUEUED
        assert job.error_code == "engine_crashed"
        assert job.claimed_by is None
        assert job.completed_positions == 0

        run = _run_for(db_session, job.id)
        assert run.status == STATUS_FAILED
        assert _evaluations(db_session, run.id) == 0

    def test_a_crash_on_the_last_attempt_fails_terminally(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Retryable does not mean infinite - max_attempts is what stops it."""
        game = _game(client, db_session)
        job = _queue(db_session, game, max_attempts=1)

        outcome = runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(fail_on_call=3),
        )

        assert outcome.result == FAILED

        db_session.refresh(job)
        assert job.status == JOB_FAILED
        assert job.error_code == "engine_crashed"
        assert job.attempt_count == 1

    def test_an_unstartable_engine_creates_no_run(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """A misconfigured server is not a failed analysis - there is nothing
        to record provenance for, because no engine ever ran."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        def _unavailable():
            raise EngineUnavailableError("The configured engine executable does not exist.")

        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=_unavailable
        )

        assert outcome.result == FAILED
        assert outcome.error_code == "engine_unavailable"

        db_session.refresh(job)
        assert job.status == JOB_FAILED
        assert _run_for(db_session, job.id) is None

    def test_invalid_stored_data_never_reaches_the_engine(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)
        # Corrupt the stored move count so validation rejects the game.
        game.move_count = game.move_count + 5
        db_session.flush()

        def _fail() -> FakeEngine:  # pragma: no cover - must never run
            raise AssertionError("the worker started an engine for invalid data")

        outcome = runner.run_once(
            config=config, session_factory=session_factory, engine_factory=_fail
        )

        assert outcome.result == FAILED
        assert outcome.error_code == "invalid_game_data"

        db_session.refresh(job)
        assert job.status == JOB_FAILED
        assert _run_for(db_session, job.id) is None

    def test_a_failure_message_does_not_leak_internals(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)

        class _Leaky:
            def identify(self):
                raise RuntimeError(r"C:\secret\path\engine.exe refused token=hunter2")

            def evaluate(self, fen, depth):  # pragma: no cover
                raise AssertionError

            def close(self) -> None:
                pass

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=_Leaky
        )
        db_session.refresh(job)

        assert job.error_message == "Analysis failed unexpectedly."
        assert "secret" not in (job.error_message or "")
        assert "hunter2" not in (job.error_message or "")
        assert job.error_code == "unexpected_error"


class TestOwnership:
    def test_results_are_discarded_when_the_lease_is_lost(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """At-least-once execution is only safe because the loser writes
        nothing. Ownership changes here on the third evaluation."""
        game = _game(client, db_session)
        job = _queue(db_session, game)

        def steal(call_number: int) -> None:
            if call_number == 3:
                db_session.execute(
                    AnalysisJob.__table__.update()
                    .where(AnalysisJob.id == job.id)
                    .values(claimed_by="another-worker")
                )
                db_session.flush()

        outcome = runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(on_evaluate=steal),
        )

        assert outcome.result == DISCARDED

        db_session.refresh(job)
        assert job.status == JOB_RUNNING
        assert job.claimed_by == "another-worker"

        run = _run_for(db_session, job.id)
        assert _evaluations(db_session, run.id) == 0

    def test_a_stolen_job_stops_the_engine_early(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        """Losing the lease should stop the analysis, not merely discard it at
        the end - otherwise a reclaimed job burns engine time twice over."""
        game = _game(client, db_session)
        job = _queue(db_session, game)
        engine = FakeEngine(
            on_evaluate=lambda call: (
                db_session.execute(
                    AnalysisJob.__table__.update()
                    .where(AnalysisJob.id == job.id)
                    .values(claimed_by="another-worker")
                )
                if call == 2
                else None
            )
        )

        runner.run_once(
            config=config, session_factory=session_factory, engine_factory=lambda: engine
        )

        assert engine.call_count < game.move_count + 1
        assert engine.closed is True


class TestProgress:
    def test_progress_is_recorded_as_the_game_is_analyzed(
        self, client: TestClient, db_session, session_factory, config
    ) -> None:
        game = _game(client, db_session)
        job = _queue(db_session, game)
        seen: list[int] = []

        def observe(call_number: int) -> None:
            db_session.refresh(job)
            seen.append(job.completed_positions)

        runner.run_once(
            config=config,
            session_factory=session_factory,
            engine_factory=lambda: FakeEngine(on_evaluate=observe),
        )

        # Progress climbs during the run rather than jumping at the end.
        assert seen == sorted(seen)
        assert seen[-1] > 0
        assert max(seen) < game.move_count + 1


class TestConfiguration:
    def test_a_worker_id_is_derived_when_unset(self) -> None:
        worker_id = runner.default_worker_id()

        assert worker_id
        assert str(__import__("os").getpid()) in worker_id

    def test_build_config_prefers_explicit_arguments(self) -> None:
        built = runner.build_config(worker_id="explicit", lease_seconds=5, depth=3)

        assert built.worker_id == "explicit"
        assert built.lease_seconds == 5
        assert built.depth == 3
