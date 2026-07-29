import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.analysis_run import STATUS_COMPLETE, STATUS_FAILED, AnalysisRun
from app.models.game import Game
from app.models.move import Move
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis as analysis_repo
from app.services.engine import EngineUnavailableError
from app.services.game_analysis_service import (
    AnalysisAlreadyRunningError,
    GameNotFoundError,
    InvalidGameDataError,
    analyze_game,
    build_positions,
)
from fake_engine import FakeEngine, cp, mate
from sample_data import SAMPLE_PGN_BYTES


@pytest.fixture
def session_factory(db_session):
    """Hands the coordinator the rollback-scoped test session.

    The coordinator commits three times per run; under the conftest fixture
    those are savepoint releases, so nothing survives the test.
    """

    @contextmanager
    def _factory():
        yield db_session

    return _factory


def _saved_game(client: TestClient, db_session, player_name: str = "NeilSamant") -> Game:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return db_session.execute(
        select(Game).where(Game.upload_id == response.json()["id"], Game.game_index == 0)
    ).scalar_one()


class TestPositionBuilding:
    def test_builds_move_count_plus_one_positions(self, client: TestClient, db_session) -> None:
        game = _saved_game(client, db_session)
        moves = list(
            db_session.execute(
                select(Move).where(Move.game_id == game.id).order_by(Move.ply)
            ).scalars()
        )

        positions = build_positions(game, moves)

        assert len(positions) == game.move_count + 1
        assert positions[0] == moves[0].fen_before
        assert positions[0].startswith("rnbqkbnr/pppppppp")  # ply 0 is the start
        assert positions[1] == moves[0].fen_after
        assert positions[-1] == moves[-1].fen_after

    def test_zero_move_game_is_rejected(self, client: TestClient, db_session) -> None:
        game = _saved_game(client, db_session)

        with pytest.raises(InvalidGameDataError, match="no moves"):
            build_positions(game, [])

    def test_move_count_mismatch_is_rejected(self, client: TestClient, db_session) -> None:
        game = _saved_game(client, db_session)
        moves = list(
            db_session.execute(
                select(Move).where(Move.game_id == game.id).order_by(Move.ply)
            ).scalars()
        )

        with pytest.raises(InvalidGameDataError, match="move count"):
            build_positions(game, moves[:-1])

    def test_noncontiguous_plies_are_rejected(self, client: TestClient, db_session) -> None:
        game = _saved_game(client, db_session)
        moves = list(
            db_session.execute(
                select(Move).where(Move.game_id == game.id).order_by(Move.ply)
            ).scalars()
        )
        moves[2].ply = 99

        with pytest.raises(InvalidGameDataError, match="non-contiguous"):
            build_positions(game, moves)

    def test_invalid_fen_is_rejected(self, client: TestClient, db_session) -> None:
        game = _saved_game(client, db_session)
        moves = list(
            db_session.execute(
                select(Move).where(Move.game_id == game.id).order_by(Move.ply)
            ).scalars()
        )
        moves[1].fen_after = "not a fen at all"

        with pytest.raises(InvalidGameDataError, match="Invalid position at ply"):
            build_positions(game, moves)


class TestSuccessfulRun:
    def test_running_to_complete_with_expected_evaluations(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        engine = FakeEngine()

        result = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: engine, depth=8
        )

        assert result.created is True
        run = analysis_repo.get_analysis_run(db_session, result.run_id)
        assert run.status == STATUS_COMPLETE
        assert run.completed_at is not None
        assert run.error_message is None
        assert run.requested_depth == 8
        assert run.engine_name == "FakeFish"

        evaluations = analysis_repo.list_position_evaluations_for_run(db_session, result.run_id)
        assert len(evaluations) == game.move_count + 1
        assert [e.ply for e in evaluations] == list(range(game.move_count + 1))
        assert engine.depths == [8] * (game.move_count + 1)

    def test_analyzes_stored_positions_in_order(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        moves = list(
            db_session.execute(
                select(Move).where(Move.game_id == game.id).order_by(Move.ply)
            ).scalars()
        )
        expected = [moves[0].fen_before] + [m.fen_after for m in moves]
        engine = FakeEngine()

        analyze_game(game.id, session_factory=session_factory, engine_factory=lambda: engine)

        assert engine.evaluated_fens == expected

    def test_persists_cp_and_mate_distinctly(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        canned = [cp(35), cp(-120), mate(4), mate(-1)] + [cp(5)] * game.move_count
        engine = FakeEngine(evaluations=canned)

        result = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: engine
        )

        evaluations = analysis_repo.list_position_evaluations_for_run(db_session, result.run_id)
        assert (evaluations[0].score_cp_white, evaluations[0].mate_in_white) == (35, None)
        assert (evaluations[1].score_cp_white, evaluations[1].mate_in_white) == (-120, None)
        assert (evaluations[2].score_cp_white, evaluations[2].mate_in_white) == (None, 4)
        assert (evaluations[3].score_cp_white, evaluations[3].mate_in_white) == (None, -1)
        assert evaluations[0].best_move_uci == "e2e4"
        assert evaluations[2].best_move_uci == "d8h4"

    def test_engine_is_closed_after_success(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        engine = FakeEngine()

        analyze_game(game.id, session_factory=session_factory, engine_factory=lambda: engine)

        assert engine.closed


class TestFailedRun:
    def test_mid_game_failure_marks_run_failed_with_no_evaluations(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        engine = FakeEngine(fail_on_call=3)

        with pytest.raises(Exception):
            analyze_game(game.id, session_factory=session_factory, engine_factory=lambda: engine)

        run = analysis_repo.get_latest_analysis_run_for_game(db_session, game.id)
        assert run.status == STATUS_FAILED
        assert run.error_message
        assert run.completed_at is not None

        # No partial output - a failed run must never look usable.
        assert analysis_repo.list_position_evaluations_for_run(db_session, run.id) == []

    def test_engine_is_closed_after_failure(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        engine = FakeEngine(fail_on_call=2)

        with pytest.raises(Exception):
            analyze_game(game.id, session_factory=session_factory, engine_factory=lambda: engine)

        assert engine.closed

    def test_engine_startup_failure_creates_no_run(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)

        def _unavailable():
            raise EngineUnavailableError("Engine analysis is not configured.")

        with pytest.raises(EngineUnavailableError):
            analyze_game(
                game.id, session_factory=session_factory, engine_factory=_unavailable
            )

        # A misconfigured server is not a failed analysis.
        assert analysis_repo.get_latest_analysis_run_for_game(db_session, game.id) is None

    def test_error_message_is_sanitized(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)

        class Exploding(FakeEngine):
            def evaluate(self, fen: str, depth: int):
                raise RuntimeError("C:\\secret\\path\\stockfish.exe blew up")

        with pytest.raises(RuntimeError):
            analyze_game(
                game.id, session_factory=session_factory, engine_factory=lambda: Exploding()
            )

        run = analysis_repo.get_latest_analysis_run_for_game(db_session, game.id)
        assert run.status == STATUS_FAILED
        assert "secret" not in run.error_message
        assert run.error_message == "Analysis failed unexpectedly."


class TestValidationBeforeEngine:
    def test_missing_game_is_rejected(self, db_session, session_factory) -> None:
        engine = FakeEngine()

        with pytest.raises(GameNotFoundError):
            analyze_game(
                uuid.uuid4(), session_factory=session_factory, engine_factory=lambda: engine
            )

        assert engine.call_count == 0

    def test_invalid_data_never_starts_the_engine(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        game.move_count = 999  # disagrees with stored moves
        db_session.flush()
        engine = FakeEngine()

        with pytest.raises(InvalidGameDataError):
            analyze_game(
                game.id, session_factory=session_factory, engine_factory=lambda: engine
            )

        assert engine.call_count == 0
        assert engine.closed is False, "the engine should never have been started"
        assert analysis_repo.get_latest_analysis_run_for_game(db_session, game.id) is None


class TestRunPolicy:
    def test_duplicate_request_while_running_is_rejected(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        # Leave a run in the "running" state, as an in-flight analysis would.
        stuck = analysis_repo.create_analysis_run(
            db_session,
            game_id=game.id,
            identity=FakeEngine().identify(),
            requested_depth=12,
        )
        db_session.commit()
        engine = FakeEngine()

        with pytest.raises(AnalysisAlreadyRunningError) as excinfo:
            analyze_game(
                game.id, session_factory=session_factory, engine_factory=lambda: engine
            )

        assert excinfo.value.run_id == stuck.id
        assert engine.call_count == 0, "a second engine must not be launched"

    def test_completed_game_returns_existing_run_without_reanalyzing(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        first_engine = FakeEngine()
        first = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: first_engine
        )

        second_engine = FakeEngine()
        second = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: second_engine
        )

        assert second.run_id == first.run_id
        assert second.created is False
        assert second_engine.call_count == 0

    def test_retry_after_failure_creates_a_new_run(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)

        with pytest.raises(Exception):
            analyze_game(
                game.id,
                session_factory=session_factory,
                engine_factory=lambda: FakeEngine(fail_on_call=2),
            )
        failed = analysis_repo.get_latest_analysis_run_for_game(db_session, game.id)
        assert failed.status == STATUS_FAILED

        retry = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: FakeEngine()
        )

        assert retry.run_id != failed.id
        assert retry.created is True
        run_count = db_session.scalar(
            select(func.count()).select_from(AnalysisRun).where(AnalysisRun.game_id == game.id)
        )
        assert run_count == 2, "the failed run is kept, not overwritten"
        assert analysis_repo.get_analysis_run(db_session, failed.id).status == STATUS_FAILED


class TestDurability:
    def test_completed_result_survives_a_fresh_session_read(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        result = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: FakeEngine()
        )

        db_session.expire_all()
        reloaded = analysis_repo.get_run_with_evaluations(db_session, result.run_id)

        assert reloaded.status == STATUS_COMPLETE
        assert len(reloaded.evaluations) == game.move_count + 1
        assert [e.ply for e in reloaded.evaluations] == list(range(game.move_count + 1))

    def test_only_this_games_evaluations_are_written(
        self, client: TestClient, db_session, session_factory
    ) -> None:
        game = _saved_game(client, db_session)
        result = analyze_game(
            game.id, session_factory=session_factory, engine_factory=lambda: FakeEngine()
        )

        # Scoped to this run - the dev database holds unrelated data.
        written = db_session.scalar(
            select(func.count())
            .select_from(PositionEvaluation)
            .where(PositionEvaluation.analysis_run_id == result.run_id)
        )
        assert written == game.move_count + 1
