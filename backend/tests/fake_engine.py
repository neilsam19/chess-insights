"""A deterministic in-memory ChessEngine for tests.

Lets the analysis code be exercised end to end without Stockfish installed,
which keeps the ordinary test suite free of a binary dependency.
"""

from app.services.engine import (
    EngineFailureError,
    EngineIdentity,
    EngineUnavailableError,
    RawEvaluation,
)


class FakeEngine:
    """Returns canned evaluations and records how it was used.

    `fail_on_call` makes the Nth `evaluate` call (1-based) raise, so mid-game
    failure can be tested deterministically.
    """

    def __init__(
        self,
        *,
        evaluations: list[RawEvaluation] | None = None,
        fail_on_call: int | None = None,
        fail_on_identify: bool = False,
        on_evaluate=None,
    ) -> None:
        self._evaluations = evaluations
        self._fail_on_call = fail_on_call
        self._fail_on_identify = fail_on_identify
        # Called with the 1-based call number before each evaluation. Lets a
        # test observe the database from another connection at a moment when
        # the engine is "thinking" - which is exactly when no transaction may
        # be held open.
        self._on_evaluate = on_evaluate

        self.closed = False
        self.close_count = 0
        self.evaluated_fens: list[str] = []
        self.depths: list[int] = []

    @property
    def call_count(self) -> int:
        return len(self.evaluated_fens)

    def identify(self) -> EngineIdentity:
        if self._fail_on_identify:
            raise EngineUnavailableError("fake engine cannot start")
        return EngineIdentity(
            name="FakeFish",
            version="1.0",
            binary_sha256="0" * 64,
            options={"Threads": 1, "Hash": 16},
        )

    def evaluate(self, fen: str, depth: int) -> RawEvaluation:
        self.evaluated_fens.append(fen)
        self.depths.append(depth)

        if self._on_evaluate is not None:
            self._on_evaluate(self.call_count)

        if self._fail_on_call is not None and self.call_count == self._fail_on_call:
            raise EngineFailureError("fake engine died mid-analysis")

        if self._evaluations is not None:
            index = self.call_count - 1
            if index < len(self._evaluations):
                return self._evaluations[index]

        # Default: a small, deterministic White edge that varies by ply so
        # ordering mistakes are visible in assertions.
        return RawEvaluation(
            score_cp_white=10 * self.call_count,
            mate_in_white=None,
            best_move_uci="e2e4",
            depth_reached=depth,
            nodes=1000 + self.call_count,
            time_ms=5,
        )

    def close(self) -> None:
        self.closed = True
        self.close_count += 1


def cp(value: int, *, best_move_uci: str | None = "e2e4") -> RawEvaluation:
    return RawEvaluation(
        score_cp_white=value,
        mate_in_white=None,
        best_move_uci=best_move_uci,
        depth_reached=12,
        nodes=1234,
        time_ms=7,
    )


def mate(value: int, *, best_move_uci: str | None = "d8h4") -> RawEvaluation:
    return RawEvaluation(
        score_cp_white=None,
        mate_in_white=value,
        best_move_uci=best_move_uci,
        depth_reached=12,
        nodes=4321,
        time_ms=9,
    )
