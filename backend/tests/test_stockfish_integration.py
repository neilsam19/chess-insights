"""Opt-in tests against the real Stockfish executable.

Skipped entirely unless STOCKFISH_PATH is configured, so the ordinary suite
never depends on a binary being installed. Run just these with:

    pytest -m stockfish_integration

Assertions deliberately avoid exact centipawn values and specific best
moves - those legitimately vary across Stockfish versions, builds, and
platforms. What is asserted is the shape of the result: a complete run, the
right number of evaluations, valid score types, and recorded provenance.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.models.analysis_run import STATUS_COMPLETE
from app.models.game import Game
from app.repositories import analysis as analysis_repo
from app.services.engine import build_stockfish_engine, engine_session
from app.services.game_analysis_service import analyze_game
from sample_data import SAMPLE_PGN_BYTES

pytestmark = [
    pytest.mark.stockfish_integration,
    pytest.mark.skipif(
        not settings.stockfish_path,
        reason="STOCKFISH_PATH is not configured",
    ),
]

# Deliberately shallow - this proves the engine is wired up, not that it
# plays well, and keeps the test fast enough to run routinely.
TEST_DEPTH = 6


@pytest.fixture
def session_factory(db_session):
    @contextmanager
    def _factory():
        yield db_session

    return _factory


def _shortest_saved_game(client: TestClient, db_session) -> Game:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "NeilSamant"},
    )
    assert response.status_code == 201
    games = db_session.execute(
        select(Game).where(Game.upload_id == response.json()["id"])
    ).scalars().all()
    return min(games, key=lambda g: g.move_count)


def test_real_engine_reports_its_identity() -> None:
    with engine_session(build_stockfish_engine) as engine:
        identity = engine.identify()

    assert identity.name
    assert identity.options["Threads"] == settings.stockfish_threads
    assert identity.options["Hash"] == settings.stockfish_hash_mb
    # The executable hash pins the exact build, which the reported name cannot.
    assert identity.binary_sha256 is None or len(identity.binary_sha256) == 64


def test_real_engine_evaluates_the_starting_position() -> None:
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    with engine_session(build_stockfish_engine) as engine:
        evaluation = engine.evaluate(start, TEST_DEPTH)

    # Exactly one score kind, never both, never neither.
    assert (evaluation.score_cp_white is None) != (evaluation.mate_in_white is None)
    # The opening position is not a forced mate.
    assert evaluation.mate_in_white is None
    # No exact value asserted - only that it is a sane, small edge.
    assert -200 < evaluation.score_cp_white < 200
    assert evaluation.best_move_uci
    assert evaluation.depth_reached is not None


def test_real_engine_analyzes_a_saved_game_end_to_end(
    client: TestClient, db_session, session_factory
) -> None:
    game = _shortest_saved_game(client, db_session)

    result = analyze_game(
        game.id,
        session_factory=session_factory,
        engine_factory=build_stockfish_engine,
        depth=TEST_DEPTH,
    )

    run = analysis_repo.get_analysis_run(db_session, result.run_id)
    assert run.status == STATUS_COMPLETE
    assert run.completed_at is not None
    assert run.error_message is None

    # Provenance is recorded from the live engine, not hard-coded.
    assert run.engine_name
    assert run.requested_depth == TEST_DEPTH
    assert run.engine_options["Threads"] == settings.stockfish_threads

    evaluations = analysis_repo.list_position_evaluations_for_run(db_session, run.id)
    assert len(evaluations) == game.move_count + 1
    assert [e.ply for e in evaluations] == list(range(game.move_count + 1))

    for evaluation in evaluations:
        has_cp = evaluation.score_cp_white is not None
        has_mate = evaluation.mate_in_white is not None
        assert has_cp != has_mate, f"ply {evaluation.ply} has an ambiguous score"
        assert evaluation.depth_reached is not None


def test_real_engine_finds_mate_in_a_finished_game(
    client: TestClient, db_session, session_factory
) -> None:
    """The sample file ends two games in checkmate, so the final position must
    be reported as mate rather than a centipawn score."""
    game = _shortest_saved_game(client, db_session)

    result = analyze_game(
        game.id,
        session_factory=session_factory,
        engine_factory=build_stockfish_engine,
        depth=TEST_DEPTH,
    )

    evaluations = analysis_repo.list_position_evaluations_for_run(db_session, result.run_id)
    final = evaluations[-1]

    assert final.mate_in_white is not None
    assert final.score_cp_white is None


def test_engine_status_endpoint_reports_the_real_engine(client: TestClient) -> None:
    body = client.get("/api/engine/status").json()

    assert body["available"] is True
    assert body["engine_name"]
    assert body["configured_depth"] == settings.stockfish_depth
    assert body["detail"] is None


def test_the_worker_analyzes_a_queued_game_with_the_real_engine(
    client: TestClient, db_session, session_factory
) -> None:
    """The full Sprint 5 path against the real binary: queue a job, run one
    worker pass, and get a durable result - with no HTTP request open for the
    duration of the analysis."""
    from app.models.analysis_job import JOB_COMPLETE
    from app.repositories import analysis_jobs as jobs_repo
    from app.worker import runner
    from app.worker.runner import COMPLETED, WorkerConfig

    game = _shortest_saved_game(client, db_session)

    batch = jobs_repo.create_batch(
        db_session,
        upload_id=game.upload_id,
        engine_config_hash="e" * 64,
        requested_depth=TEST_DEPTH,
        engine_options={"Threads": settings.stockfish_threads},
    )
    job = jobs_repo.enqueue_job(
        db_session,
        batch_id=batch.id,
        game_id=game.id,
        config_hash="worker-integration",
        total_positions=game.move_count + 1,
    )

    outcome = runner.run_once(
        config=WorkerConfig(
            worker_id="integration-worker",
            lease_seconds=120,
            poll_seconds=0.01,
            depth=TEST_DEPTH,
        ),
        session_factory=session_factory,
        engine_factory=build_stockfish_engine,
    )

    assert outcome.result == COMPLETED
    assert outcome.job_id == job.id

    db_session.refresh(job)
    assert job.status == JOB_COMPLETE
    assert job.completed_positions == game.move_count + 1

    run = jobs_repo.latest_run_for_job(db_session, job.id)
    assert run.status == STATUS_COMPLETE
    assert run.attempt_number == 1
    # Provenance comes from the live engine, not from the job's config summary.
    assert run.engine_name

    evaluations = analysis_repo.list_position_evaluations_for_run(db_session, run.id)
    assert len(evaluations) == game.move_count + 1
    for evaluation in evaluations:
        has_cp = evaluation.score_cp_white is not None
        has_mate = evaluation.mate_in_white is not None
        assert has_cp != has_mate, f"ply {evaluation.ply} has an ambiguous score"
