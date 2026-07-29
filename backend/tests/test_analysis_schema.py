"""Database-enforced invariants for analysis batches and jobs.

These deliberately test the *database*, not service code. Service checks are
convenience; the constraints here are what make two concurrent enqueues safe
and what stop a bad progress value being stored at all.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.models.analysis_batch import AnalysisBatch, BATCH_QUEUED
from app.models.analysis_job import (
    JOB_CANCELLED,
    JOB_COMPLETE,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    AnalysisJob,
)
from app.models.analysis_run import STATUS_COMPLETE, AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.models.upload import Upload
from fake_engine import FakeEngine
from sample_data import SAMPLE_PGN_BYTES

IDENTITY = FakeEngine().identify()


def _saved_upload(client: TestClient, player_name: str = "NeilSamant") -> uuid.UUID:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def _first_game(db_session, upload_id: uuid.UUID) -> Game:
    return db_session.execute(
        select(Game).where(Game.upload_id == upload_id, Game.game_index == 0)
    ).scalar_one()


def _batch(db_session, upload_id: uuid.UUID | None = None) -> AnalysisBatch:
    batch = AnalysisBatch(
        upload_id=upload_id,
        status=BATCH_QUEUED,
        engine_config_hash="e" * 64,
        requested_depth=12,
        engine_options={"Threads": 1, "Hash": 64},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(batch)
    db_session.flush()
    return batch


def _job(db_session, batch: AnalysisBatch, game: Game, **overrides) -> AnalysisJob:
    now = datetime.now(timezone.utc)
    fields = {
        "batch_id": batch.id,
        "game_id": game.id,
        "status": JOB_QUEUED,
        "config_hash": "c" * 64,
        "attempt_count": 0,
        "max_attempts": 2,
        "available_at": now,
        "created_at": now,
        "total_positions": game.move_count + 1,
        "completed_positions": 0,
    }
    job = AnalysisJob(**{**fields, **overrides})
    db_session.add(job)
    db_session.flush()
    return job


class TestProgressConstraints:
    def test_negative_progress_is_rejected(self, client: TestClient, db_session) -> None:
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        with pytest.raises(IntegrityError):
            _job(db_session, batch, game, completed_positions=-1)

    def test_progress_beyond_total_is_rejected(self, client: TestClient, db_session) -> None:
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        with pytest.raises(IntegrityError):
            _job(
                db_session,
                batch,
                game,
                total_positions=10,
                completed_positions=11,
            )

    def test_full_progress_is_allowed(self, client: TestClient, db_session) -> None:
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        job = _job(db_session, batch, game, total_positions=10, completed_positions=10)

        assert job.completed_positions == job.total_positions

    def test_zero_positions_is_rejected(self, client: TestClient, db_session) -> None:
        """A zero-move game is unanalyzable and must never reach the queue."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        with pytest.raises(IntegrityError):
            _job(db_session, batch, game, total_positions=0)


class TestAttemptConstraints:
    def test_attempts_beyond_the_maximum_are_rejected(
        self, client: TestClient, db_session
    ) -> None:
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        with pytest.raises(IntegrityError):
            _job(db_session, batch, game, attempt_count=3, max_attempts=2)

    def test_zero_max_attempts_is_rejected(self, client: TestClient, db_session) -> None:
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        with pytest.raises(IntegrityError):
            _job(db_session, batch, game, max_attempts=0)


class TestActiveJobUniqueness:
    """The partial unique index is what makes concurrent enqueues safe."""

    @pytest.mark.parametrize(
        "first_status,second_status",
        [
            (JOB_QUEUED, JOB_QUEUED),
            (JOB_QUEUED, JOB_RUNNING),
            (JOB_RUNNING, JOB_QUEUED),
            (JOB_RUNNING, JOB_RUNNING),
        ],
    )
    def test_two_active_jobs_for_one_game_and_config_are_rejected(
        self, client: TestClient, db_session, first_status, second_status
    ) -> None:
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        _job(db_session, batch, game, status=first_status)

        with pytest.raises(IntegrityError):
            _job(db_session, batch, game, status=second_status)

    @pytest.mark.parametrize("terminal", [JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED])
    def test_a_new_job_may_follow_a_terminal_one(
        self, client: TestClient, db_session, terminal
    ) -> None:
        """Retry has to be possible: the index only reserves the active slot."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        _job(db_session, batch, game, status=terminal)
        retried = _job(db_session, batch, game, status=JOB_QUEUED)

        assert retried.id is not None

    def test_two_terminal_jobs_may_coexist(self, client: TestClient, db_session) -> None:
        """Attempt history accumulates and must never be constrained away."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        _job(db_session, batch, game, status=JOB_FAILED)
        _job(db_session, batch, game, status=JOB_FAILED)

        count = db_session.execute(
            select(func.count()).select_from(AnalysisJob).where(AnalysisJob.game_id == game.id)
        ).scalar_one()
        assert count == 2

    def test_a_different_configuration_may_be_active_at_the_same_time(
        self, client: TestClient, db_session
    ) -> None:
        """Analyzing the same game at a different depth is a different job."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)

        _job(db_session, batch, game, config_hash="c" * 64)
        other = _job(db_session, batch, game, config_hash="d" * 64)

        assert other.id is not None

    def test_the_same_configuration_may_be_active_for_different_games(
        self, client: TestClient, db_session
    ) -> None:
        upload_id = _saved_upload(client)
        games = db_session.execute(
            select(Game).where(Game.upload_id == upload_id).order_by(Game.game_index).limit(2)
        ).scalars().all()
        batch = _batch(db_session, upload_id)

        _job(db_session, batch, games[0])
        second = _job(db_session, batch, games[1])

        assert second.id is not None


class TestCascades:
    def test_deleting_an_upload_removes_batches_jobs_runs_and_evaluations(
        self, client: TestClient, db_session
    ) -> None:
        """Issued as a raw DELETE so this exercises the database's ON DELETE
        CASCADE rather than SQLAlchemy's in-Python relationship cascade."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)
        job = _job(db_session, batch, game)

        run = AnalysisRun(
            game_id=game.id,
            analysis_job_id=job.id,
            attempt_number=1,
            status=STATUS_COMPLETE,
            engine_name=IDENTITY.name,
            engine_version=IDENTITY.version,
            engine_binary_sha256=IDENTITY.binary_sha256,
            requested_depth=12,
            engine_options=dict(IDENTITY.options),
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(run)
        db_session.flush()
        db_session.add(
            PositionEvaluation(analysis_run_id=run.id, ply=0, score_cp_white=15)
        )
        db_session.flush()

        batch_id, job_id, run_id = batch.id, job.id, run.id
        db_session.expunge_all()
        db_session.execute(delete(Upload).where(Upload.id == upload_id))
        db_session.flush()

        assert db_session.get(AnalysisBatch, batch_id) is None
        assert db_session.get(AnalysisJob, job_id) is None
        assert db_session.get(AnalysisRun, run_id) is None
        assert (
            db_session.execute(
                select(func.count())
                .select_from(PositionEvaluation)
                .where(PositionEvaluation.analysis_run_id == run_id)
            ).scalar_one()
            == 0
        )

    def test_deleting_a_job_preserves_its_run_history(
        self, client: TestClient, db_session
    ) -> None:
        """ON DELETE SET NULL, not CASCADE: a run is the record of engine work
        that actually happened, and losing it would lose provenance."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)
        job = _job(db_session, batch, game)

        run = AnalysisRun(
            game_id=game.id,
            analysis_job_id=job.id,
            attempt_number=1,
            status=STATUS_COMPLETE,
            engine_name=IDENTITY.name,
            engine_version=IDENTITY.version,
            engine_binary_sha256=IDENTITY.binary_sha256,
            requested_depth=12,
            engine_options=dict(IDENTITY.options),
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(run)
        db_session.flush()
        run_id = run.id

        db_session.expunge_all()
        db_session.execute(delete(AnalysisJob).where(AnalysisJob.id == job.id))
        db_session.flush()

        survivor = db_session.get(AnalysisRun, run_id)
        assert survivor is not None
        assert survivor.analysis_job_id is None
        assert survivor.attempt_number == 1

    def test_a_batch_may_exist_without_an_upload(
        self, client: TestClient, db_session
    ) -> None:
        """Single-game requests still create a batch, unattached to an upload."""
        batch = _batch(db_session, upload_id=None)

        assert batch.upload_id is None


class TestSprint4Compatibility:
    def test_existing_runs_carry_no_job_linkage(self, client: TestClient, db_session) -> None:
        """Runs from the synchronous path predate jobs; both new columns are
        nullable precisely so that history survives the migration."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)

        run = AnalysisRun(
            game_id=game.id,
            status=STATUS_COMPLETE,
            engine_name=IDENTITY.name,
            engine_version=IDENTITY.version,
            engine_binary_sha256=IDENTITY.binary_sha256,
            requested_depth=12,
            engine_options=dict(IDENTITY.options),
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(run)
        db_session.flush()

        assert run.analysis_job_id is None
        assert run.attempt_number is None


class TestClaimOrdering:
    def test_future_availability_is_visible_to_the_claim_index(
        self, client: TestClient, db_session
    ) -> None:
        """`available_at` is what implements a retry delay. Actual claiming is
        Checkpoint 2 - this only confirms the column behaves as a filter."""
        upload_id = _saved_upload(client)
        game = _first_game(db_session, upload_id)
        batch = _batch(db_session, upload_id)
        now = datetime.now(timezone.utc)

        _job(db_session, batch, game, available_at=now + timedelta(minutes=5))

        eligible = db_session.execute(
            select(func.count())
            .select_from(AnalysisJob)
            .where(
                AnalysisJob.batch_id == batch.id,
                AnalysisJob.status == JOB_QUEUED,
                AnalysisJob.available_at <= now,
            )
        ).scalar_one()
        assert eligible == 0
