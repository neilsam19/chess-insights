"""Claim-protocol tests against genuinely concurrent connections.

These use the `committed_db` fixture rather than the usual rollback session.
The reason is in tests/committed.py: two claimers inside one shared
transaction never contend, so the same tests written on the default fixture
would pass whether or not `FOR UPDATE SKIP LOCKED` were there at all.

Every test sets a short `statement_timeout` on the competing session. Without
it, removing `SKIP LOCKED` would make these tests *hang* on a row lock instead
of failing - and a suite that hangs on regression is barely better than one
that passes on it.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.models.analysis_job import JOB_QUEUED, JOB_RUNNING, AnalysisJob
from app.repositories import analysis_jobs as jobs_repo

LEASE_SECONDS = 60
COMPETITOR_TIMEOUT_MS = 3000


def _impatient(session: Session) -> Session:
    """Fail fast instead of waiting on a row lock.

    This is the guard that turns "SKIP LOCKED was removed" from a hang into a
    visible error.
    """
    session.execute(text(f"SET statement_timeout = {COMPETITOR_TIMEOUT_MS}"))
    return session


def _assert_no_foreign_queued_jobs(session: Session, own_ids: set[uuid.UUID]) -> None:
    """The queue is global, so a stray queued job from a crashed earlier run
    would make these tests claim the wrong row. Surface that clearly rather
    than producing a confusing failure."""
    foreign = session.execute(
        select(func.count())
        .select_from(AnalysisJob)
        .where(AnalysisJob.status == JOB_QUEUED, AnalysisJob.id.notin_(own_ids))
    ).scalar_one()
    assert foreign == 0, (
        f"{foreign} queued job(s) exist that this test did not create. "
        "A previous run probably crashed before cleanup; clear analysis_jobs "
        "in the dev database before re-running."
    )


def _queued_job(world, session: Session, **overrides) -> AnalysisJob:
    game = world.create_game(session)
    batch = world.create_batch(session, upload_id=game.upload_id)
    job = jobs_repo.enqueue_job(
        session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=uuid.uuid4().hex,
        total_positions=game.move_count + 1,
        **overrides,
    )
    session.commit()
    return job


def test_two_workers_cannot_claim_the_same_job(committed_db) -> None:
    """The headline invariant. Worker A holds its claim open; worker B must
    skip the locked row and come away with nothing."""
    setup = committed_db.session()
    job = _queued_job(committed_db, setup)
    _assert_no_foreign_queued_jobs(setup, {job.id})
    setup.commit()

    worker_a = committed_db.session()
    worker_b = _impatient(committed_db.session())

    claimed_a = jobs_repo.claim_next_job(worker_a, worker_id="worker-a", lease_seconds=LEASE_SECONDS)
    assert claimed_a is not None
    assert claimed_a.id == job.id

    # A has not committed: the row is locked and B must not block on it.
    claimed_b = jobs_repo.claim_next_job(worker_b, worker_id="worker-b", lease_seconds=LEASE_SECONDS)
    assert claimed_b is None

    worker_a.commit()
    worker_b.rollback()

    check = committed_db.session()
    stored = check.get(AnalysisJob, job.id)
    assert stored.status == JOB_RUNNING
    assert stored.claimed_by == "worker-a"
    assert stored.attempt_count == 1


def test_a_committed_job_is_no_longer_claimable(committed_db) -> None:
    setup = committed_db.session()
    job = _queued_job(committed_db, setup)
    _assert_no_foreign_queued_jobs(setup, {job.id})
    setup.commit()

    worker_a = committed_db.session()
    jobs_repo.claim_next_job(worker_a, worker_id="worker-a", lease_seconds=LEASE_SECONDS)
    worker_a.commit()

    worker_b = _impatient(committed_db.session())
    assert jobs_repo.claim_next_job(worker_b, worker_id="worker-b", lease_seconds=LEASE_SECONDS) is None


def test_two_workers_claim_two_different_jobs(committed_db) -> None:
    """SKIP LOCKED is what makes a second worker useful rather than blocked:
    it moves past the contended row to the next available one."""
    setup = committed_db.session()
    game_one = committed_db.create_game(setup)
    game_two = committed_db.create_game(setup)
    batch = committed_db.create_batch(setup, upload_id=game_one.upload_id)
    first = jobs_repo.enqueue_job(
        setup,
        batch_id=batch.id,
        game_id=game_one.id,
        config_hash=uuid.uuid4().hex,
        total_positions=11,
    )
    second = jobs_repo.enqueue_job(
        setup,
        batch_id=batch.id,
        game_id=game_two.id,
        config_hash=uuid.uuid4().hex,
        total_positions=11,
    )
    setup.commit()
    _assert_no_foreign_queued_jobs(setup, {first.id, second.id})

    worker_a = committed_db.session()
    worker_b = _impatient(committed_db.session())

    claimed_a = jobs_repo.claim_next_job(worker_a, worker_id="worker-a", lease_seconds=LEASE_SECONDS)
    claimed_b = jobs_repo.claim_next_job(worker_b, worker_id="worker-b", lease_seconds=LEASE_SECONDS)

    assert claimed_a is not None and claimed_b is not None
    assert claimed_a.id != claimed_b.id
    assert {claimed_a.id, claimed_b.id} == {first.id, second.id}

    worker_a.commit()
    worker_b.commit()


def test_claiming_releases_its_lock_on_commit(committed_db) -> None:
    """Invariant 4 in miniature: nothing may still be locked once the claim
    transaction ends, because the engine runs after this point."""
    setup = committed_db.session()
    job = _queued_job(committed_db, setup)
    _assert_no_foreign_queued_jobs(setup, {job.id})
    setup.commit()

    worker = committed_db.session()
    jobs_repo.claim_next_job(worker, worker_id="worker-a", lease_seconds=LEASE_SECONDS)

    observer = committed_db.session()

    # While the claim is open the row is genuinely locked...
    with pytest.raises((OperationalError, DBAPIError)):
        observer.execute(
            select(AnalysisJob).where(AnalysisJob.id == job.id).with_for_update(nowait=True)
        )
    observer.rollback()

    worker.commit()

    # ...and immediately afterwards it is not.
    locked = observer.execute(
        select(AnalysisJob).where(AnalysisJob.id == job.id).with_for_update(nowait=True)
    ).scalars().one()
    assert locked.id == job.id
    observer.rollback()


def test_a_worker_that_lost_its_lease_cannot_complete_the_job(committed_db) -> None:
    """At-least-once execution is only safe because a stale worker's final
    write matches nothing."""
    setup = committed_db.session()
    job = _queued_job(committed_db, setup)
    _assert_no_foreign_queued_jobs(setup, {job.id})
    setup.commit()

    worker = committed_db.session()
    jobs_repo.claim_next_job(worker, worker_id="worker-a", lease_seconds=LEASE_SECONDS)
    worker.commit()

    # The sweep reclaims it and someone else picks it up.
    sweeper = committed_db.session()
    assert jobs_repo.requeue_job(
        sweeper, job_id=job.id, worker_id=None, error_code="worker_lease_expired"
    )
    sweeper.commit()

    reclaimed = jobs_repo.claim_next_job(sweeper, worker_id="worker-b", lease_seconds=LEASE_SECONDS)
    assert reclaimed is not None
    sweeper.commit()

    # The original worker finishes late and tries to commit its results.
    assert jobs_repo.complete_job(worker, job_id=job.id, worker_id="worker-a") is False
    worker.commit()

    check = committed_db.session()
    stored = check.get(AnalysisJob, job.id)
    assert stored.status == JOB_RUNNING
    assert stored.claimed_by == "worker-b"
    assert stored.attempt_count == 2
