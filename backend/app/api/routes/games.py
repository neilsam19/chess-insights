import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_engine_factory, get_session_factory
from app.core.database import get_db
from app.models.analysis_run import STATUS_FAILED, STATUS_RUNNING
from app.models.game import Game
from app.repositories import analysis as analysis_repo
from app.repositories import games as game_repo
from app.schemas.analysis import (
    FailedAnalysisOut,
    GameAnalysisOut,
    NotAnalyzedOut,
    RunningAnalysisOut,
)
from app.schemas.library import GameDetailOut
from app.services.analysis_view import complete_analysis_out
from app.services.engine import EngineUnavailableError
from app.services.game_analysis_service import (
    AnalysisAlreadyRunningError,
    GameNotFoundError,
    InvalidGameDataError,
    analyze_game,
)

router = APIRouter()


@router.get("/games/{game_id}", response_model=GameDetailOut)
def get_game(game_id: uuid.UUID, db: Session = Depends(get_db)) -> GameDetailOut:
    game = game_repo.get_game_with_moves(db, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    return GameDetailOut.model_validate(game)


def _analysis_state(db: Session, game_id: uuid.UUID) -> GameAnalysisOut:
    """Current analysis state for a game, derived from its latest run.

    "not_analyzed" is never stored - it is simply the absence of a run.
    """
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")

    run = analysis_repo.get_latest_analysis_run_for_game(db, game_id)
    if run is None:
        return NotAnalyzedOut(game_id=game_id)

    if run.status == STATUS_RUNNING:
        return RunningAnalysisOut(game_id=game_id, run_id=run.id, started_at=run.started_at)

    if run.status == STATUS_FAILED:
        return FailedAnalysisOut(
            game_id=game_id,
            run_id=run.id,
            started_at=run.started_at,
            completed_at=run.completed_at,
            error_message=run.error_message,
        )

    evaluations = analysis_repo.list_position_evaluations_for_run(db, run.id)
    return complete_analysis_out(game, run, evaluations)


@router.get("/games/{game_id}/analysis", response_model=GameAnalysisOut)
def get_game_analysis(game_id: uuid.UUID, db: Session = Depends(get_db)) -> GameAnalysisOut:
    return _analysis_state(db, game_id)


# Deliberately a plain `def`: FastAPI runs it in a worker thread, so the
# blocking engine call cannot stall the event loop. Declaring it `async def`
# would freeze the whole server for the duration of the analysis.
@router.post("/games/{game_id}/analysis", response_model=GameAnalysisOut, status_code=201)
def create_game_analysis(
    game_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
    engine_factory=Depends(get_engine_factory),
) -> GameAnalysisOut:
    """Analyze one saved game.

    Takes nothing but the game id - positions, engine identity, and depth all
    come from the server. Synchronous for Sprint 4; Sprint 5 replaces this
    with a persistent job.
    """
    try:
        result = analyze_game(
            game_id, session_factory=session_factory, engine_factory=engine_factory
        )
    except GameNotFoundError:
        raise HTTPException(status_code=404, detail="Game not found.") from None
    except AnalysisAlreadyRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Analysis is already running for this game.",
                "run_id": str(exc.run_id),
            },
        ) from None
    except InvalidGameDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    if not result.created:
        # An existing complete run was returned rather than re-analyzed.
        response.status_code = 200

    db.expire_all()  # the coordinator committed on its own sessions
    return _analysis_state(db, game_id)
