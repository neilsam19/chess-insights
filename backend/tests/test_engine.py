import chess
import chess.engine
import pytest

from app.services.engine import (
    EngineFailureError,
    EngineUnavailableError,
    _split_name_and_version,
    build_stockfish_engine,
    engine_session,
    normalize_score,
)
from fake_engine import FakeEngine


def pov(score: chess.engine.Score, turn: chess.Color) -> chess.engine.PovScore:
    return chess.engine.PovScore(score, turn)


class TestScoreNormalization:
    def test_white_advantage_is_positive_cp(self) -> None:
        cp_white, mate_white = normalize_score(pov(chess.engine.Cp(120), chess.WHITE))

        assert cp_white == 120
        assert mate_white is None

    def test_black_advantage_is_negative_cp(self) -> None:
        # Reported from White's point of view, Black better.
        cp_white, mate_white = normalize_score(pov(chess.engine.Cp(-85), chess.WHITE))

        assert cp_white == -85
        assert mate_white is None

    def test_score_reported_from_blacks_perspective_is_inverted(self) -> None:
        # +150 "for the side to move" when Black is to move means White is -150.
        cp_white, mate_white = normalize_score(pov(chess.engine.Cp(150), chess.BLACK))

        assert cp_white == -150
        assert mate_white is None

    def test_white_mate_is_positive(self) -> None:
        cp_white, mate_white = normalize_score(pov(chess.engine.Mate(3), chess.WHITE))

        assert mate_white == 3
        assert cp_white is None

    def test_black_mate_is_negative(self) -> None:
        cp_white, mate_white = normalize_score(pov(chess.engine.Mate(-2), chess.WHITE))

        assert mate_white == -2
        assert cp_white is None

    def test_mate_reported_from_blacks_perspective_is_inverted(self) -> None:
        # Black to move, mating in 2 -> canonical White view is mate in -2.
        cp_white, mate_white = normalize_score(pov(chess.engine.Mate(2), chess.BLACK))

        assert mate_white == -2
        assert cp_white is None

    @pytest.mark.parametrize(
        "score,turn",
        [
            (chess.engine.Cp(50), chess.WHITE),
            (chess.engine.Cp(-50), chess.BLACK),
            (chess.engine.Mate(1), chess.WHITE),
            (chess.engine.Mate(-1), chess.BLACK),
        ],
    )
    def test_cp_and_mate_are_never_both_populated(
        self, score: chess.engine.Score, turn: chess.Color
    ) -> None:
        cp_white, mate_white = normalize_score(pov(score, turn))

        assert (cp_white is None) != (mate_white is None)


class TestEngineIdentityParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Stockfish 17.1", ("Stockfish", "17.1")),
            ("Stockfish 16", ("Stockfish", "16")),
            ("Stockfish dev-20240101", ("Stockfish", "dev-20240101")),
            ("SomeEngine", ("SomeEngine", None)),
            ("  Stockfish 17  ", ("Stockfish", "17")),
        ],
    )
    def test_splits_name_and_version(self, raw: str, expected: tuple[str, str | None]) -> None:
        assert _split_name_and_version(raw) == expected


class TestEngineSession:
    def test_engine_is_closed_after_success(self) -> None:
        fake = FakeEngine()

        with engine_session(lambda: fake) as engine:
            engine.evaluate("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 12)

        assert fake.closed
        assert fake.close_count == 1

    def test_engine_is_closed_after_failure(self) -> None:
        fake = FakeEngine(fail_on_call=1)

        with pytest.raises(EngineFailureError):
            with engine_session(lambda: fake) as engine:
                engine.evaluate("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 12)

        assert fake.closed, "engine must be shut down even when analysis raises"

    def test_failure_midway_through_a_game_propagates(self) -> None:
        fake = FakeEngine(fail_on_call=3)
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

        with pytest.raises(EngineFailureError):
            with engine_session(lambda: fake) as engine:
                for _ in range(5):
                    engine.evaluate(fen, 12)

        assert fake.call_count == 3, "should stop at the failing position, not continue"
        assert fake.closed

    def test_startup_failure_propagates_as_unavailable(self) -> None:
        def _boom():
            raise EngineUnavailableError("no engine here")

        with pytest.raises(EngineUnavailableError):
            with engine_session(_boom):
                pass


class TestEngineConfiguration:
    def test_missing_stockfish_path_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services import engine as engine_module

        monkeypatch.setattr(engine_module.settings, "stockfish_path", None)

        with pytest.raises(EngineUnavailableError, match="not configured"):
            build_stockfish_engine()

    def test_nonexistent_executable_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        monkeypatch.setattr(
            engine_module.settings, "stockfish_path", str(tmp_path / "not-here.exe")
        )

        with pytest.raises(EngineUnavailableError, match="does not exist"):
            build_stockfish_engine()

    def test_directory_instead_of_executable_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.services import engine as engine_module

        monkeypatch.setattr(engine_module.settings, "stockfish_path", str(tmp_path))

        with pytest.raises(EngineUnavailableError, match="not a file"):
            build_stockfish_engine()


class TestFakeEngineContract:
    """The fake stands in for the real adapter in later checkpoints, so its
    shape needs to match what the coordinator will rely on."""

    def test_reports_identity(self) -> None:
        identity = FakeEngine().identify()

        assert identity.name == "FakeFish"
        assert identity.version == "1.0"
        assert identity.options == {"Threads": 1, "Hash": 16}

    def test_evaluations_are_deterministic_and_ordered(self) -> None:
        fake = FakeEngine()
        fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

        first = fake.evaluate(fen, 12)
        second = fake.evaluate(fen, 12)

        assert first.score_cp_white == 10
        assert second.score_cp_white == 20
        assert fake.depths == [12, 12]
