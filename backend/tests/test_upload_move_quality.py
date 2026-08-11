"""HTTP contract for upload-level move quality.

Aggregation is where a metric can quietly become a lie: a denominator that
counts unanalysed games, an average that silently treats a mate as zero, an
ordering that shuffles between requests. So the assertions here are exact
numbers, and several of them are cross-checks against the per-game endpoint
rather than against constants copied out of the implementation.

The sample PGN gives one game as White ending in a delivered checkmate, one as
Black, and one as White ending in being mated - three openings, both colours,
and both mate directions, without inventing fixtures.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.core.database import engine as db_engine
from app.models.analysis_job import JOB_QUEUED, JOB_RUNNING, AnalysisJob
from app.models.analysis_run import STATUS_COMPLETE, AnalysisRun
from app.models.game import Game
from app.models.position_evaluation import PositionEvaluation
from app.services.engine import RawEvaluation
from app.services.upload_move_quality import (
    NOT_GRADABLE,
    QUEUED,
    RUNNING,
    _coverage_bucket,
)
from fake_engine import FakeEngine, cp, mate
from sample_data import SAMPLE_PGN_BYTES, SAMPLE_PGN_TEXT


# --- helpers --------------------------------------------------------------

# One game only, for the query-count comparison. Built from the sample file so
# it stays in step with it, with its own header line so the file hash differs.
SINGLE_GAME_PGN_BYTES = (
    SAMPLE_PGN_TEXT.split("\n\n[Event")[0] + "\n% single-game copy\n"
).encode("utf-8")


def _upload(client: TestClient, player_name: str = "NeilSamant", *, pgn=None) -> uuid.UUID:
    response = client.post(
        "/api/uploads",
        files={
            "file": (
                "sample-games.pgn",
                SAMPLE_PGN_BYTES if pgn is None else pgn,
                "application/x-chess-pgn",
            )
        },
        data={"player_name": player_name},
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def _game(db_session, upload_id: uuid.UUID, index: int) -> Game:
    return db_session.execute(
        select(Game).where(Game.upload_id == upload_id, Game.game_index == index)
    ).scalar_one()


def _analyze(client, work, engine_holder, game: Game, evaluations: list[RawEvaluation]) -> None:
    engine_holder["engine"] = FakeEngine(evaluations=evaluations)
    assert client.post(f"/api/games/{game.id}/analysis").status_code == 202
    work()


def _quality(client, upload_id: uuid.UUID) -> dict:
    response = client.get(f"/api/uploads/{upload_id}/move-quality")
    assert response.status_code == 200
    return response.json()


def _game_quality(client, game: Game) -> dict:
    response = client.get(f"/api/games/{game.id}/move-quality")
    assert response.status_code == 200
    return response.json()


# Game 0: NeilSamant is White, and delivers checkmate. Player plies 1, 3, 5, 7.
GAME_0_EVALUATIONS = [
    cp(20, best_move_uci="e2e4"),
    cp(0),
    cp(0, best_move_uci="e2e4"),
    cp(-100),
    cp(-100, best_move_uci="e2e4"),
    cp(-400),
    mate(1, best_move_uci="h5f7"),
    mate(0),
]

# Game 1: NeilSamant is Black. Player plies 2, 4, 6, 8, 10; losses 50 then 0s.
GAME_1_EVALUATIONS = [cp(0), cp(100)] + [cp(150) for _ in range(9)]

# Game 2: NeilSamant is White, and is mated. Player plies 1, 3.
GAME_2_EVALUATIONS = [
    cp(20, best_move_uci="e2e4"),
    cp(-30),
    cp(-30, best_move_uci="d2d4"),
    mate(-1),
    mate(0),
]


@pytest.fixture
def analyzed_upload(analysis_client, work, engine_holder, db_session):
    """All three sample games analysed, with scripted scores."""
    upload_id = _upload(analysis_client)
    for index, evaluations in enumerate(
        (GAME_0_EVALUATIONS, GAME_1_EVALUATIONS, GAME_2_EVALUATIONS)
    ):
        _analyze(
            analysis_client, work, engine_holder, _game(db_session, upload_id, index), evaluations
        )
    return upload_id


# --- a fully analysed upload ----------------------------------------------


class TestFullyAnalyzed:
    @pytest.fixture
    def body(self, analysis_client, analyzed_upload):
        return _quality(analysis_client, analyzed_upload)

    def test_reports_full_coverage(self, body):
        coverage = body["coverage"]

        assert coverage["total_games"] == 3
        assert coverage["complete_games"] == 3
        assert coverage["not_analyzed_games"] == 0
        assert coverage["not_gradable_games"] == 0

    def test_every_game_lands_in_exactly_one_bucket(self, body):
        coverage = body["coverage"]
        buckets = (
            "complete_games",
            "queued_games",
            "running_games",
            "failed_games",
            "cancelled_games",
            "not_analyzed_games",
            "not_gradable_games",
        )

        assert sum(coverage[bucket] for bucket in buckets) == coverage["total_games"]

    def test_the_overall_sample_is_the_players_moves_only(self, body):
        overall = body["overall"]

        # 4 + 5 + 2 player moves. The opponent's 11 do not appear anywhere.
        assert overall["games"] == 3
        assert overall["total_player_moves"] == 11
        assert overall["graded_moves"] == 10
        assert overall["numeric_cpl_moves"] == 9
        assert overall["unscored_moves"] == 1
        assert overall["coverage_percent"] == 91

    def test_the_average_uses_only_moves_with_a_number(self, body):
        # Losses across the upload: 20, 100, 300, 50, 0, 0, 0, 0, 50.
        # Mean 520/9 = 57.8 -> 58. The two mate transitions contribute to
        # neither, because pricing a mate in pawns is what this whole sprint
        # refuses to do.
        assert body["overall"]["average_centipawn_loss"] == 58
        assert body["overall"]["median_centipawn_loss"] == 20

    def test_mate_events_are_excluded_from_the_average_but_counted_as_critical(self, body):
        overall = body["overall"]

        assert overall["allowed_mates"] == 1
        assert overall["mate_sequences"] == 1
        assert overall["blunders"] == 1
        # One blunder plus one allowed mate. The labels are disjoint.
        assert overall["critical_mistakes"] == 2
        assert overall["numeric_cpl_moves"] < overall["total_player_moves"]

    def test_engine_agreement_reports_its_own_denominator(self, body):
        overall = body["overall"]

        assert overall["exact_best_move_count"] == 2
        assert overall["exact_best_move_comparable"] == 11
        assert overall["exact_best_move_percent"] == 18

    def test_coverage_move_counts_match_the_overall_metrics(self, body):
        assert body["coverage"]["graded_player_moves"] == body["overall"]["graded_moves"]
        assert body["coverage"]["numeric_cpl_moves"] == body["overall"]["numeric_cpl_moves"]

    def test_carries_the_derivation_version(self, body):
        assert body["move_quality_version"] == 1


# --- agreement with the per-game endpoint ---------------------------------


class TestAgreesWithPerGame:
    def test_totals_are_the_sum_of_the_games(self, analysis_client, analyzed_upload, db_session):
        """The aggregate is not allowed to be its own second opinion.

        Computed independently by the two endpoints, so an arithmetic change
        in one that is not made in the other shows up here rather than as a
        dashboard that contradicts the game it links to.
        """
        body = _quality(analysis_client, analyzed_upload)
        games = [_game(db_session, analyzed_upload, index) for index in range(3)]
        summaries = [_game_quality(analysis_client, game)["summary"] for game in games]

        for field in (
            "total_player_moves",
            "graded_moves",
            "numeric_cpl_moves",
            "unscored_moves",
            "inaccuracies",
            "mistakes",
            "blunders",
            "missed_mates",
            "allowed_mates",
            "mate_sequences",
            "critical_mistakes",
            "exact_best_move_count",
            "exact_best_move_comparable",
        ):
            assert body["overall"][field] == sum(s[field] for s in summaries), field


# --- splits ----------------------------------------------------------------


class TestByColor:
    def test_splits_both_colours_with_their_sample_sizes(
        self, analysis_client, analyzed_upload
    ):
        by_color = {row["color"]: row for row in _quality(analysis_client, analyzed_upload)["by_color"]}

        assert by_color["white"]["metrics"]["games"] == 2
        assert by_color["black"]["metrics"]["games"] == 1
        assert by_color["white"]["metrics"]["total_player_moves"] == 6
        assert by_color["black"]["metrics"]["total_player_moves"] == 5

    def test_reports_only_the_colour_that_has_games(
        self, analysis_client, work, engine_holder, db_session
    ):
        """A colour with no analysed games is absent, not present as zeroes.

        Zeroes read as a result - "you play badly as Black" - rather than as
        the absence of data.
        """
        upload_id = _upload(analysis_client)
        _analyze(
            analysis_client,
            work,
            engine_holder,
            _game(db_session, upload_id, 1),  # the only Black game
            GAME_1_EVALUATIONS,
        )

        by_color = _quality(analysis_client, upload_id)["by_color"]

        assert [row["color"] for row in by_color] == ["black"]

    def test_white_only_coverage(self, analysis_client, work, engine_holder, db_session):
        upload_id = _upload(analysis_client)
        _analyze(
            analysis_client, work, engine_holder, _game(db_session, upload_id, 0), GAME_0_EVALUATIONS
        )

        by_color = _quality(analysis_client, upload_id)["by_color"]

        assert [row["color"] for row in by_color] == ["white"]
        assert by_color[0]["metrics"]["games"] == 1


class TestByOpening:
    def test_one_bucket_per_opening_with_sample_sizes(
        self, analysis_client, analyzed_upload
    ):
        by_opening = _quality(analysis_client, analyzed_upload)["by_opening"]

        assert len(by_opening) == 3
        assert all(row["metrics"]["games"] == 1 for row in by_opening)
        assert all(row["opening"] for row in by_opening)

    def test_orders_by_games_then_name(self, analysis_client, analyzed_upload):
        by_opening = _quality(analysis_client, analyzed_upload)["by_opening"]
        names = [row["opening"] for row in by_opening]

        # Every game is its own opening here, so the tie-break is all that
        # decides the order - which is exactly the part that must be stable.
        assert names == sorted(names)

    def test_an_unclassified_game_gets_an_explicit_bucket_at_the_end(
        self, analysis_client, work, engine_holder, db_session
    ):
        """A structural null, not the string "Unknown".

        Rows imported before Sprint 3 carry no classification. Bucketing them
        under a name would make them indistinguishable from an opening
        actually called that.
        """
        upload_id = _upload(analysis_client)
        for index, evaluations in enumerate((GAME_0_EVALUATIONS, GAME_1_EVALUATIONS)):
            _analyze(
                analysis_client,
                work,
                engine_holder,
                _game(db_session, upload_id, index),
                evaluations,
            )

        unclassified = _game(db_session, upload_id, 0)
        unclassified.opening_name = None
        unclassified.eco = None
        db_session.flush()

        by_opening = _quality(analysis_client, upload_id)["by_opening"]

        assert by_opening[-1]["opening"] is None
        assert by_opening[-1]["metrics"]["games"] == 1
        assert sum(row["metrics"]["games"] for row in by_opening) == 2


# --- the review list -------------------------------------------------------


class TestTopReviewMoves:
    @pytest.fixture
    def moves(self, analysis_client, analyzed_upload):
        return _quality(analysis_client, analyzed_upload)["top_review_moves"]

    def test_lists_mistakes_and_worse_only(self, moves):
        # Game 1's 50-point loss is an inaccuracy and is deliberately absent.
        assert {move["quality"] for move in moves} <= {"mistake", "blunder", "critical"}
        assert len(moves) == 3

    def test_a_mate_error_outranks_every_centipawn_loss(self, moves):
        """Not because it scores higher - it has no score at all."""
        assert moves[0]["special_classification"] == "allowed_mate"
        assert moves[0]["centipawn_loss"] is None
        assert moves[0]["quality"] == "critical"

    def test_centipawn_losses_follow_worst_first(self, moves):
        assert [move["centipawn_loss"] for move in moves[1:]] == [300, 100]

    def test_carries_enough_to_link_straight_to_the_move(self, moves, db_session, analyzed_upload):
        first = moves[0]
        game = _game(db_session, analyzed_upload, 2)

        assert first["game_id"] == str(game.id)
        assert first["ply"] == 3
        assert first["san"] == "g4"
        assert first["player_color"] == "white"
        assert first["opening"]

    def test_every_referenced_game_belongs_to_this_upload(
        self, moves, db_session, analyzed_upload
    ):
        ids = {
            str(game_id)
            for game_id in db_session.execute(
                select(Game.id).where(Game.upload_id == analyzed_upload)
            ).scalars()
        }

        assert {move["game_id"] for move in moves} <= ids

    def test_equal_severity_ties_break_on_game_then_ply(
        self, analysis_client, work, engine_holder, db_session
    ):
        """Two identical blunders must not swap places between requests."""
        upload_id = _upload(analysis_client)

        # Game 0 and game 2, each with one 300-point loss on the player's
        # first move - the same severity, in different games.
        tie = [cp(0, best_move_uci="e2e4"), cp(-300)] + [cp(-300) for _ in range(9)]
        for index in (0, 2):
            _analyze(
                analysis_client, work, engine_holder, _game(db_session, upload_id, index), tie
            )

        moves = _quality(analysis_client, upload_id)["top_review_moves"]
        ordering = [(move["game_index"], move["ply"]) for move in moves]

        assert ordering == sorted(ordering)
        assert ordering[0][0] == 0
        assert _quality(analysis_client, upload_id)["top_review_moves"] == moves


# --- partial and empty coverage -------------------------------------------


class TestPartialCoverage:
    def test_the_denominator_is_complete_games_only(
        self, analysis_client, work, engine_holder, db_session
    ):
        """Three games, one analysed. The metrics are over one; the coverage
        block still reports all three."""
        upload_id = _upload(analysis_client)
        _analyze(
            analysis_client, work, engine_holder, _game(db_session, upload_id, 0), GAME_0_EVALUATIONS
        )

        body = _quality(analysis_client, upload_id)

        assert body["coverage"]["total_games"] == 3
        assert body["coverage"]["complete_games"] == 1
        assert body["coverage"]["not_analyzed_games"] == 2
        assert body["overall"]["games"] == 1
        assert body["overall"]["total_player_moves"] == 4

    def test_exactly_one_completed_game(self, analysis_client, work, engine_holder, db_session):
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 0)
        _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

        body = _quality(analysis_client, upload_id)

        assert body["overall"]["average_centipawn_loss"] == 140
        assert body["overall"] == {
            **_game_quality(analysis_client, game)["summary"],
            "games": 1,
        }

    def test_no_completed_games_reports_nulls_not_zeroes(self, analysis_client):
        upload_id = _upload(analysis_client)

        body = _quality(analysis_client, upload_id)

        assert body["coverage"]["complete_games"] == 0
        assert body["coverage"]["not_analyzed_games"] == 3
        assert body["overall"]["games"] == 0
        # "ACPL 0" would read as perfect play rather than as no sample.
        assert body["overall"]["average_centipawn_loss"] is None
        assert body["overall"]["median_centipawn_loss"] is None
        assert body["overall"]["exact_best_move_percent"] is None
        assert body["by_color"] == []
        assert body["by_opening"] == []
        assert body["top_review_moves"] == []

    def test_a_queued_game_is_counted_as_queued_not_complete(
        self, analysis_client, db_session
    ):
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 0)
        assert analysis_client.post(f"/api/games/{game.id}/analysis").status_code == 202

        coverage = _quality(analysis_client, upload_id)["coverage"]

        assert coverage["queued_games"] == 1
        assert coverage["complete_games"] == 0

    def test_an_analysis_in_flight_outranks_an_older_complete_run(self):
        """A game being re-analysed reports as running, not complete.

        The precedence is asserted on the bucketing function directly because
        no API route can currently produce this state - a completed analysis is
        reused rather than re-run. The claim is in the module docstring and in
        `game_analysis_state`, so it needs a guard now rather than when a
        re-analysis path is finally added and silently disagrees with the game
        page it links to.
        """
        game = Game(user_color="white")
        complete = AnalysisRun(status=STATUS_COMPLETE)

        assert (
            _coverage_bucket(
                game,
                active_job=AnalysisJob(status=JOB_RUNNING),
                complete_run=complete,
                latest_job=None,
                latest_run=complete,
            )
            == RUNNING
        )
        assert (
            _coverage_bucket(
                game,
                active_job=AnalysisJob(status=JOB_QUEUED),
                complete_run=complete,
                latest_job=None,
                latest_run=complete,
            )
            == QUEUED
        )

    def test_an_ungradable_colour_outranks_every_analysis_state(self):
        """No amount of analysis makes a game with no identifiable player
        gradable, so the colour check comes before all of them."""
        assert (
            _coverage_bucket(
                Game(user_color="unknown"),
                active_job=AnalysisJob(status=JOB_RUNNING),
                complete_run=AnalysisRun(status=STATUS_COMPLETE),
                latest_job=None,
                latest_run=None,
            )
            == NOT_GRADABLE
        )

    def test_a_cancelled_game_is_counted_as_cancelled(self, analysis_client, db_session):
        upload_id = _upload(analysis_client)
        game = _game(db_session, upload_id, 0)
        job_id = analysis_client.post(f"/api/games/{game.id}/analysis").json()["job_id"]
        assert analysis_client.post(f"/api/analysis/jobs/{job_id}/cancel").status_code == 200

        coverage = _quality(analysis_client, upload_id)["coverage"]

        assert coverage["cancelled_games"] == 1
        assert coverage["complete_games"] == 0


class TestNotGradable:
    def test_unknown_colour_games_are_excluded_and_counted_separately(
        self, analysis_client, work, engine_holder, db_session
    ):
        """`user_color` is "unknown" when the saved name matched neither side.

        Grading anyway would present an opponent's moves as the user's own, so
        the game is reported in its own bucket rather than folded into White.
        """
        upload_id = _upload(analysis_client, player_name="SomebodyElse")
        game = _game(db_session, upload_id, 0)
        _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

        body = _quality(analysis_client, upload_id)

        assert game.user_color == "unknown"
        assert body["coverage"]["not_gradable_games"] == 3
        assert body["coverage"]["complete_games"] == 0
        assert body["overall"]["games"] == 0
        assert body["overall"]["total_player_moves"] == 0

    def test_a_single_ungradable_game_does_not_remove_the_others(
        self, analysis_client, work, engine_holder, db_session
    ):
        upload_id = _upload(analysis_client)
        for index in (0, 1):
            _analyze(
                analysis_client,
                work,
                engine_holder,
                _game(db_session, upload_id, index),
                GAME_0_EVALUATIONS if index == 0 else GAME_1_EVALUATIONS,
            )

        ungradable = _game(db_session, upload_id, 1)
        ungradable.user_color = "unknown"
        db_session.flush()

        body = _quality(analysis_client, upload_id)

        assert body["coverage"]["not_gradable_games"] == 1
        assert body["coverage"]["complete_games"] == 1
        assert body["overall"]["games"] == 1


# --- one definition of "the current analysis" ------------------------------


class TestAgreesWithGameAnalysisState:
    def test_every_bucket_matches_the_games_own_endpoint(
        self, analysis_client, work, engine_holder, db_session
    ):
        """The aggregate must classify a game exactly as the game does.

        Two definitions of "current" would drift, and the drift would appear
        as a dashboard saying "40 analysed" beside a game page offering to
        analyse one of them.
        """
        upload_id = _upload(analysis_client)

        complete = _game(db_session, upload_id, 0)
        _analyze(analysis_client, work, engine_holder, complete, GAME_0_EVALUATIONS)

        queued = _game(db_session, upload_id, 1)
        assert analysis_client.post(f"/api/games/{queued.id}/analysis").status_code == 202

        untouched = _game(db_session, upload_id, 2)

        coverage = _quality(analysis_client, upload_id)["coverage"]
        states = {
            game.game_index: analysis_client.get(f"/api/games/{game.id}/analysis").json()["status"]
            for game in (complete, queued, untouched)
        }

        assert states == {0: "complete", 1: "queued", 2: "not_analyzed"}
        assert coverage["complete_games"] == 1
        assert coverage["queued_games"] == 1
        assert coverage["not_analyzed_games"] == 1


# --- read-only, and bounded ------------------------------------------------


class TestReadOnlyAndBounded:
    def test_writes_nothing(self, analysis_client, analyzed_upload, db_session):
        def _counts() -> tuple[int, int, int]:
            game_ids = list(
                db_session.execute(
                    select(Game.id).where(Game.upload_id == analyzed_upload)
                ).scalars()
            )
            runs = db_session.execute(
                select(func.count()).select_from(AnalysisRun).where(
                    AnalysisRun.game_id.in_(game_ids)
                )
            ).scalar_one()
            jobs = db_session.execute(
                select(func.count()).select_from(AnalysisJob).where(
                    AnalysisJob.game_id.in_(game_ids)
                )
            ).scalar_one()
            run_ids = list(
                db_session.execute(
                    select(AnalysisRun.id).where(AnalysisRun.game_id.in_(game_ids))
                ).scalars()
            )
            evaluations = db_session.execute(
                select(func.count()).select_from(PositionEvaluation).where(
                    PositionEvaluation.analysis_run_id.in_(run_ids)
                )
            ).scalar_one()
            return runs, jobs, evaluations

        before = _counts()
        _quality(analysis_client, analyzed_upload)

        assert _counts() == before

    def test_never_starts_an_engine(self, analysis_client, analyzed_upload, engine_holder):
        engine_holder["engine"] = FakeEngine()
        _quality(analysis_client, analyzed_upload)

        assert engine_holder["engine"].evaluated_fens == []

    def test_repeated_reads_are_identical(self, analysis_client, analyzed_upload):
        assert _quality(analysis_client, analyzed_upload) == _quality(
            analysis_client, analyzed_upload
        )

    def test_query_count_does_not_grow_with_the_number_of_games(
        self, analysis_client, work, engine_holder, db_session
    ):
        """The N+1 guard. Asserted, not assumed.

        A one-game upload and a three-game upload must cost the same number of
        SELECTs; if a per-game lookup ever creeps in, the two diverge.
        """
        # Same player name: a different one would make every game
        # `not_gradable`, and the two uploads would exercise different paths.
        small = _upload(analysis_client, pgn=SINGLE_GAME_PGN_BYTES)
        _analyze(
            analysis_client, work, engine_holder, _game(db_session, small, 0), GAME_0_EVALUATIONS
        )

        large = _upload(analysis_client)
        for index, evaluations in enumerate(
            (GAME_0_EVALUATIONS, GAME_1_EVALUATIONS, GAME_2_EVALUATIONS)
        ):
            _analyze(
                analysis_client,
                work,
                engine_holder,
                _game(db_session, large, index),
                evaluations,
            )

        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        def _count(upload_id: uuid.UUID) -> int:
            statements.clear()
            event.listen(db_engine, "before_cursor_execute", _record)
            try:
                _quality(analysis_client, upload_id)
            finally:
                event.remove(db_engine, "before_cursor_execute", _record)
            return sum(1 for s in statements if s.lstrip().upper().startswith("SELECT"))

        # Both must actually be complete, or the comparison is between two
        # different code paths rather than two library sizes.
        assert _quality(analysis_client, small)["coverage"]["complete_games"] == 1
        assert _quality(analysis_client, large)["coverage"]["complete_games"] == 3

        small_count = _count(small)
        large_count = _count(large)

        assert small_count == large_count
        # Games, moves, runs, jobs, evaluations, and the upload lookup.
        assert large_count <= 8


# --- recommendations, over real stored rows --------------------------------


class TestRecommendations:
    """The rules themselves are covered in test_training_recommendations.py.

    What is checked here is the join: that the endpoint reaches them with real
    aggregates, and that every position they point at is one of this upload's.
    """

    def test_a_thin_library_gets_only_what_it_supports(
        self, analysis_client, analyzed_upload
    ):
        """Three games is not enough to conclude anything about colours or
        openings, and the rules decline rather than guess."""
        recommendations = _quality(analysis_client, analyzed_upload)["recommendations"]

        assert [r["type"] for r in recommendations] == ["review_mistakes"]

    def test_an_unanalysed_library_gets_nothing_at_all(self, analysis_client):
        upload_id = _upload(analysis_client)

        assert _quality(analysis_client, upload_id)["recommendations"] == []

    def test_every_evidence_item_belongs_to_this_upload(
        self, analysis_client, analyzed_upload, db_session
    ):
        """The one containment guarantee that matters.

        A recommendation deep-links to a game and a ply. Pointing at a game
        from a different upload would send the reader somewhere they never
        asked about, and would leak that the other upload exists.
        """
        body = _quality(analysis_client, analyzed_upload)
        owned = {
            str(game_id)
            for game_id in db_session.execute(
                select(Game.id).where(Game.upload_id == analyzed_upload)
            ).scalars()
        }

        seen = 0
        for recommendation in body["recommendations"]:
            for item in recommendation["evidence_items"]:
                assert item["game_id"] in owned
                seen += 1

        assert seen > 0

    def test_evidence_matches_the_standalone_review_list(
        self, analysis_client, analyzed_upload
    ):
        """Same facts, same ranking. Two orderings would be two answers."""
        body = _quality(analysis_client, analyzed_upload)
        review = next(
            r for r in body["recommendations"] if r["type"] == "review_mistakes"
        )

        assert review["evidence_items"] == body["top_review_moves"][: len(review["evidence_items"])]

    def test_every_recommendation_declares_its_sample(
        self, analysis_client, work, engine_holder, db_session
    ):
        upload_id = _upload(analysis_client)
        _analyze(
            analysis_client, work, engine_holder, _game(db_session, upload_id, 0), GAME_0_EVALUATIONS
        )

        for recommendation in _quality(analysis_client, upload_id)["recommendations"]:
            assert "1 of 3 games" in recommendation["coverage_note"]

    def test_carries_the_scores_a_reader_needs_to_judge_the_claim(
        self, analysis_client, analyzed_upload
    ):
        body = _quality(analysis_client, analyzed_upload)
        item = body["recommendations"][0]["evidence_items"][0]

        assert item["score_before"] is not None
        assert item["score_after"] is not None
        assert item["game_id"] and item["ply"] and item["san"]


# --- the endpoint itself ---------------------------------------------------


class TestEndpoint:
    def test_unknown_upload_is_a_404(self, analysis_client):
        response = analysis_client.get(f"/api/uploads/{uuid.uuid4()}/move-quality")

        assert response.status_code == 404

    def test_existing_upload_endpoints_still_work(self, analysis_client, analyzed_upload):
        """Pagination and the upload list are untouched by this checkpoint."""
        listing = analysis_client.get("/api/uploads")
        stats = analysis_client.get(f"/api/uploads/{analyzed_upload}/stats")
        page = analysis_client.get(
            f"/api/uploads/{analyzed_upload}/games?page=1&page_size=2"
        )

        assert listing.status_code == 200
        assert stats.status_code == 200 and stats.json()["total_games"] == 3
        assert page.status_code == 200
        assert len(page.json()["items"]) == 2
        assert page.json()["total"] == 3
