"""The payload a future explanation feature would be handed.

The tests that matter here are not about what the payload contains but about
what it can never contain: a filesystem path, an environment value, a fact that
traces to nothing, or a sentence sitting where a measurement is read from.

Those are the properties that make it safe to send this somewhere else later.
They are asserted structurally - by walking every string in the serialized
payload - rather than by naming the fields that happen to exist today, so a
field added in a future sprint is covered the moment it appears.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.models.game import Game
from app.schemas.evidence import EVIDENCE_CONTRACT_VERSION, EvidenceEnvelope
from app.services.engine import RawEvaluation
from app.services.evidence import evidence_for_positions, game_evidence
from app.services.move_quality import MOVE_QUALITY_VERSION
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
    engine_holder["engine"] = FakeEngine(evaluations=evaluations)
    assert client.post(f"/api/games/{game.id}/analysis").status_code == 202
    work()


# Game 0: NeilSamant is White and delivers checkmate. Player plies 1, 3, 5, 7.
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

# Game 2: NeilSamant is White and is mated. Player plies 1, 3.
GAME_2_EVALUATIONS = [
    cp(20, best_move_uci="e2e4"),
    cp(-30),
    cp(-30, best_move_uci="d2d4"),
    mate(-1),
    mate(0),
]


@pytest.fixture
def analyzed(analysis_client, work, engine_holder, db_session):
    upload_id = _upload(analysis_client)
    game = _game(db_session, upload_id, 0)
    _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)
    return game


def _strings(value: object, path: str = "") -> list[tuple[str, str]]:
    """Every string leaf in a serialized payload, with where it came from."""
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [
            pair
            for key, item in value.items()
            for pair in _strings(item, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, list):
        return [
            pair
            for index, item in enumerate(value)
            for pair in _strings(item, f"{path}[{index}]")
        ]
    return []


def _payload(envelope: EvidenceEnvelope) -> dict:
    return envelope.model_dump(mode="json")


# The contract, with the source of every field. "stored" means a column of a
# row; "derived" means computed by move_quality from stored rows and nothing
# else. Nothing may be in the payload that is not in one of those two
# categories, and nothing may be here that is neither.
EXPECTED_FIELDS: dict[str, dict[str, str]] = {
    "game": {
        "game_id": "stored: games.id",
        "upload_id": "stored: games.upload_id",
        "game_index": "stored: games.game_index",
        "white_name": "stored: games.white_name",
        "black_name": "stored: games.black_name",
        "result": "stored: games.result",
        "played_date_text": "stored: games.played_date_text",
        "player_color": "stored: games.user_color",
        "player_result": "stored: games.user_result",
        "eco": "stored: games.eco",
        "opening_name": "stored: games.opening_name",
        "opening_source": "stored: games.opening_source",
        "opening_matched_ply": "stored: games.opening_matched_ply",
    },
    "move": {
        "move_row_id": "stored: moves.id",
        "ply": "stored: moves.ply",
        "move_number": "derived: ply arithmetic",
        "side": "derived: ply parity",
        "san": "stored: moves.san",
        "uci": "stored: moves.uci",
        "fen_before": "stored: moves.fen_before",
        "fen_after": "stored: moves.fen_after",
    },
    "engine": {
        "before_evaluation_id": "stored: position_evaluations.id",
        "after_evaluation_id": "stored: position_evaluations.id",
        "best_move_uci": "stored: position_evaluations.best_move_uci",
        "is_exact_best_move": "derived: CP1 best-move comparison",
        "depth_reached": "stored: position_evaluations.depth_reached",
        "nodes": "stored: position_evaluations.nodes",
        "time_ms": "stored: position_evaluations.time_ms",
    },
    "assessment": {
        "stored_white_score_before": "stored: position_evaluations score columns",
        "stored_white_score_after": "stored: position_evaluations score columns",
        "player_score_before": "derived: CP1 perspective and mate attribution",
        "player_score_after": "derived: CP1 perspective and mate attribution",
        "centipawn_loss": "derived: CP1 transition table",
        "quality": "derived: CP1 thresholds",
        "special_classification": "derived: CP1 transition table",
        "unscored_reason": "derived: CP1 reason codes",
    },
    "provenance": {
        "analysis_run_id": "stored: analysis_runs.id",
        "analysis_job_id": "stored: analysis_runs.analysis_job_id",
        "engine_name": "stored: analysis_runs.engine_name",
        "engine_version": "stored: analysis_runs.engine_version",
        "engine_binary_sha256": "stored: analysis_runs.engine_binary_sha256",
        "requested_depth": "stored: analysis_runs.requested_depth",
        "engine_options": "stored: analysis_runs.engine_options",
        "run_started_at": "stored: analysis_runs.started_at",
        "run_completed_at": "stored: analysis_runs.completed_at",
        "move_quality_version": "constant: MOVE_QUALITY_VERSION",
    },
}


# --- what it contains ------------------------------------------------------


class TestContents:
    def test_covers_every_graded_move(self, analyzed, db_session):
        envelope = game_evidence(db_session, analyzed)

        assert [item.move.ply for item in envelope.evidence] == [1, 3, 5, 7]
        assert {item.move.side for item in envelope.evidence} == {"white"}

    def test_carries_the_position_itself(self, analyzed, db_session):
        item = game_evidence(db_session, analyzed).evidence[0]

        assert item.move.san == "e4"
        assert item.move.uci == "e2e4"
        assert item.move.fen_before.startswith("rnbqkbnr/pppppppp")
        assert item.move.fen_after != item.move.fen_before

    def test_carries_both_the_stored_score_and_the_derived_one(self, analyzed, db_session):
        """The raw row and its interpretation, side by side.

        A consumer can check the derivation against the value it came from,
        and the player-relative form is the only one that can express who
        delivered a checkmate.
        """
        by_ply = {
            item.move.ply: item.assessment.model_dump()
            for item in game_evidence(db_session, analyzed).evidence
        }
        first = by_ply[1]

        assert first["stored_white_score_before"] == {"type": "cp", "value": 20}
        assert first["player_score_before"] == {"kind": "cp", "cp": 20, "mate_distance": None}
        assert first["centipawn_loss"] == 20

        checkmate = by_ply[7]
        assert checkmate["stored_white_score_after"] == {"type": "mate", "value": 0}
        # The signless stored zero, resolved by ply parity into a direction.
        assert checkmate["player_score_after"] == {
            "kind": "mate_for",
            "cp": None,
            "mate_distance": 0,
        }

    def test_points_at_the_exact_rows_it_was_built_from(self, analyzed, db_session):
        item = game_evidence(db_session, analyzed).evidence[0]

        assert item.move.move_row_id > 0
        assert item.engine.before_evaluation_id is not None
        assert item.engine.after_evaluation_id is not None
        assert item.engine.before_evaluation_id != item.engine.after_evaluation_id

    def test_carries_engine_provenance_and_both_versions(self, analyzed, db_session):
        item = game_evidence(db_session, analyzed).evidence[0]

        assert item.provenance.engine_name == "FakeFish"
        assert item.provenance.requested_depth == 12
        assert item.provenance.analysis_run_id
        assert item.provenance.move_quality_version == MOVE_QUALITY_VERSION
        assert game_evidence(db_session, analyzed).contract_version == EVIDENCE_CONTRACT_VERSION

    def test_search_statistics_come_from_the_position_the_player_faced(
        self, analyzed, db_session
    ):
        item = game_evidence(db_session, analyzed).evidence[0]

        assert item.engine.depth_reached == 12
        assert item.engine.best_move_uci == "e2e4"
        assert item.engine.is_exact_best_move is True


# --- what it must never contain -------------------------------------------


class TestLeaks:
    def test_contains_no_filesystem_path(self, analyzed, db_session):
        """Where the engine binary lives is a property of the machine, not of
        the analysis, and this payload is designed to be sendable elsewhere.

        Asserted over every string rather than over named fields, so a field
        added later is covered without anyone remembering to extend this.
        """
        absolute = re.compile(r"^(/|~|[A-Za-z]:[\\/])")

        for path, value in _strings(_payload(game_evidence(db_session, analyzed))):
            assert not absolute.match(value), f"{path} looks like a path: {value!r}"
            assert "\\" not in value, f"{path} contains a backslash: {value!r}"
            assert ".exe" not in value.lower(), f"{path} names a binary: {value!r}"

    def test_contains_no_configured_secret_or_location(self, analyzed, db_session):
        """The two settings that must never travel: the database URL, which
        carries credentials, and the engine path."""
        serialized = str(_payload(game_evidence(db_session, analyzed)))
        sensitive = [settings.database_url, settings.stockfish_path]

        for value in sensitive:
            if value:
                assert value not in serialized

    def test_contains_no_environment_values(self, analyzed, db_session, monkeypatch):
        monkeypatch.setenv("A_SECRET_FOR_THIS_TEST", "swordfish-4a1b2c3d")

        serialized = str(_payload(game_evidence(db_session, analyzed)))

        assert "swordfish-4a1b2c3d" not in serialized
        assert "A_SECRET_FOR_THIS_TEST" not in serialized

    def test_holds_no_free_text(self, analyzed, db_session):
        """Nowhere to put a sentence.

        Asserted as the contract itself rather than by guessing which strings
        look like prose: opening names run to eighty characters and half a
        dozen words, so any heuristic that tolerates them would tolerate a
        sentence too.

        `EXPECTED_FIELDS` is the contract. Every entry names where its value
        comes from, and adding a field without adding it here - a `notes` or a
        `summary`, say - fails this test, which is exactly the change that
        would let generated text into the facts.
        """
        item = game_evidence(db_session, analyzed).evidence[0].model_dump()

        assert set(item) == set(EXPECTED_FIELDS)
        for section, fields in EXPECTED_FIELDS.items():
            assert set(item[section]) == set(fields), section

    def test_the_prose_slot_is_empty_and_separate(self, analyzed, db_session):
        envelope = game_evidence(db_session, analyzed)

        assert envelope.explanation is None
        # And there is no such field inside the evidence itself.
        assert "explanation" not in envelope.evidence[0].model_dump()


# --- properties ------------------------------------------------------------


class TestProperties:
    def test_is_deterministic(self, analyzed, db_session):
        """Serialize twice, compare. A payload that varies between calls
        cannot be cached, diffed, or used to validate a citation."""
        assert _payload(game_evidence(db_session, analyzed)) == _payload(
            game_evidence(db_session, analyzed)
        )

    def test_every_value_traces_to_a_stored_row(self, analyzed, db_session, analysis_client):
        """Cross-checked against the endpoints rather than the implementation.

        If the evidence and the move-quality response ever disagree, an
        explanation built on one would contradict the screen showing the other.
        """
        envelope = game_evidence(db_session, analyzed)
        body = analysis_client.get(f"/api/games/{analyzed.id}/move-quality").json()
        moves = {move["ply"]: move for move in body["moves"]}

        for item in envelope.evidence:
            move = moves[item.move.ply]
            assert item.assessment.centipawn_loss == move["centipawn_loss"]
            assert item.assessment.quality == move["quality"]
            assert item.assessment.special_classification == move["special_classification"]
            before = item.assessment.player_score_before
            assert (None if before is None else before.model_dump()) == move["score_before"]
            assert item.engine.is_exact_best_move == move["is_exact_best_move"]
            assert str(item.provenance.analysis_run_id) == body["analysis_run_id"]

    def test_reads_nothing_and_starts_no_engine(self, analyzed, db_session, engine_holder):
        engine_holder["engine"] = FakeEngine()
        before = db_session.execute(
            select(Game.id).where(Game.upload_id == analyzed.upload_id)
        ).scalars().all()

        game_evidence(db_session, analyzed)

        assert engine_holder["engine"].evaluated_fens == []
        assert (
            db_session.execute(
                select(Game.id).where(Game.upload_id == analyzed.upload_id)
            ).scalars().all()
            == before
        )


# --- absence is a normal state ---------------------------------------------


class TestAbsence:
    def test_an_unanalysed_game_yields_an_empty_envelope(self, analysis_client, db_session):
        upload_id = _upload(analysis_client)

        envelope = game_evidence(db_session, _game(db_session, upload_id, 1))

        assert envelope.evidence == []
        assert envelope.contract_version == EVIDENCE_CONTRACT_VERSION

    def test_an_ungradable_game_yields_an_empty_envelope(
        self, analysis_client, work, engine_holder, db_session
    ):
        """No identifiable player means nothing can be said about their moves,
        however much analysis exists."""
        upload_id = _upload(analysis_client, player_name="SomebodyElse")
        game = _game(db_session, upload_id, 0)
        _analyze(analysis_client, work, engine_holder, game, GAME_0_EVALUATIONS)

        assert game.user_color == "unknown"
        assert game_evidence(db_session, game).evidence == []

    def test_narrowing_to_plies_returns_only_those(self, analyzed, db_session):
        envelope = game_evidence(db_session, analyzed, plies=[3, 7])

        assert [item.move.ply for item in envelope.evidence] == [3, 7]

    def test_an_unknown_ply_is_ignored_rather_than_invented(self, analyzed, db_session):
        envelope = game_evidence(db_session, analyzed, plies=[3, 999])

        assert [item.move.ply for item in envelope.evidence] == [3]


# --- citing specific positions --------------------------------------------


class TestCitedPositions:
    def test_gathers_evidence_across_games(
        self, analysis_client, work, engine_holder, db_session
    ):
        upload_id = _upload(analysis_client)
        first = _game(db_session, upload_id, 0)
        third = _game(db_session, upload_id, 2)
        _analyze(analysis_client, work, engine_holder, first, GAME_0_EVALUATIONS)
        _analyze(analysis_client, work, engine_holder, third, GAME_2_EVALUATIONS)

        envelope = evidence_for_positions(
            db_session, [(first.id, 5), (third.id, 3)], [first, third]
        )

        assert {(item.game.game_index, item.move.ply) for item in envelope.evidence} == {
            (0, 5),
            (2, 3),
        }

    def test_cannot_reach_a_game_the_caller_did_not_supply(
        self, analysis_client, work, engine_holder, db_session
    ):
        """Ownership is the caller's to establish, and this must not widen it.

        A recommendation cites positions from one upload; being able to
        assemble evidence for a game outside it would leak that the game
        exists.
        """
        upload_id = _upload(analysis_client)
        first = _game(db_session, upload_id, 0)
        third = _game(db_session, upload_id, 2)
        _analyze(analysis_client, work, engine_holder, first, GAME_0_EVALUATIONS)
        _analyze(analysis_client, work, engine_holder, third, GAME_2_EVALUATIONS)

        envelope = evidence_for_positions(
            db_session, [(first.id, 5), (third.id, 3)], [first]
        )

        assert {item.game.game_index for item in envelope.evidence} == {0}
