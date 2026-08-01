"""Injectable factories for the API layer.

Only `/api/engine/status` still touches an engine: it starts one, asks its
identity, and shuts down. No other endpoint does, because analysis moved to
the worker in Sprint 5 - the API's job is to write a durable record and
return.

The factory is a dependency so tests can override it the way `get_db` is
already overridden, keeping the ordinary suite free of a live binary.
"""

from contextlib import AbstractContextManager
from typing import Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.engine import ChessEngine, build_stockfish_engine

SessionFactory = Callable[[], AbstractContextManager[Session]]
EngineFactory = Callable[[], ChessEngine]


def default_session() -> AbstractContextManager[Session]:
    return SessionLocal()


def get_session_factory() -> SessionFactory:
    return default_session


def get_engine_factory() -> EngineFactory:
    return build_stockfish_engine
