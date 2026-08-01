"""Bounded concurrency: the ceiling on simultaneous engine processes.

The whole point of a queue here is that submitting 500 games starts the
configured number of engines, not 500. These tests use real connections
because slots are real threads claiming independently - the shared rollback
session cannot express that.
"""

import threading
import time
import uuid

from sqlalchemy import select

from app.models.analysis_job import JOB_COMPLETE, AnalysisJob
from app.repositories import analysis_jobs as jobs_repo
from app.worker import runner
from app.worker.runner import build_config, run_forever
from fake_engine import FakeEngine


class CountingEngineFactory:
    """Builds engines while recording how many exist at once.

    `peak` is the number that mattered: the highest count of simultaneously
    open engines observed across the whole run.
    """

    def __init__(self, *, hold_seconds: float = 0.05) -> None:
        self._lock = threading.Lock()
        self._hold = hold_seconds
        self.live = 0
        self.peak = 0
        self.built = 0

    def __call__(self) -> FakeEngine:
        with self._lock:
            self.live += 1
            self.built += 1
            self.peak = max(self.peak, self.live)

        factory = self

        class _Tracked(FakeEngine):
            def evaluate(self, fen: str, depth: int):
                # Hold the engine open long enough for slots to overlap; without
                # this the fake finishes so fast that concurrency never shows.
                time.sleep(factory._hold)
                return super().evaluate(fen, depth)

            def close(self) -> None:
                with factory._lock:
                    factory.live -= 1
                super().close()

        return _Tracked()


def _queue_games(committed_db, session, count: int, *, move_count: int = 2):
    jobs = []
    for _ in range(count):
        game = committed_db.create_game_with_moves(session, move_count=move_count)
        batch = committed_db.create_batch(session, upload_id=game.upload_id)
        jobs.append(
            jobs_repo.enqueue_job(
                session,
                batch_id=batch.id,
                game_id=game.id,
                config_hash=uuid.uuid4().hex,
                total_positions=move_count + 1,
            )
        )
    session.commit()
    return jobs


def _drain(config, engine_factory, *, timeout: float = 20.0) -> None:
    """Run the worker until the queue is empty, then stop it gracefully."""
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever,
        kwargs={"config": config, "engine_factory": engine_factory, "stop": stop},
    )
    thread.start()
    time.sleep(timeout)
    stop.set()
    thread.join(timeout=15)
    assert not thread.is_alive(), "worker did not shut down"


def test_concurrency_one_never_runs_two_engines(committed_db) -> None:
    setup = committed_db.session()
    jobs = _queue_games(committed_db, setup, 4)

    factory = CountingEngineFactory()
    _drain(
        build_config(worker_id=f"solo-{uuid.uuid4().hex[:8]}", concurrency=1),
        factory,
        timeout=3.0,
    )

    assert factory.peak == 1, f"ran {factory.peak} engines at once with concurrency=1"
    _assert_all_complete(committed_db, jobs)


def test_concurrency_is_capped_by_configuration(committed_db) -> None:
    """More queued work must not mean more engines."""
    setup = committed_db.session()
    jobs = _queue_games(committed_db, setup, 8)

    factory = CountingEngineFactory()
    _drain(
        build_config(worker_id=f"pair-{uuid.uuid4().hex[:8]}", concurrency=2),
        factory,
        timeout=4.0,
    )

    assert factory.peak <= 2, f"ran {factory.peak} engines at once with concurrency=2"
    _assert_all_complete(committed_db, jobs)


def test_slots_do_not_claim_the_same_job(committed_db) -> None:
    """Each slot owns its lease under a distinct id, so no job is analysed
    twice and every job is analysed once."""
    setup = committed_db.session()
    jobs = _queue_games(committed_db, setup, 6)

    factory = CountingEngineFactory()
    _drain(
        build_config(worker_id=f"shared-{uuid.uuid4().hex[:8]}", concurrency=3),
        factory,
        timeout=4.0,
    )

    check = committed_db.session()
    for job in jobs:
        stored = check.get(AnalysisJob, job.id)
        assert stored.status == JOB_COMPLETE
        # Exactly one attempt each: a double claim would show up as a retry.
        assert stored.attempt_count == 1

        runs = check.execute(
            select(runner.analysis_repo.AnalysisRun).where(
                runner.analysis_repo.AnalysisRun.analysis_job_id == job.id
            )
        ).scalars().all()
        assert len(runs) == 1


def test_one_failing_slot_does_not_stop_the_others(committed_db) -> None:
    """A slot that blows up must not take the worker's capacity with it."""
    setup = committed_db.session()
    jobs = _queue_games(committed_db, setup, 4)

    calls = {"n": 0}
    lock = threading.Lock()
    factory = CountingEngineFactory()

    def flaky():
        with lock:
            calls["n"] += 1
            should_fail = calls["n"] == 1
        if should_fail:
            raise RuntimeError("simulated catastrophic slot failure")
        return factory()

    _drain(
        build_config(worker_id=f"flaky-{uuid.uuid4().hex[:8]}", concurrency=2),
        flaky,
        timeout=5.0,
    )

    check = committed_db.session()
    completed = sum(
        1 for job in jobs if check.get(AnalysisJob, job.id).status == JOB_COMPLETE
    )
    assert completed >= len(jobs) - 1, "a single slot failure stalled the worker"


def test_graceful_shutdown_finishes_the_current_game(committed_db) -> None:
    """Stopping should not abandon work that is nearly done."""
    setup = committed_db.session()
    jobs = _queue_games(committed_db, setup, 1, move_count=4)

    factory = CountingEngineFactory(hold_seconds=0.1)
    config = build_config(worker_id=f"tidy-{uuid.uuid4().hex[:8]}", concurrency=1)

    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever,
        kwargs={"config": config, "engine_factory": factory, "stop": stop},
    )
    thread.start()

    # Ask it to stop while the single game is still being analysed.
    time.sleep(0.25)
    stop.set()
    thread.join(timeout=15)

    assert not thread.is_alive()
    check = committed_db.session()
    stored = check.get(AnalysisJob, jobs[0].id)
    assert stored.status == JOB_COMPLETE
    assert stored.claimed_by is None
    # And no engine was left running.
    assert factory.live == 0


def _assert_all_complete(committed_db, jobs) -> None:
    check = committed_db.session()
    for job in jobs:
        assert check.get(AnalysisJob, job.id).status == JOB_COMPLETE
