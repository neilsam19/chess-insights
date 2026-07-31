"""Command-line entry point: `python -m app.worker`.

Deliberately thin. Everything testable lives in `runner`; this file only parses
arguments, sets up logging, and translates Ctrl+C into a clean shutdown.
"""

import argparse
import logging
import signal
import threading

from app.services.engine import EngineUnavailableError, resolve_engine_config
from app.worker.runner import CLAIMED_NOTHING, build_config, run_forever, run_once
from app.worker.sweeper import recover_stale_jobs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.worker",
        description="Execute queued Stockfish analysis jobs.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one job and exit. Used by tests and CI.",
    )
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Override the lease owner name. Defaults to host-pid.",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="Override the configured analysis depth.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    logger = logging.getLogger("app.worker")

    config = build_config(worker_id=args.worker_id, depth=args.depth)

    # Fail loudly at startup rather than failing every job one at a time. The
    # engine is not started here - only its configuration is validated.
    try:
        engine_config = resolve_engine_config(depth=config.depth)
    except EngineUnavailableError as exc:
        logger.error("%s", exc)
        logger.error("Set STOCKFISH_PATH in backend/.env before starting the worker.")
        return 2

    logger.info(
        "engine ready sha256=%s options=%s",
        (engine_config.binary_sha256 or "unknown")[:12],
        engine_config.options,
    )

    if args.once:
        # A single pass is still a startup, so it recovers abandoned work
        # first - otherwise `--once` could report an empty queue while a job
        # sat stuck in `running` from a killed process.
        recovered = recover_stale_jobs(retry_delay_seconds=config.retry_delay_seconds)
        if recovered:
            logger.warning("recovered %s abandoned job(s)", len(recovered))

        outcome = run_once(config=config)
        if outcome.result == CLAIMED_NOTHING:
            logger.info("nothing queued")
        return 0

    stop = threading.Event()

    def request_stop(signum, _frame) -> None:
        logger.info("signal %s received - finishing current job then stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    run_forever(config=config, stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
