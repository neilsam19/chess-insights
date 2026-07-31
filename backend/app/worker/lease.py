"""Keeping a claim alive while the engine is busy.

A lease says "this worker is still here". The obvious way to renew it is
between positions, which is what Checkpoint 3 did - and it is wrong, because a
single position can take longer than the whole lease. At depth 20 in a sharp
middlegame, one `evaluate` call can run for minutes. The job would be swept up
as abandoned while its worker was working perfectly.

So renewal happens on its own thread, on its own database sessions, on a fixed
interval that has nothing to do with how long any position takes. The analysis
thread only publishes a counter; the keeper thread does all the writing.

That also solves progress throttling for free. Checkpoint 3 wrote one row
update per position; now progress rides along with the heartbeat at a fixed
interval, no matter how many positions fly past in between.

Set the interval to zero to get a keeper with no thread at all, which is what
tests using the shared rollback session need - a Session must not be touched
from two threads.
"""

import logging
import threading
import uuid
from typing import Callable, ContextManager

from sqlalchemy.orm import Session

from app.repositories import analysis_jobs as jobs_repo

logger = logging.getLogger("app.worker")

SessionFactory = Callable[[], ContextManager[Session]]


class LeaseKeeper:
    """Renews one job's lease until told to stop, or until it is lost.

    `lost` becomes true as soon as a renewal matches no row - the job was
    reclaimed, cancelled, or finished by someone else. The analysis loop checks
    it at each position boundary and stops, so a worker that no longer owns its
    job stops burning engine time immediately rather than at the end.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float,
        total_positions: int,
    ) -> None:
        self._session_factory = session_factory
        self._job_id = job_id
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._total_positions = total_positions

        self._completed = 0
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def cancel_requested(self) -> bool:
        """A user asked this job to stop.

        Learned from the heartbeat rather than a separate query: cancellation
        sets a flag instead of changing status, precisely so that the worker
        can notice it at a position boundary and stop cleanly.
        """
        return self._cancelled.is_set()

    @property
    def should_stop(self) -> bool:
        return self.lost or self.cancel_requested

    @property
    def threaded(self) -> bool:
        return self._thread is not None

    def note_progress(self, completed: int) -> None:
        """Publish progress. Cheap by design - called once per position.

        With a keeper thread this only assigns an integer; the write happens on
        the next heartbeat. Without one, it writes immediately, since there is
        no other opportunity.
        """
        self._completed = completed
        if self._thread is None:
            self._renew()

    def start(self) -> None:
        if self._interval_seconds <= 0:
            return
        self._thread = threading.Thread(
            target=self._loop,
            name=f"lease-{self._job_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop renewing and wait for the thread to finish.

        Always called from a `finally`, so the thread cannot outlive the job
        and keep a finished job's lease alive.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=self._interval_seconds + 5)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            if not self._renew():
                break

    def _renew(self) -> bool:
        """One heartbeat. Returns False once there is no reason to keep going."""
        if self._stop.is_set() or self.should_stop:
            return False
        try:
            with self._session_factory() as db:
                result = jobs_repo.heartbeat_job(
                    db,
                    job_id=self._job_id,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                    completed_positions=min(self._completed, self._total_positions),
                )
                db.commit()
        except Exception:  # noqa: BLE001 - a failed heartbeat must not kill the run
            # One unlucky heartbeat is survivable: the lease is long enough to
            # cover several. If the database is really gone, the next renewals
            # fail too and the lease expires, which is the correct outcome.
            logger.warning("job=%s heartbeat failed", self._job_id, exc_info=True)
            return True

        if not result.owned:
            logger.info("job=%s lease lost", self._job_id)
            self._lost.set()
            return False

        if result.cancel_requested:
            logger.info("job=%s cancellation requested", self._job_id)
            self._cancelled.set()
            return False

        return True
