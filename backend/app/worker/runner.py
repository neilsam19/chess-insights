"""Claim one job, analyze one game, persist the result. Repeat.

The whole point of this process is the transaction shape. Stockfish is slow and
a database transaction must never wait on it, so one job is four short,
independently committed transactions with the engine running strictly between
them:

    1. claim the job and take a lease           (short, committed)
    2. read the game's positions                (short, committed)
    3. record that the run has begun            (short, committed)
       ... analyze, holding no transaction ...
    4. verify ownership, save results, finish   (short, committed)

Step 4 checks ownership *before* writing anything. That is what makes
at-least-once execution safe: a worker whose lease was reclaimed writes
nothing, and its results are discarded rather than overwriting the new owner's.

Scope note: this checkpoint always fails a job terminally. Retry policy,
cancellation, and the stale-lease sweep are Checkpoint 5 and Checkpoint 4 - the
error codes recorded here are already the ones those checkpoints will act on.
"""

import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, ContextManager

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.analysis_job import AnalysisJob
from app.repositories import analysis as analysis_repo
from app.repositories import analysis_jobs as jobs_repo
from app.services.analysis_executor import (
    MAX_ERROR_MESSAGE_LENGTH,
    AnalysisAbandoned,
    GameNotFoundError,
    InvalidGameDataError,
    evaluate_positions,
    load_game_positions,
)
from app.services.engine import (
    ChessEngine,
    EngineError,
    EngineUnavailableError,
    build_stockfish_engine,
    engine_session,
)
from app.worker.lease import LeaseKeeper
from app.worker.sweeper import recover_stale_jobs

logger = logging.getLogger("app.worker")

SessionFactory = Callable[[], ContextManager[Session]]
EngineFactory = Callable[[], ChessEngine]

# Outcomes, for logging and for tests to assert on without inspecting the
# database. These are not persisted anywhere.
CLAIMED_NOTHING = "idle"
COMPLETED = "completed"
FAILED = "failed"
DISCARDED = "discarded"


@dataclass(frozen=True)
class WorkerConfig:
    worker_id: str
    lease_seconds: int
    poll_seconds: float
    depth: int
    # Zero disables the background renewal thread and falls back to renewing
    # once per position. Tests that share a single Session need that, because a
    # Session must not be used from two threads.
    heartbeat_seconds: float = 0.0
    stale_sweep_seconds: float = 30.0
    retry_delay_seconds: int = 5


@dataclass(frozen=True)
class JobOutcome:
    job_id: uuid.UUID | None
    result: str
    error_code: str | None = None


def default_worker_id() -> str:
    """Host and pid, so two workers on one machine still own distinct leases."""
    return f"{socket.gethostname()}-{os.getpid()}"


def build_config(
    *,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    poll_seconds: float | None = None,
    depth: int | None = None,
    heartbeat_seconds: float | None = None,
) -> WorkerConfig:
    return WorkerConfig(
        worker_id=worker_id or settings.analysis_worker_id or default_worker_id(),
        lease_seconds=lease_seconds or settings.analysis_lease_seconds,
        poll_seconds=poll_seconds or settings.analysis_poll_seconds,
        depth=depth or settings.stockfish_depth,
        heartbeat_seconds=(
            heartbeat_seconds
            if heartbeat_seconds is not None
            else settings.analysis_heartbeat_seconds
        ),
        stale_sweep_seconds=settings.analysis_stale_sweep_seconds,
        retry_delay_seconds=settings.analysis_retry_delay_seconds,
    )


def _default_session() -> ContextManager[Session]:
    return SessionLocal()


def classify_error(exc: Exception) -> str:
    """Map a failure onto the error codes in ADR section 6.

    Checkpoint 5 splits `EngineFailureError` so that a crashed engine can be
    told apart from an invalid position or an ambiguous score. Until then every
    engine failure records `engine_crashed`, which is harmless because nothing
    retries automatically yet.
    """
    if isinstance(exc, EngineUnavailableError):
        return "engine_unavailable"
    if isinstance(exc, EngineError):
        return "engine_crashed"
    if isinstance(exc, (InvalidGameDataError, GameNotFoundError)):
        # A game deleted mid-run takes its job with it by cascade, so
        # GameNotFoundError after a claim is effectively unreachable.
        return "invalid_game_data"
    return "unexpected_error"


def safe_message(exc: Exception) -> str:
    """Never leak paths, secrets, or tracebacks into a stored error message.

    Engine and stored-data errors are written by this application and are safe
    to show; anything else is replaced wholesale.
    """
    if isinstance(exc, (EngineError, InvalidGameDataError, GameNotFoundError)):
        message = str(exc) or "Engine analysis failed."
    else:
        message = "Analysis failed unexpectedly."
    return message[:MAX_ERROR_MESSAGE_LENGTH]


def _fail(
    session_factory: SessionFactory,
    *,
    job_id: uuid.UUID,
    run_id: uuid.UUID | None,
    worker_id: str,
    exc: Exception,
) -> JobOutcome:
    """Record a terminal failure on the job and, if one exists, on the run.

    Stores no evaluations. Secondary errors are swallowed: the original failure
    is what matters, not a follow-on database problem while recording it.
    """
    code = classify_error(exc)
    message = safe_message(exc)
    try:
        with session_factory() as db:
            db.rollback()
            jobs_repo.fail_job(
                db, job_id=job_id, worker_id=worker_id, error_code=code, error_message=message
            )
            if run_id is not None:
                run = analysis_repo.get_analysis_run(db, run_id)
                if run is not None:
                    analysis_repo.mark_analysis_run_failed(db, run, message)
            db.commit()
    except Exception:  # noqa: BLE001 - never mask the original failure
        logger.exception("Could not record failure for job %s", job_id)

    logger.warning("job=%s failed code=%s", job_id, code)
    return JobOutcome(job_id=job_id, result=FAILED, error_code=code)


def _claim(
    session_factory: SessionFactory, config: WorkerConfig
) -> tuple[uuid.UUID, uuid.UUID, int] | None:
    """Take a job. Returns (job_id, game_id, attempt) or None if the queue is empty."""
    with session_factory() as db:
        job: AnalysisJob | None = jobs_repo.claim_next_job(
            db, worker_id=config.worker_id, lease_seconds=config.lease_seconds
        )
        if job is None:
            return None

        identifiers = (job.id, job.game_id, job.attempt_count)
        db.commit()
        return identifiers


def run_once(
    *,
    config: WorkerConfig | None = None,
    session_factory: SessionFactory = _default_session,
    engine_factory: EngineFactory = build_stockfish_engine,
) -> JobOutcome:
    """Claim and process at most one job.

    Returns an outcome describing what happened. Never raises for an ordinary
    analysis failure - that is recorded on the job instead, which is the whole
    point of a durable queue.
    """
    config = config or build_config()
    claimed = _claim(session_factory, config)
    if claimed is None:
        return JobOutcome(job_id=None, result=CLAIMED_NOTHING)

    job_id, game_id, attempt = claimed
    logger.info("job=%s claimed attempt=%s worker=%s", job_id, attempt, config.worker_id)

    # --- Read positions. Invalid stored data never reaches the engine. ---
    try:
        # Read-only. Not rolled back explicitly: closing the session discards
        # the transaction anyway, and an explicit rollback would discard a
        # caller-supplied shared session's uncommitted work.
        with session_factory() as db:
            _, positions = load_game_positions(db, game_id)
    except Exception as exc:  # noqa: BLE001 - classified below
        return _fail(
            session_factory, job_id=job_id, run_id=None, worker_id=config.worker_id, exc=exc
        )

    # Renewal runs on its own thread and its own sessions, so a position that
    # takes longer than the whole lease cannot cause its own job to be swept
    # up as abandoned. Progress rides along with it rather than costing a
    # transaction per position.
    keeper = LeaseKeeper(
        session_factory,
        job_id=job_id,
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
        interval_seconds=config.heartbeat_seconds,
        total_positions=len(positions),
    )

    def report_progress(done: int, total: int) -> bool:
        """Publish progress and report whether the job is still ours.

        Returning False stops the analysis at this position boundary: the job
        belongs to someone else now, so continuing would burn engine time on
        work that is going to be discarded anyway.
        """
        keeper.note_progress(done)
        return not keeper.lost

    run_id: uuid.UUID | None = None
    try:
        with engine_session(engine_factory) as engine:
            identity = engine.identify()

            # The run cannot be created at claim time: engine_name is NOT NULL
            # and only exists once the engine is actually up. See ADR D4a.
            with session_factory() as db:
                job = jobs_repo.get_job(db, job_id)
                if job is None:  # pragma: no cover - cascaded away mid-run
                    db.rollback()
                    return JobOutcome(job_id=job_id, result=DISCARDED)
                run = jobs_repo.start_run_for_job(
                    db, job=job, identity=identity, requested_depth=config.depth
                )
                run_id = run.id
                db.commit()

            keeper.start()
            try:
                evaluations = evaluate_positions(
                    engine, positions, config.depth, on_progress=report_progress
                )
            finally:
                # Always stopped before the engine closes, so a finished job
                # never has a thread still renewing its lease.
                keeper.stop()
    except AnalysisAbandoned:
        # Ownership was lost mid-run. The new owner is authoritative; this
        # attempt simply throws its work away.
        logger.info("job=%s abandoned - lease no longer held", job_id)
        return JobOutcome(job_id=job_id, result=DISCARDED)
    except Exception as exc:  # noqa: BLE001 - classified and recorded
        return _fail(
            session_factory, job_id=job_id, run_id=run_id, worker_id=config.worker_id, exc=exc
        )

    # --- Persist. Ownership is checked before anything is written. -------
    try:
        with session_factory() as db:
            if not jobs_repo.complete_job(
                db, job_id=job_id, worker_id=config.worker_id
            ):
                # Reclaimed, cancelled, or deleted while we were analyzing.
                db.rollback()
                logger.info("job=%s discarded - not ours at commit time", job_id)
                return JobOutcome(job_id=job_id, result=DISCARDED)

            analysis_repo.save_position_evaluations(
                db, run_id=run_id, evaluations=evaluations
            )
            run = analysis_repo.get_analysis_run(db, run_id)
            analysis_repo.mark_analysis_run_complete(db, run)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        return _fail(
            session_factory, job_id=job_id, run_id=run_id, worker_id=config.worker_id, exc=exc
        )

    logger.info("job=%s complete positions=%s", job_id, len(evaluations))
    return JobOutcome(job_id=job_id, result=COMPLETED)


def run_forever(
    *,
    config: WorkerConfig | None = None,
    session_factory: SessionFactory = _default_session,
    engine_factory: EngineFactory = build_stockfish_engine,
    stop: threading.Event | None = None,
) -> None:
    """Poll until asked to stop.

    Sweeps abandoned work at startup and then periodically. Startup matters
    most: restarting a worker that was killed mid-game is exactly how a user
    recovers, and it should just work rather than requiring anything manual.

    Sleeps only when the queue is empty, so a backlog is drained without
    waiting between jobs. `stop` is checked between jobs, never mid-analysis -
    graceful shutdown of an in-flight game is Checkpoint 10.
    """
    config = config or build_config()
    stop = stop or threading.Event()

    logger.info(
        "worker=%s starting lease=%ss heartbeat=%ss poll=%ss depth=%s",
        config.worker_id,
        config.lease_seconds,
        config.heartbeat_seconds,
        config.poll_seconds,
        config.depth,
    )

    recovered = recover_stale_jobs(
        session_factory, retry_delay_seconds=config.retry_delay_seconds
    )
    if recovered:
        logger.warning("recovered %s abandoned job(s) at startup", len(recovered))
    last_sweep = time.monotonic()

    while not stop.is_set():
        outcome = run_once(
            config=config, session_factory=session_factory, engine_factory=engine_factory
        )

        if time.monotonic() - last_sweep >= config.stale_sweep_seconds:
            recover_stale_jobs(
                session_factory, retry_delay_seconds=config.retry_delay_seconds
            )
            last_sweep = time.monotonic()

        if outcome.result == CLAIMED_NOTHING:
            stop.wait(config.poll_seconds)

    logger.info("worker=%s stopped", config.worker_id)
