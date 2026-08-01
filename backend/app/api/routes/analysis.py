"""Job endpoints: read one job, cancel it, retry it.

Commands, not state writes. There is no endpoint that accepts a status,
progress value, engine identity, or evaluation from a client - a client says
what it wants to happen and the server decides what that means.

Reading a job is separate from reading a game's analysis on purpose: a client
polling progress should not re-download every evaluation each time.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.analysis_batch import AnalysisBatch
from app.models.analysis_job import AnalysisJob
from app.repositories import analysis_jobs as jobs_repo
from app.schemas.analysis import BatchStatusOut, JobStatusOut
from app.services import analysis_batches
from app.services.analysis_view import batch_status_out, job_status_out

router = APIRouter()


def _job_or_404(db: Session, job_id: uuid.UUID) -> AnalysisJob:
    job = jobs_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    return job


def _batch_or_404(db: Session, batch_id: uuid.UUID) -> AnalysisBatch:
    batch = jobs_repo.get_batch(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Analysis batch not found.")
    return batch


@router.get("/analysis/jobs/{job_id}", response_model=JobStatusOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobStatusOut:
    return job_status_out(_job_or_404(db, job_id))


@router.post("/analysis/jobs/{job_id}/cancel", response_model=JobStatusOut)
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobStatusOut:
    """Ask a job to stop.

    A queued job is cancelled outright. A running one is asked, and stops at
    the next position boundary - which is what guarantees a cancelled attempt
    leaves no partial evaluations behind. Either way this returns immediately;
    the reported status may still be `running` for a moment afterwards.

    Cancelling an already-finished job is not an error. The caller wanted it
    stopped, and it is stopped.
    """
    _job_or_404(db, job_id)
    jobs_repo.cancel_job(db, job_id=job_id)
    db.commit()
    return job_status_out(_job_or_404(db, job_id))


@router.post("/analysis/jobs/{job_id}/retry", response_model=JobStatusOut)
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> JobStatusOut:
    """Queue another attempt at a failed or cancelled job.

    Rejects anything else with 409: a completed run is immutable, and retrying
    a job that is currently running would abandon a live worker's work. Each
    retry creates a new attempt and a new run rather than rewriting history.
    """
    job = _job_or_404(db, job_id)
    batch_id = job.batch_id
    try:
        jobs_repo.retry_job(db, job_id=job_id)
    except jobs_repo.InvalidJobTransition as exc:
        raise HTTPException(
            status_code=409,
            detail=f"This analysis cannot be retried while it is {exc.from_status}.",
        ) from None

    # Reopening a job can move its batch out of a terminal state.
    analysis_batches.refresh_batch_status(db, batch_id)
    db.commit()
    return job_status_out(_job_or_404(db, job_id))


@router.get("/analysis/batches/{batch_id}", response_model=BatchStatusOut)
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)) -> BatchStatusOut:
    """Aggregate progress across every game in one request.

    Counts and percentages are derived from the jobs on each read rather than
    read from the batch row, so they cannot drift out of step with reality.
    """
    return batch_status_out(db, _batch_or_404(db, batch_id))


@router.post("/analysis/batches/{batch_id}/cancel", response_model=BatchStatusOut)
def cancel_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)) -> BatchStatusOut:
    """Stop everything in this batch that has not finished.

    Games that already completed keep their results - cancelling the rest of a
    batch is not a reason to throw away work that is already done.
    """
    batch = _batch_or_404(db, batch_id)
    analysis_batches.cancel_batch(db, batch)
    db.commit()
    return batch_status_out(db, _batch_or_404(db, batch_id))
