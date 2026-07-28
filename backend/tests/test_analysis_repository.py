import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.analysis_run import STATUS_COMPLETE, STATUS_FAILED, STATUS_RUNNING, AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis as analysis_repo
from app.services.engine import RawEvaluation
from fake_engine import FakeEngine, cp, mate
from sample_data import SAMPLE_PGN_BYTES

IDENTITY = FakeEngine().identify()


def _saved_game(client: TestClient, db_session, player_name: str = "NeilSamant") -> Game:
    """Import a real upload and return its first persisted game."""
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return db_session.execute(
        select(Game).where(Game.upload_id == response.json()["id"], Game.game_index == 0)
    ).scalar_one()


def _run(db_session, game: Game, depth: int = 12) -> AnalysisRun:
    return analysis_repo.create_analysis_run(
        db_session, game_id=game.id, identity=IDENTITY, requested_depth=depth
    )


def test_create_run_records_provenance(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)

    run = _run(db_session, game)

    assert run.id is not None
    assert run.status == STATUS_RUNNING
    assert run.engine_name == "FakeFish"
    assert run.engine_version == "1.0"
    assert run.engine_binary_sha256 == "0" * 64
    assert run.requested_depth == 12
    assert run.engine_options == {"Threads": 1, "Hash": 16}
    assert run.created_at is not None and run.started_at is not None
    assert run.completed_at is None


def test_saves_move_count_plus_one_ordered_evaluations(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)
    run = _run(db_session, game)

    expected = game.move_count + 1
    saved = analysis_repo.save_position_evaluations(
        db_session,
        run_id=run.id,
        evaluations=[(ply, cp(10 * ply)) for ply in range(expected)],
    )
    assert saved == expected

    evaluations = analysis_repo.list_position_evaluations_for_run(db_session, run.id)
    assert len(evaluations) == expected
    assert [e.ply for e in evaluations] == list(range(expected))
    assert evaluations[0].ply == 0, "ply 0 is the starting position"
    assert evaluations[-1].ply == game.move_count


def test_cp_and_mate_rows_both_persist(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)
    run = _run(db_session, game)

    analysis_repo.save_position_evaluations(
        db_session,
        run_id=run.id,
        evaluations=[(0, cp(-45)), (1, mate(3)), (2, mate(-2))],
    )

    evaluations = analysis_repo.list_position_evaluations_for_run(db_session, run.id)
    assert (evaluations[0].score_cp_white, evaluations[0].mate_in_white) == (-45, None)
    assert (evaluations[1].score_cp_white, evaluations[1].mate_in_white) == (None, 3)
    assert (evaluations[2].score_cp_white, evaluations[2].mate_in_white) == (None, -2)
    assert evaluations[1].best_move_uci == "d8h4"


def test_duplicate_ply_for_a_run_is_rejected(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)
    run = _run(db_session, game)

    analysis_repo.save_position_evaluations(
        db_session, run_id=run.id, evaluations=[(0, cp(10))]
    )

    with pytest.raises(IntegrityError):
        analysis_repo.save_position_evaluations(
            db_session, run_id=run.id, evaluations=[(0, cp(20))]
        )


@pytest.mark.parametrize(
    "score_cp_white,mate_in_white",
    [(None, None), (25, 3)],
    ids=["neither-score", "both-scores"],
)
def test_invalid_score_shape_is_rejected(
    client: TestClient, db_session, score_cp_white, mate_in_white
) -> None:
    game = _saved_game(client, db_session)
    run = _run(db_session, game)

    with pytest.raises(IntegrityError):
        analysis_repo.save_position_evaluations(
            db_session,
            run_id=run.id,
            evaluations=[
                (
                    0,
                    RawEvaluation(
                        score_cp_white=score_cp_white,
                        mate_in_white=mate_in_white,
                        best_move_uci=None,
                        depth_reached=12,
                        nodes=1,
                        time_ms=1,
                    ),
                )
            ],
        )


def test_complete_and_failed_transitions(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)

    completed = _run(db_session, game)
    analysis_repo.mark_analysis_run_complete(db_session, completed)
    assert completed.status == STATUS_COMPLETE
    assert completed.completed_at is not None
    assert completed.error_message is None

    failed = _run(db_session, game)
    analysis_repo.mark_analysis_run_failed(db_session, failed, "Stockfish exited unexpectedly.")
    assert failed.status == STATUS_FAILED
    assert failed.completed_at is not None
    assert failed.error_message == "Stockfish exited unexpectedly."


def test_latest_run_wins_and_is_deterministic(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)

    first = _run(db_session, game)
    analysis_repo.mark_analysis_run_failed(db_session, first, "boom")
    second = _run(db_session, game)

    latest = analysis_repo.get_latest_analysis_run_for_game(db_session, game.id)
    assert latest is not None
    assert latest.id == second.id
    assert latest.status == STATUS_RUNNING

    # A retry creates a new run rather than overwriting the failed one.
    assert analysis_repo.get_analysis_run(db_session, first.id) is not None


def test_no_run_for_an_unanalyzed_game(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)

    assert analysis_repo.get_latest_analysis_run_for_game(db_session, game.id) is None
    assert analysis_repo.get_analysis_run(db_session, uuid.uuid4()) is None


def test_run_reloads_with_evaluations_in_ply_order(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)
    run = _run(db_session, game)
    analysis_repo.save_position_evaluations(
        db_session,
        run_id=run.id,
        # Deliberately out of order - ordering must come from the query.
        evaluations=[(2, cp(30)), (0, cp(10)), (1, cp(20))],
    )
    analysis_repo.mark_analysis_run_complete(db_session, run)

    db_session.expire_all()
    reloaded = analysis_repo.get_run_with_evaluations(db_session, run.id)

    assert reloaded is not None
    assert reloaded.status == STATUS_COMPLETE
    assert [e.ply for e in reloaded.evaluations] == [0, 1, 2]
    assert [e.score_cp_white for e in reloaded.evaluations] == [10, 20, 30]


def test_deleting_a_game_cascades_to_runs_and_evaluations(client: TestClient, db_session) -> None:
    game = _saved_game(client, db_session)
    run = _run(db_session, game)
    analysis_repo.save_position_evaluations(
        db_session, run_id=run.id, evaluations=[(0, cp(10)), (1, cp(20))]
    )
    db_session.flush()
    run_id = run.id

    db_session.delete(game)
    db_session.flush()

    assert analysis_repo.get_analysis_run(db_session, run_id) is None
    remaining = db_session.scalar(
        select(func.count())
        .select_from(PositionEvaluation)
        .where(PositionEvaluation.analysis_run_id == run_id)
    )
    assert remaining == 0


def test_deleting_an_upload_cascades_all_the_way_down(client: TestClient, db_session) -> None:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": "CascadePlayer"},
    )
    upload_id = response.json()["id"]
    game = db_session.execute(
        select(Game).where(Game.upload_id == upload_id, Game.game_index == 0)
    ).scalar_one()

    run = _run(db_session, game)
    analysis_repo.save_position_evaluations(
        db_session, run_id=run.id, evaluations=[(0, cp(10))]
    )
    db_session.flush()
    run_id = run.id

    assert client.delete(f"/api/uploads/{upload_id}").status_code == 204

    # Scoped to this test's own ids - the dev database holds real analyses too.
    assert db_session.scalar(
        select(func.count()).select_from(AnalysisRun).where(AnalysisRun.id == run_id)
    ) == 0
    assert db_session.scalar(
        select(func.count())
        .select_from(PositionEvaluation)
        .where(PositionEvaluation.analysis_run_id == run_id)
    ) == 0
