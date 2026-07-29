"""Injectable factories for engine analysis.

Analysis needs its own short-lived database sessions rather than the
request-scoped one, and it needs a real Stockfish process. Both are exposed
as dependencies so tests can override them the same way `get_db` is already
overridden - keeping the ordinary suite free of both a live engine and any
session that escapes the test transaction.
"""

from app.services.engine import build_stockfish_engine
from app.services.game_analysis_service import (
    EngineFactory,
    SessionFactory,
    _default_session,
)


def get_session_factory() -> SessionFactory:
    return _default_session


def get_engine_factory() -> EngineFactory:
    return build_stockfish_engine
