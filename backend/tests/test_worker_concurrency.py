"""Worker tests that need genuinely independent database connections.

The claim happens on the worker's own session, so proving "no transaction is
held while the engine runs" requires observing that session's row from a
different connection at the moment the engine is thinking. The shared rollback
fixture cannot express that - see tests/committed.py.
"""

import time
import uuid

from sqlalchemy import select, text

from app.models.analysis_job import JOB_COMPLETE, JOB_QUEUED, JOB_RUNNING, AnalysisJob
from app.repositories import analysis_jobs as jobs_repo
from app.worker import runner
from app.worker.runner import COMPLETED, WorkerConfig
from app.worker.sweeper import recover_stale_jobs
from fake_engine import FakeEngine

CONFIG = WorkerConfig(worker_id="committed-worker", lease_seconds=60, poll_seconds=0.01, depth=4)
OBSERVER_TIMEOUT_MS = 3000


def _assert_no_foreign_queued_jobs(session, own_ids: set[uuid.UUID]) -> None:
    from sqlalchemy import func

    foreign = session.execute(
        select(func.count())
        .select_from(AnalysisJob)
        .where(AnalysisJob.status == JOB_QUEUED, AnalysisJob.id.notin_(own_ids))
    ).scalar_one()
    assert foreign == 0, (
        f"{foreign} queued job(s) exist that this test did not create. "
        "A previous run probably crashed before cleanup."
    )


def _queue_real_game(committed_db, session, move_count: int = 6):
    game = committed_db.create_game_with_moves(session, move_count=move_count)
    batch = committed_db.create_batch(session, upload_id=game.upload_id)
    job = jobs_repo.enqueue_job(
        session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=uuid.uuid4().hex,
        total_positions=move_count + 1,
    )
    session.commit()
    _assert_no_foreign_queued_jobs(session, {job.id})
    return game, job


def test_no_database_transaction_is_held_while_the_engine_runs(committed_db) -> None:
    """Invariant 4, observed rather than asserted.

    While the engine is "thinking", an unrelated connection must be able to
    lock the job row immediately. If the worker were holding its claim
    transaction open, this NOWAIT lock would fail.
    """
    setup = committed_db.session()
    game, job = _queue_real_game(committed_db, setup)

    observer = committed_db.session()
    observer.execute(text(f"SET statement_timeout = {OBSERVER_TIMEOUT_MS}"))
    observations: list[tuple[str, int]] = []

    def look(call_number: int) -> None:
        locked = observer.execute(
            select(AnalysisJob).where(AnalysisJob.id == job.id).with_for_update(nowait=True)
        ).scalars().one()
        observations.append((locked.status, locked.completed_positions))
        observer.rollback()

    outcome = runner.run_once(
        config=CONFIG, engine_factory=lambda: FakeEngine(on_evaluate=look)
    )

    assert outcome.result == COMPLETED
    assert outcome.job_id == job.id

    # Every observation succeeded, which it could not have if a lock were held.
    assert len(observations) == game.move_count + 1
    assert {status for status, _ in observations} == {JOB_RUNNING}

    # And progress was committed incrementally, not all at the end.
    progress = [completed for _, completed in observations]
    assert progress == sorted(progress)
    assert progress[-1] > 0


def test_a_position_slower_than_the_lease_does_not_expire_it(committed_db) -> None:
    """The reason lease renewal runs on its own thread.

    Renewing between positions - which is what the obvious implementation does
    - means one slow position can outlast the whole lease and get its own job
    swept up as abandoned while the worker is working perfectly. Here a single
    evaluation takes longer than the entire lease, and the job must still
    finish untouched.
    """
    setup = committed_db.session()
    game, job = _queue_real_game(committed_db, setup, move_count=3)

    lease_seconds = 2
    slow_config = WorkerConfig(
        worker_id="slow-worker",
        lease_seconds=lease_seconds,
        poll_seconds=0.01,
        depth=4,
        heartbeat_seconds=0.25,
    )

    swept: list = []

    def stall(call_number: int) -> None:
        if call_number != 1:
            return
        # Longer than the whole lease. Without background renewal the sweep
        # below would find this job abandoned.
        time.sleep(lease_seconds + 1.0)
        swept.extend(recover_stale_jobs(retry_delay_seconds=0))

    outcome = runner.run_once(
        config=slow_config, engine_factory=lambda: FakeEngine(on_evaluate=stall)
    )

    assert outcome.result == COMPLETED
    assert [r.job_id for r in swept if r.job_id == job.id] == []

    fresh = committed_db.session()
    stored = fresh.get(AnalysisJob, job.id)
    assert stored.status == JOB_COMPLETE
    assert stored.attempt_count == 1
    assert stored.completed_positions == game.move_count + 1


def test_the_renewal_thread_stops_when_the_job_does(committed_db) -> None:
    """A finished job must not have a thread still renewing its lease - that
    would keep a completed job looking owned forever."""
    setup = committed_db.session()
    _, job = _queue_real_game(committed_db, setup, move_count=3)

    outcome = runner.run_once(
        config=WorkerConfig(
            worker_id="tidy-worker",
            lease_seconds=60,
            poll_seconds=0.01,
            depth=4,
            heartbeat_seconds=0.1,
        ),
        engine_factory=FakeEngine,
    )
    assert outcome.result == COMPLETED

    fresh = committed_db.session()
    released = fresh.get(AnalysisJob, job.id)
    assert released.lease_expires_at is None
    assert released.claimed_by is None

    # Several heartbeat intervals later, nothing has written to the row again.
    time.sleep(0.4)
    fresh.expire_all()
    still_released = fresh.get(AnalysisJob, job.id)
    assert still_released.lease_expires_at is None
    assert still_released.claimed_by is None


def test_a_worker_commits_its_result_for_another_connection_to_see(committed_db) -> None:
    """A restarted API process must see the finished analysis. Nothing about
    the result may depend on the worker's session still existing."""
    setup = committed_db.session()
    game, job = _queue_real_game(committed_db, setup)

    outcome = runner.run_once(config=CONFIG, engine_factory=FakeEngine)
    assert outcome.result == COMPLETED

    fresh = committed_db.session()
    stored = fresh.get(AnalysisJob, job.id)

    assert stored.status == JOB_COMPLETE
    assert stored.completed_positions == game.move_count + 1
    assert stored.claimed_by is None

    run = jobs_repo.latest_run_for_job(fresh, job.id)
    assert run is not None
    assert len(run.evaluations) == game.move_count + 1
