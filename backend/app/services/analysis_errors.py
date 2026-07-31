"""Error codes for analysis jobs, and the retry policy built on them.

One module owns the whole question "what went wrong, and should we try again?".
The alternative - deciding retryability at each call site, or worse, by
inspecting message text - is how a queue ends up retrying a permanently broken
game forever while giving up instantly on a blip.

The rule is simple: retry only what is plausibly transient. A crashed engine or
a dead worker had nothing to do with the request, so another attempt has a real
chance. A zero-move game or an unreadable position will be exactly as broken
next time, so retrying only wastes engine time and hides the real problem.

These codes are the contract described in
`docs/adr/0001-durable-analysis-jobs.md` section 6, and they are what the API
exposes as `error_code`.
"""

from app.services.analysis_executor import (
    MAX_ERROR_MESSAGE_LENGTH,
    GameNotFoundError,
    InvalidGameDataError,
)
from app.services.engine import (
    AmbiguousScoreError,
    EngineError,
    EngineMisconfiguredError,
    EngineUnavailableError,
    InvalidPositionError,
)

# --- Retryable: plausibly transient ---------------------------------------

WORKER_LEASE_EXPIRED = "worker_lease_expired"
ENGINE_CRASHED = "engine_crashed"
DATABASE_UNAVAILABLE = "database_unavailable"

# --- Not retryable: deterministic, or the user's decision ------------------

INVALID_GAME_DATA = "invalid_game_data"
INVALID_POSITION = "invalid_position"
ENGINE_UNAVAILABLE = "engine_unavailable"
ENGINE_MISCONFIGURED = "engine_misconfigured"
AMBIGUOUS_SCORE = "ambiguous_score"
EVALUATION_COUNT_MISMATCH = "evaluation_count_mismatch"
CANCELLED_BY_USER = "cancelled_by_user"

# An unrecognised failure. Deliberately *not* retryable: an error nobody
# anticipated is the last thing that should be repeated automatically. It also
# reads as a signal in the data that something needs classifying properly.
UNEXPECTED_ERROR = "unexpected_error"

RETRYABLE_CODES = frozenset({WORKER_LEASE_EXPIRED, ENGINE_CRASHED, DATABASE_UNAVAILABLE})

ALL_CODES = frozenset(
    {
        WORKER_LEASE_EXPIRED,
        ENGINE_CRASHED,
        DATABASE_UNAVAILABLE,
        INVALID_GAME_DATA,
        INVALID_POSITION,
        ENGINE_UNAVAILABLE,
        ENGINE_MISCONFIGURED,
        AMBIGUOUS_SCORE,
        EVALUATION_COUNT_MISMATCH,
        CANCELLED_BY_USER,
        UNEXPECTED_ERROR,
    }
)


def classify(exc: Exception) -> str:
    """Map an exception to its error code.

    Ordered most specific first: `EngineMisconfiguredError` is a subclass of
    `EngineUnavailableError`, and the crashed / invalid-position / ambiguous
    engine failures are all subclasses of `EngineFailureError`.
    """
    if isinstance(exc, EngineMisconfiguredError):
        return ENGINE_MISCONFIGURED
    if isinstance(exc, EngineUnavailableError):
        return ENGINE_UNAVAILABLE
    if isinstance(exc, InvalidPositionError):
        return INVALID_POSITION
    if isinstance(exc, AmbiguousScoreError):
        return AMBIGUOUS_SCORE
    if isinstance(exc, EngineError):
        return ENGINE_CRASHED
    if isinstance(exc, (InvalidGameDataError, GameNotFoundError)):
        # A game deleted mid-run takes its job with it by cascade, so
        # GameNotFoundError after a claim is effectively unreachable.
        return INVALID_GAME_DATA
    return UNEXPECTED_ERROR


def is_retryable(code: str) -> bool:
    return code in RETRYABLE_CODES


def safe_message(exc: Exception) -> str:
    """A user-visible message that never leaks paths, secrets, or tracebacks.

    Engine and stored-data messages are written by this application and are
    safe to show. Anything else is replaced wholesale rather than trimmed,
    because an unanticipated exception can carry anything at all.
    """
    if isinstance(exc, (EngineError, InvalidGameDataError, GameNotFoundError)):
        message = str(exc) or "Engine analysis failed."
    else:
        message = "Analysis failed unexpectedly."
    return message[:MAX_ERROR_MESSAGE_LENGTH]


__all__ = [
    "ALL_CODES",
    "AMBIGUOUS_SCORE",
    "CANCELLED_BY_USER",
    "DATABASE_UNAVAILABLE",
    "ENGINE_CRASHED",
    "ENGINE_MISCONFIGURED",
    "ENGINE_UNAVAILABLE",
    "EVALUATION_COUNT_MISMATCH",
    "INVALID_GAME_DATA",
    "INVALID_POSITION",
    "RETRYABLE_CODES",
    "UNEXPECTED_ERROR",
    "WORKER_LEASE_EXPIRED",
    "classify",
    "is_retryable",
    "safe_message",
]
