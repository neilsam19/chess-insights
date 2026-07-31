"""The analysis worker: a separate process that executes queued engine work.

Run it alongside the API, never inside it:

    python -m app.worker            # poll until stopped
    python -m app.worker --once     # process at most one job, then exit

It shares the database and the application's models and services, but nothing
else. The API never starts an engine, and the worker never serves a request.
"""

from app.worker.lease import LeaseKeeper
from app.worker.runner import WorkerConfig, run_forever, run_once
from app.worker.sweeper import recover_stale_jobs

__all__ = [
    "LeaseKeeper",
    "WorkerConfig",
    "recover_stale_jobs",
    "run_forever",
    "run_once",
]
