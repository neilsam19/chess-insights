"""Chess engine abstraction and the Stockfish adapter.

Everything that understands UCI, subprocesses, or python-chess score objects
lives here. The rest of the application sees only `ChessEngine`,
`EngineIdentity`, and `RawEvaluation`, so services can be tested with a fake
engine and the ordinary test suite never needs the real binary.

Scores are normalized to a single canonical convention on the way out:

    positive centipawns  -> White is better
    negative centipawns  -> Black is better
    positive mate value  -> White mates in N
    negative mate value  -> Black mates in N

Exactly one of `score_cp_white` / `mate_in_white` is populated. Converting to
a particular player's perspective is a presentation concern and happens
further out, never here.
"""

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

import chess
import chess.engine

from app.core.config import settings


class EngineError(Exception):
    """Base class for engine problems."""


class EngineUnavailableError(EngineError):
    """Stockfish is missing, misconfigured, or failed to start.

    This is a server configuration fault, not a failed analysis - it should
    surface as 503 rather than being recorded as a failed run.
    """


class EngineMisconfiguredError(EngineUnavailableError):
    """The engine started but rejected its configuration.

    A subclass of unavailable, because it is still a server configuration
    fault and still a 503 - but a distinguishable one, since a wrong option is
    fixed differently from a missing binary.
    """


class EngineFailureError(EngineError):
    """The engine started but failed during analysis.

    This *is* an analysis failure and should be recorded on the run.
    """


class EngineCrashedError(EngineFailureError):
    """The engine process died or misbehaved mid-analysis.

    Retryable: nothing about the request caused it, so the same work has a
    real chance of succeeding on another attempt.
    """


class InvalidPositionError(EngineFailureError):
    """A stored position the engine refused to analyze.

    Not retryable - the position will be exactly as invalid next time.
    """


class AmbiguousScoreError(EngineFailureError):
    """The engine returned neither a centipawn score nor a mate.

    Not retryable: it means the output could not be interpreted, and storing
    it as "no evaluation" would be a lie.
    """


@dataclass(frozen=True)
class EngineIdentity:
    name: str
    version: str | None
    binary_sha256: str | None
    options: dict[str, object]


@dataclass(frozen=True)
class EngineConfig:
    """Everything needed to identify an analysis configuration, resolved
    without starting a subprocess.

    The enqueue path needs to answer "is the engine usable, and what is this
    configuration's identity?" on every request. Launching Stockfish to find
    out would be absurd, so path validation and binary hashing are separated
    from actually running the engine.
    """

    path: str
    binary_sha256: str | None
    options: dict[str, object]
    depth: int


@dataclass(frozen=True)
class RawEvaluation:
    score_cp_white: int | None
    mate_in_white: int | None
    best_move_uci: str | None
    depth_reached: int | None
    nodes: int | None
    time_ms: int | None


class ChessEngine(Protocol):
    def identify(self) -> EngineIdentity: ...

    def evaluate(self, fen: str, depth: int) -> RawEvaluation: ...

    def close(self) -> None: ...


def normalize_score(pov_score: chess.engine.PovScore) -> tuple[int | None, int | None]:
    """Convert a python-chess PovScore into canonical White-perspective values.

    Returns `(score_cp_white, mate_in_white)` with exactly one populated.
    `PovScore.white()` handles the perspective flip, so a score reported from
    Black's point of view is inverted here rather than anywhere downstream.
    """
    white_score = pov_score.white()

    mate = white_score.mate()
    if mate is not None:
        return None, int(mate)

    centipawns = white_score.score()
    if centipawns is not None:
        return int(centipawns), None

    # Neither a centipawn nor a mate score - treated as malformed rather than
    # silently stored as "no evaluation".
    raise AmbiguousScoreError("Engine returned a score with neither centipawns nor mate.")


def _split_name_and_version(raw_name: str) -> tuple[str, str | None]:
    """UCI engines report a single id string, e.g. "Stockfish 17.1".

    Split off a trailing version token when one is present; otherwise report
    the whole string as the name and no version.
    """
    parts = raw_name.strip().split()
    if len(parts) >= 2 and any(ch.isdigit() for ch in parts[-1]):
        return " ".join(parts[:-1]), parts[-1]
    return raw_name.strip(), None


def _hash_binary(path: Path) -> str | None:
    """SHA-256 of the executable - reported UCI names don't pin an exact build."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# Hashing a ~50 MB executable on every enqueue would be silly, so the digest is
# cached per (path, mtime, size). Replacing the binary changes at least one of
# those, so a new build is picked up automatically rather than being served a
# stale hash - which matters, because the hash is part of analysis identity.
_binary_hash_cache: dict[tuple[str, int, int], str | None] = {}


def _cached_binary_hash(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None

    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _binary_hash_cache:
        _binary_hash_cache[key] = _hash_binary(path)
    return _binary_hash_cache[key]


def _validated_engine_path() -> Path:
    """The configured executable, or an explanation of why it is unusable.

    Deliberately refuses to search PATH: the executable comes from
    configuration only, never from a client and never from discovery.
    """
    if not settings.stockfish_path:
        raise EngineUnavailableError(
            "Engine analysis is not configured. Set STOCKFISH_PATH to a Stockfish executable."
        )

    path = Path(settings.stockfish_path)
    if not path.exists():
        raise EngineUnavailableError("The configured engine executable does not exist.")
    if not path.is_file():
        raise EngineUnavailableError("The configured engine path is not a file.")
    return path


def resolve_engine_config(depth: int | None = None) -> EngineConfig:
    """Validate configuration and derive analysis identity - without spawning.

    Raises `EngineUnavailableError` for a missing or unusable path, which the
    API turns into 503 with no job created. Starting no process is the point:
    this runs on every enqueue.
    """
    path = _validated_engine_path()
    return EngineConfig(
        path=str(path),
        binary_sha256=_cached_binary_hash(path),
        options={"Threads": settings.stockfish_threads, "Hash": settings.stockfish_hash_mb},
        depth=depth if depth is not None else settings.stockfish_depth,
    )


class StockfishEngine:
    """One Stockfish process, reused across every position in a game."""

    def __init__(
        self,
        engine_path: str,
        *,
        threads: int,
        hash_mb: int,
        binary_sha256: str | None = None,
    ) -> None:
        self._options: dict[str, object] = {"Threads": threads, "Hash": hash_mb}
        self._binary_sha256 = binary_sha256

        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(engine_path)
        except Exception as exc:  # noqa: BLE001 - any startup failure is a config fault
            raise EngineUnavailableError(f"Could not start the chess engine: {exc}") from exc

        try:
            self._engine.configure(self._options)
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise EngineMisconfiguredError(
                f"Could not configure the chess engine: {exc}"
            ) from exc

    def identify(self) -> EngineIdentity:
        raw_name = str(self._engine.id.get("name", "Unknown"))
        name, version = _split_name_and_version(raw_name)
        return EngineIdentity(
            name=name,
            version=version,
            binary_sha256=self._binary_sha256,
            options=dict(self._options),
        )

    def evaluate(self, fen: str, depth: int) -> RawEvaluation:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise InvalidPositionError(
                f"Refusing to analyze an invalid position: {exc}"
            ) from exc

        try:
            info = self._engine.analyse(board, chess.engine.Limit(depth=depth))
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001 - engine died or misbehaved mid-run
            raise EngineCrashedError(f"Engine analysis failed: {exc}") from exc

        pov_score = info.get("score")
        if pov_score is None:
            raise AmbiguousScoreError("Engine returned no score for a position.")

        score_cp_white, mate_in_white = normalize_score(pov_score)

        principal_variation = info.get("pv") or []
        best_move_uci = principal_variation[0].uci() if principal_variation else None

        time_seconds = info.get("time")

        return RawEvaluation(
            score_cp_white=score_cp_white,
            mate_in_white=mate_in_white,
            best_move_uci=best_move_uci,
            depth_reached=info.get("depth"),
            nodes=info.get("nodes"),
            time_ms=int(time_seconds * 1000) if time_seconds is not None else None,
        )

    def close(self) -> None:
        try:
            self._engine.quit()
        except Exception:  # noqa: BLE001 - a failed shutdown must not mask the real error
            pass


def build_stockfish_engine() -> StockfishEngine:
    """Start the configured engine, or explain why it isn't usable.

    Path validation and identity now come from `resolve_engine_config()`, so
    the launcher and the enqueue path can never disagree about which binary or
    options an analysis refers to.
    """
    config = resolve_engine_config()
    return StockfishEngine(
        config.path,
        threads=int(config.options["Threads"]),
        hash_mb=int(config.options["Hash"]),
        binary_sha256=config.binary_sha256,
    )


@contextmanager
def engine_session(
    factory: Callable[[], ChessEngine] = build_stockfish_engine,
) -> Iterator[ChessEngine]:
    """Yield an engine and guarantee shutdown, including on failure.

    Callers get the close-in-finally guarantee for free rather than each
    having to remember it.
    """
    engine = factory()
    try:
        yield engine
    finally:
        engine.close()
