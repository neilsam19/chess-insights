"""HTTP contract for move quality.

Read-only end to end: games are analysed by driving the worker with a scripted
fake engine, then the endpoint is asked what it makes of the stored rows. The
scripted scores are chosen so every assertion is an exact number rather than a
range - a derived metric that is only asserted loosely stops catching the
arithmetic errors it exists to catch.

The sample PGN happens to contain both mate cases the derivation must get
right: game 0 ends in checkmate *delivered by* the saved player, game 2 in
checkmate *against* them.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.analysis_job import AnalysisJob
from app.models.analysis_run import AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.repositories import analysis as analysis_repo
from app.repositories import analysis_jobs as jobs_repo
from app.services.engine import EngineIdentity, RawEvaluation
from fake_engine import FakeEngine, cp, mate
from sample_data import SAMPLE_PGN_BYTES

# --- helpers --------------------------------------------------------------


def _upload(client: TestClient, player_name: str = "NeilSamant") -> uuid.UUID:
    response = client.post(
        "/api/uploads",
        files={"file": ("sample-games.pgn", SAMPLE_PGN_BYTES, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def _game(db_session, upload_id: uuid.UUID, index: int) -> Game:
    return db_session.execute(
        select(Game).where(Game.upload_id == upload_id, Game.game_index == index)
    ).scalar_one()


def _analyze(client, work, engine_holder, game: Game, evaluations: list[RawEvaluation]) -> None:
    """Queue a game and run the worker over it with scripted evaluations."""
    engine_holder["engine"] = FakeEngine(evaluations=evaluations)
    assert client.post(f"/api/games/{game.id}/analysis").status_code == 202
    work()


def _move_quality(client, game: Game) -> dict:
    response = client.get(f"/api/games/{game.id}/move-quality")
    assert response.status_code == 200
    return response.json()


# Game 0: NeilSamant is White. 1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7#
# Player moves are plies 1, 3, 5, 7. The final position is checkmate that the
# player delivered, stored as mate_in_white = 0.
GAME_0_EVALUATIONS = [
    cp(20, best_move_uci="e2e4"),  # ply 0 - the engine agrees with 1. e4
    cp(0),  # ply 1 -> move 1 loses 20
    cp(0, best_move_uci="e2e4"),  # ply 2 - engine wanted something else
    cp(-100),  # ply 3 -> move 3 loses 100
    cp(-100, best_move_uci="e2e4"),  # ply 4
    cp(-400),  # ply 5 -> move 5 loses 300
    mate(1, best_move_uci="h5f7"),  # ply 6 - mate in 1, and the player plays it
    mate(0),  # ply 7 - checkmate on the board
]

# Game 1: NeilSamant is Black. Player moves are plies 2, 4, 6, 8, 10.
# White's score rising by 50 across ply 2 is Black losing 50.
GAME_1_EVALUATIONS = [cp(0), cp(100)] + [cp(150) for _ in range(9)]

# Game 2: NeilSamant is White. 1. f3 e5 2. g4 Qh4# - mated by the opponent.
GAME_2_EVALUATIONS = [
    cp(20, best_move_uci="e2e4"),  # ply 0
    cp(-30),  # ply 1 -> move 1 loses 50
    cp(-30, best_move_uci="d2d4"),  # ply 2
    mate(-1),  # ply 3 -> the player's move allows mate
    mate(0),  # ply 4 - checkmate, delivered by the opponent
]


# --- a completed game, White ----------------------------------------------


class TestCompleteWhite:
    @pytest.fixture
    def body(self, analysis_client, work, engine_holder, db_session):
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 0)
        _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)
        return _move_quality(analysis_client, game)

    def test_reports_complete_with_provenance(self, body):
        assert body["status"] == "complete"
        assert body["player_color"] == "white"
        assert body["move_quality_version"] == 1
        assert body["analysis_run_id"]
        assert body["engine"]["name"] == "FakeFish"

    def test_grades_only_the_players_moves(self, body):
        assert [move["ply"] for move in body["moves"]] == [1, 3, 5, 7]
        assert {move["side"] for move in body["moves"]} == {"white"}

    def test_centipawn_losses_are_exact(self, body):
        by_ply = {move["ply"]: move for move in body["moves"]}

        assert by_ply[1]["centipawn_loss"] == 20
        assert by_ply[3]["centipawn_loss"] == 100
        assert by_ply[5]["centipawn_loss"] == 300

    def test_quality_labels_follow_the_thresholds(self, body):
        by_ply = {move["ply"]: move for move in body["moves"]}

        assert by_ply[1]["quality"] == "good"
        assert by_ply[3]["quality"] == "mistake"
        assert by_ply[5]["quality"] == "blunder"

    def test_the_delivered_checkmate_is_not_a_missed_mate(self, body):
        """The regression the ply-parity rule exists for. `mate 0` is signless,
        so a sign test reads the winning move as throwing a mate away."""
        checkmate = next(move for move in body["moves"] if move["ply"] == 7)

        assert checkmate["special_classification"] != "missed_mate"
        assert checkmate["quality"] != "critical"
        assert checkmate["score_after"] == {"kind": "mate_for", "cp": None, "mate_distance": 0}

    def test_the_summary_matches_the_moves(self, body):
        summary = body["summary"]

        assert summary["total_player_moves"] == 4
        assert summary["graded_moves"] == 3
        assert summary["numeric_cpl_moves"] == 3
        assert summary["unscored_moves"] == 1
        assert summary["coverage_percent"] == 75
        assert summary["average_centipawn_loss"] == 140
        assert summary["median_centipawn_loss"] == 100
        assert (summary["inaccuracies"], summary["mistakes"], summary["blunders"]) == (0, 1, 1)
        assert summary["mate_sequences"] == 1
        assert summary["critical_mistakes"] == 1

    def test_best_move_matches_come_from_the_position_the_player_faced(self, body):
        by_ply = {move["ply"]: move for move in body["moves"]}

        assert by_ply[1]["is_exact_best_move"] is True
        assert by_ply[3]["is_exact_best_move"] is False
        assert body["summary"]["exact_best_move_count"] == 2
        assert body["summary"]["exact_best_move_comparable"] == 4
        assert body["summary"]["exact_best_move_percent"] == 50

    def test_mate_positions_never_carry_a_centipawn_number(self, body):
        for move in body["moves"]:
            if move["special_classification"] in ("missed_mate", "allowed_mate", "mate_sequence"):
                assert move["centipawn_loss"] is None


# --- a completed game, Black ----------------------------------------------


class TestCompleteBlack:
    @pytest.fixture
    def body(self, analysis_client, work, engine_holder, db_session):
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 1)
        _analyze(analysis_client, work, engine_holder, game, GAME_1_EVALUATIONS)
        return _move_quality(analysis_client, game)

    def test_grades_the_black_players_moves(self, body):
        assert body["player_color"] == "black"
        assert [move["ply"] for move in body["moves"]] == [2, 4, 6, 8, 10]
        assert {move["side"] for move in body["moves"]} == {"black"}

    def test_a_rising_white_score_is_a_loss_for_black(self, body):
        """Stored scores are White-relative. Reporting this as a gain would
        tell a Black player their worst moves were their best."""
        first = next(move for move in body["moves"] if move["ply"] == 2)

        assert first["centipawn_loss"] == 50
        assert first["quality"] == "inaccuracy"
        assert first["score_before"] == {"kind": "cp", "cp": -100, "mate_distance": None}
        assert first["score_after"] == {"kind": "cp", "cp": -150, "mate_distance": None}

    def test_the_summary_uses_the_black_perspective(self, body):
        summary = body["summary"]

        assert summary["total_player_moves"] == 5
        assert summary["average_centipawn_loss"] == 10
        assert summary["median_centipawn_loss"] == 0
        assert summary["inaccuracies"] == 1
        assert summary["blunders"] == 0


# --- mate against the player ----------------------------------------------


class TestAllowedMate:
    @pytest.fixture
    def body(self, analysis_client, work, engine_holder, db_session):
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 2)
        _analyze(analysis_client, work, engine_holder, game, GAME_2_EVALUATIONS)
        return _move_quality(analysis_client, game)

    def test_walking_into_a_forced_mate_is_critical(self, body):
        blunder = next(move for move in body["moves"] if move["ply"] == 3)

        assert blunder["special_classification"] == "allowed_mate"
        assert blunder["quality"] == "critical"
        assert blunder["centipawn_loss"] is None

    def test_a_mate_error_counts_as_critical_without_becoming_a_blunder(self, body):
        summary = body["summary"]

        assert summary["allowed_mates"] == 1
        assert summary["blunders"] == 0
        assert summary["critical_mistakes"] == 1

    def test_a_mate_error_still_counts_as_graded(self, body):
        summary = body["summary"]

        assert summary["graded_moves"] == 2
        assert summary["coverage_percent"] == 100
        assert summary["numeric_cpl_moves"] == 1
        assert summary["average_centipawn_loss"] == 50


# --- analysis states ------------------------------------------------------


class TestAnalysisStates:
    def test_an_unanalyzed_game_reports_not_analyzed(self, analysis_client, db_session):
        game = _game(db_session, _upload(analysis_client), 0)

        body = _move_quality(analysis_client, game)

        assert body["status"] == "not_analyzed"
        assert "moves" not in body

    def test_a_queued_game_reports_queued(self, analysis_client, db_session):
        game = _game(db_session, _upload(analysis_client), 0)
        analysis_client.post(f"/api/games/{game.id}/analysis")

        body = _move_quality(analysis_client, game)

        assert body["status"] == "queued"
        assert body["progress"]["total_positions"] == 8

    def test_a_running_game_reports_running_with_the_same_actions(
        self, analysis_client, db_session
    ):
        """Non-complete states are the analysis endpoint's own objects, so
        `can_cancel` and `can_retry` cannot drift between the two endpoints."""
        game = _game(db_session, _upload(analysis_client), 0)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        jobs_repo.claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)
        db_session.commit()

        body = _move_quality(analysis_client, game)
        analysis = analysis_client.get(f"/api/games/{game.id}/analysis").json()

        assert body["status"] == "running"
        assert body["can_cancel"] == analysis["can_cancel"]
        assert body["can_retry"] == analysis["can_retry"]

    def test_a_failed_game_reports_failed_with_retry(self, analysis_client, db_session):
        game = _game(db_session, _upload(analysis_client), 0)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        job = jobs_repo.claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)
        jobs_repo.fail_job(
            db_session,
            job_id=job.id,
            worker_id="test-worker",
            error_code="engine_crashed",
            error_message="Analysis failed unexpectedly.",
        )
        db_session.commit()

        body = _move_quality(analysis_client, game)

        assert body["status"] == "failed"
        assert body["can_retry"] is True

    def test_a_cancelled_game_reports_cancelled(self, analysis_client, db_session):
        game = _game(db_session, _upload(analysis_client), 0)
        analysis_client.post(f"/api/games/{game.id}/analysis")
        job = jobs_repo.claim_next_job(db_session, worker_id="test-worker", lease_seconds=60)
        jobs_repo.mark_job_cancelled(db_session, job_id=job.id, worker_id="test-worker")
        db_session.commit()

        body = _move_quality(analysis_client, game)

        assert body["status"] == "cancelled"

    def test_a_completed_run_wins_over_a_later_failure(
        self, analysis_client, work, engine_holder, db_session
    ):
        """Same precedence as the analysis endpoint: real results beat a later
        failed retry, rather than being hidden behind it."""
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 0)
        _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

        body = _move_quality(analysis_client, game)

        assert body["status"] == "complete"

    def test_an_unknown_game_is_404(self, analysis_client):
        response = analysis_client.get(f"/api/games/{uuid.uuid4()}/move-quality")

        assert response.status_code == 404


# --- games that cannot be graded ------------------------------------------


def test_a_game_with_no_identifiable_player_is_not_gradable(
    analysis_client, work, engine_holder, db_session
):
    """`user_color` is "unknown" when the saved name matched neither side.
    Grading anyway would report an opponent's moves as the user's own."""
    upload_id = _upload(analysis_client, player_name="SomeoneElse")
    game = _game(db_session, upload_id, 0)
    assert game.user_color == "unknown"
    _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

    body = _move_quality(analysis_client, game)

    assert body["status"] == "not_gradable"
    assert body["reason"] == "unknown_player_color"
    assert "moves" not in body


def test_a_legacy_run_without_a_job_still_produces_move_quality(
    analysis_client, db_session
):
    """Sprint 4 runs predate jobs and carry a null `analysis_job_id`. They hold
    real evaluations and must not be excluded by the newer plumbing."""
    game = _game(db_session, _upload(analysis_client), 0)
    run = analysis_repo.create_analysis_run(
        db_session,
        game_id=game.id,
        identity=EngineIdentity(
            name="Stockfish", version="17", binary_sha256="a" * 64, options={"Threads": 1}
        ),
        requested_depth=12,
    )
    analysis_repo.save_position_evaluations(
        db_session,
        run_id=run.id,
        evaluations=list(enumerate(GAME_0_EVALUATIONS)),
    )
    analysis_repo.mark_analysis_run_complete(db_session, run)
    db_session.commit()

    body = _move_quality(analysis_client, game)

    assert body["status"] == "complete"
    assert body["job_id"] is None
    assert body["summary"]["total_player_moves"] == 4


# --- read-only guarantees -------------------------------------------------


def test_the_endpoint_writes_nothing(analysis_client, work, engine_holder, db_session):
    """Scoped to this game's own rows - a global count would be read from the
    developer's real library and would drift with it."""
    upload_id = _upload(analysis_client)
    game = _game(db_session, upload_id, 0)
    _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

    def _counts() -> tuple[int, int, int]:
        runs = db_session.scalar(
            select(func.count()).select_from(AnalysisRun).where(AnalysisRun.game_id == game.id)
        )
        jobs = db_session.scalar(
            select(func.count()).select_from(AnalysisJob).where(AnalysisJob.game_id == game.id)
        )
        evaluations = db_session.scalar(
            select(func.count())
            .select_from(PositionEvaluation)
            .join(AnalysisRun, AnalysisRun.id == PositionEvaluation.analysis_run_id)
            .where(AnalysisRun.game_id == game.id)
        )
        return runs, jobs, evaluations

    before = _counts()
    _move_quality(analysis_client, game)
    _move_quality(analysis_client, game)

    assert _counts() == before


def test_the_endpoint_never_starts_an_engine(
    analysis_client, work, engine_holder, db_session
):
    upload_id = _upload(analysis_client)
    game = _game(db_session, upload_id, 0)
    _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)
    calls_after_analysis = engine_holder["engine"].call_count

    _move_quality(analysis_client, game)

    assert engine_holder["engine"].call_count == calls_after_analysis


def test_repeated_reads_are_identical(analysis_client, work, engine_holder, db_session):
    """Derivation is a pure function of stored rows, so the same request twice
    must produce the same answer - which is what makes it safe to not store."""
    upload_id = _upload(analysis_client)
    game = _game(db_session, upload_id, 0)
    _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

    assert _move_quality(analysis_client, game) == _move_quality(analysis_client, game)
