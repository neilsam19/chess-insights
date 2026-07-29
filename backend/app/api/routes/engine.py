from fastapi import APIRouter, Depends

from app.api.dependencies import get_engine_factory
from app.core.config import settings
from app.schemas.analysis import EngineStatusOut
from app.services.engine import EngineError, engine_session

router = APIRouter()


@router.get("/engine/status", response_model=EngineStatusOut)
def get_engine_status(engine_factory=Depends(get_engine_factory)) -> EngineStatusOut:
    """Report whether engine analysis is usable.

    Starts the engine, asks its identity, and shuts it down - deliberately no
    analysis, so this stays fast enough for the UI to call on page load.
    """
    try:
        with engine_session(engine_factory) as engine:
            identity = engine.identify()
    except EngineError as exc:
        return EngineStatusOut(
            available=False,
            configured_depth=settings.stockfish_depth,
            detail=str(exc),
        )

    return EngineStatusOut(
        available=True,
        engine_name=identity.name,
        engine_version=identity.version,
        configured_depth=settings.stockfish_depth,
    )
