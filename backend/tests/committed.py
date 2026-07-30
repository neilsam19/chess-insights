"""Real, committed database access - for concurrency tests ONLY.

READ THIS BEFORE USING IT.

Every other test in this suite uses the `db_session` fixture in `conftest.py`,
which binds everything to one connection inside one outer transaction and rolls
it back at teardown. That is the right default and nothing here changes it.

It cannot, however, test the claim protocol. `FOR UPDATE SKIP LOCKED` skips
rows locked by *other* transactions. Two "competing workers" built on the
shared-transaction fixture are the same transaction, so both would claim the
same job and the test would pass while proving nothing - a green test that
verifies the opposite of what it claims.

So concurrency tests need genuinely independent connections, which means real
commits, which means this file has to clean up after itself explicitly.

Rules for anything using `CommittedWorld`:

  * Create data only through its helpers, so every row is tracked.
  * Never touch rows the test did not create. Assertions are always scoped to
    tracked ids - never to global counts, which is what broke Sprint 2's tests.
  * Cleanup runs in a `finally`, so a failed assertion still tidies up.
  * `assert_clean()` verifies teardown actually worked, because a leak here
    silently pollutes the developer's real database.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.analysis_batch import AnalysisBatch
from app.models.analysis_job import AnalysisJob
from app.models.analysis_run import AnalysisRun
from app.models.game import Game
from app.models.upload import Upload

# Deliberately minimal fixtures: claim tests need a game row to point at, not a
# realistic game. Importing a PGN would commit thousands of move rows for no
# benefit and make cleanup slower and riskier.
STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class CommittedWorld:
    """Hands out independent sessions and remembers what to delete."""

    def __init__(self) -> None:
        self._sessions: list[Session] = []
        self._upload_ids: list[uuid.UUID] = []
        self._batch_ids: list[uuid.UUID] = []

    # --- sessions ---------------------------------------------------------

    def session(self) -> Session:
        """A new session on its own connection.

        Two sessions from here are two real transactions, which is the entire
        reason this class exists.
        """
        session = SessionLocal()
        self._sessions.append(session)
        return session

    # --- data -------------------------------------------------------------

    def create_game(self, session: Session, *, move_count: int = 10) -> Game:
        """One upload with one game, committed. Both are tracked for cleanup."""
        marker = uuid.uuid4().hex
        upload = Upload(
            original_filename=f"committed-{marker}.pgn",
            # Unique per call: uploads are unique on (file_sha256, player) and
            # these rows really are committed, so a fixed value would collide
            # with itself across tests.
            file_sha256=marker,
            player_name="ConcurrencyTest",
            normalized_player_name=f"concurrencytest-{marker}",
            uploaded_at=datetime.now(timezone.utc),
            game_count=1,
            warnings=[],
        )
        session.add(upload)
        session.flush()

        game = Game(
            upload_id=upload.id,
            game_index=0,
            white_name="ConcurrencyTest",
            black_name="Opponent",
            result="1-0",
            user_color="white",
            user_result="win",
            move_count=move_count,
            raw_pgn="[Event \"Committed fixture\"]\n\n1. e4 e5 1-0\n",
            game_hash=marker,
        )
        session.add(game)
        session.commit()

        self._upload_ids.append(upload.id)
        return game

    def create_batch(self, session: Session, *, upload_id: uuid.UUID | None = None):
        from app.repositories import analysis_jobs as jobs_repo

        batch = jobs_repo.create_batch(
            session,
            upload_id=upload_id,
            engine_config_hash="e" * 64,
            requested_depth=12,
            engine_options={"Threads": 1, "Hash": 64},
        )
        session.commit()
        self._batch_ids.append(batch.id)
        return batch

    def track_batch(self, batch_id: uuid.UUID) -> None:
        self._batch_ids.append(batch_id)

    # --- teardown ---------------------------------------------------------

    def cleanup(self) -> None:
        for session in self._sessions:
            try:
                session.rollback()
                session.close()
            except Exception:  # noqa: BLE001 - teardown must not mask a failure
                pass

        with SessionLocal() as session:
            # Order matters: batches first, because a single-game batch has no
            # upload to cascade from.
            if self._batch_ids:
                session.execute(
                    delete(AnalysisBatch).where(AnalysisBatch.id.in_(self._batch_ids))
                )
            if self._upload_ids:
                session.execute(delete(Upload).where(Upload.id.in_(self._upload_ids)))
            session.commit()

    def assert_clean(self) -> None:
        """Prove teardown worked. A leak here pollutes the real dev database."""
        with SessionLocal() as session:
            for model, ids in (
                (AnalysisBatch, self._batch_ids),
                (Upload, self._upload_ids),
            ):
                if not ids:
                    continue
                remaining = session.execute(
                    select(func.count()).select_from(model).where(model.id.in_(ids))
                ).scalar_one()
                assert remaining == 0, f"{model.__name__} rows survived teardown"

            if self._upload_ids:
                orphan_jobs = session.execute(
                    select(func.count())
                    .select_from(AnalysisJob)
                    .join(Game, Game.id == AnalysisJob.game_id)
                    .where(Game.upload_id.in_(self._upload_ids))
                ).scalar_one()
                assert orphan_jobs == 0, "AnalysisJob rows survived teardown"

                orphan_runs = session.execute(
                    select(func.count())
                    .select_from(AnalysisRun)
                    .join(Game, Game.id == AnalysisRun.game_id)
                    .where(Game.upload_id.in_(self._upload_ids))
                ).scalar_one()
                assert orphan_runs == 0, "AnalysisRun rows survived teardown"
