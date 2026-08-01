"""Deciding what happens when someone asks for a game to be analyzed.

This is the single authority for idempotency (ADR decision D4). Exactly three
answers are possible, and nothing else in the application makes this call:

    reuse     - a matching completed analysis already exists
    conflict  - matching work is already queued or running
    queued    - a new job was created

"Matching" means same game and same configuration, where configuration is a
hash of the engine build, depth, options, and behaviour version. Analysing the
same game at a different depth is different work and is allowed to proceed.

Everything is validated *before* a job is created: a game that cannot be
analysed is rejected outright rather than queued and then failed, which would
be a slower way of saying the same thing while leaving a failed record behind.

The engine is never started here. `resolve_engine_config` only reads and
validates configuration, so enqueueing stays cheap enough to run per request.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.analysis_job import AnalysisJob
from app.models.analysis_run import STATUS_COMPLETE, AnalysisRun
from app.models.game import Game
from app.repositories import analysis_jobs as jobs_repo
from app.services.analysis_config import (
    compute_engine_config_hash,
    compute_job_config_hash,
)
from app.services.analysis_executor import load_game_positions
from app.services.engine import EngineConfig, resolve_engine_config

REUSED = "reused"
CONFLICT = "conflict"
QUEUED = "queued"


@dataclass(frozen=True)
class EnqueueOutcome:
    result: str
    job: AnalysisJob | None = None
    reused_run: AnalysisRun | None = None
    conflicting_job: AnalysisJob | None = None


def run_matches_config(run: AnalysisRun, engine_config_hash: str) -> bool:
    """Would this run's engine setup produce the same evaluations today?

    Reconstructs the configuration identity from what the run recorded and
    compares hashes, rather than comparing fields by hand. That also means
    Sprint 4 runs - which predate jobs entirely and have no `config_hash` -
    are still reusable, so upgrading did not silently invalidate 430 stored
    evaluations.

    The executable path is deliberately not part of identity: moving the same
    binary must not invalidate existing analyses.
    """
    candidate = EngineConfig(
        path="",
        binary_sha256=run.engine_binary_sha256,
        options=dict(run.engine_options or {}),
        depth=run.requested_depth,
    )
    return compute_engine_config_hash(candidate) == engine_config_hash


def find_reusable_run(
    db: Session, game_id: uuid.UUID, engine_config_hash: str
) -> AnalysisRun | None:
    """The newest completed run whose configuration matches, if any."""
    from sqlalchemy import select

    runs = db.execute(
        select(AnalysisRun)
        .where(AnalysisRun.game_id == game_id, AnalysisRun.status == STATUS_COMPLETE)
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
    ).scalars()

    for run in runs:
        if run_matches_config(run, engine_config_hash):
            return run
    return None


def enqueue_game_analysis(
    db: Session, game: Game, *, depth: int | None = None
) -> EnqueueOutcome:
    """Queue one game for analysis, or explain why that is unnecessary.

    Raises `EngineUnavailableError` if the engine is unusable and
    `InvalidGameDataError` if the stored game cannot be analysed. Neither
    creates a job, because neither is a failed analysis.

    Caller owns the transaction. The partial unique index on
    `(game_id, config_hash)` is the real guard against duplicates - the
    conflict check here exists to return a helpful 409, not to be correct
    under concurrency.
    """
    config = resolve_engine_config(depth=depth)
    engine_config_hash = compute_engine_config_hash(config)
    job_config_hash = compute_job_config_hash(engine_config_hash, game.game_hash)

    # Validate before creating anything. Raises for an unanalyzable game.
    _, positions = load_game_positions(db, game.id)

    reusable = find_reusable_run(db, game.id, engine_config_hash)
    if reusable is not None:
        return EnqueueOutcome(result=REUSED, reused_run=reusable)

    active = jobs_repo.find_active_job(
        db, game_id=game.id, config_hash=job_config_hash
    )
    if active is not None:
        return EnqueueOutcome(result=CONFLICT, conflicting_job=active)

    batch = jobs_repo.create_batch(
        db,
        upload_id=game.upload_id,
        engine_config_hash=engine_config_hash,
        requested_depth=config.depth,
        engine_options=config.options,
    )
    job = jobs_repo.enqueue_job(
        db,
        batch_id=batch.id,
        game_id=game.id,
        config_hash=job_config_hash,
        total_positions=len(positions),
    )
    return EnqueueOutcome(result=QUEUED, job=job)


__all__ = [
    "CONFLICT",
    "QUEUED",
    "REUSED",
    "EnqueueOutcome",
    "enqueue_game_analysis",
    "find_reusable_run",
    "run_matches_config",
]
