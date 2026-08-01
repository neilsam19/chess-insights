import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.game import Game
from app.schemas.analysis import GameAnalysisOut
from app.schemas.library import GameDetailOut
from app.services import analysis_enqueue
from app.services.analysis_executor import InvalidGameDataError
from app.services.analysis_view import game_analysis_state
from app.services.engine import EngineUnavailableError
from app.repositories import games as game_repo

router = APIRouter()


@router.get("/games/{game_id}", response_model=GameDetailOut)
def get_game(game_id: uuid.UUID, db: Session = Depends(get_db)) -> GameDetailOut:
    game = game_repo.get_game_with_moves(db, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    return GameDetailOut.model_validate(game)


def _game_or_404(db: Session, game_id: uuid.UUID) -> Game:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    return game


@router.get("/games/{game_id}/analysis", response_model=GameAnalysisOut)
def get_game_analysis(game_id: uuid.UUID, db: Session = Depends(get_db)) -> GameAnalysisOut:
    """Everything a client needs to render analysis, in one shape.

    Reports queued and running states with progress as well as finished ones,
    so a client can poll this single endpoint from request to result.
    """
    return game_analysis_state(db, _game_or_404(db, game_id))


@router.post("/games/{game_id}/analysis", response_model=GameAnalysisOut, status_code=202)
def create_game_analysis(
    game_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
) -> GameAnalysisOut:
    """Queue one saved game for analysis and return immediately.

    Takes nothing but the game id - positions, engine identity, and depth all
    come from the server, never from the request body.

    The request does not wait for Stockfish. It commits a durable job and
    returns; a separate worker process does the analysis. That is why this is
    202 rather than 201: accepted, not done.

      202  queued
      200  a matching completed analysis already existed and was reused
      409  matching work is already queued or running
      422  the stored game cannot be analysed
      503  the engine is unusable - and no job is created, because a
           misconfigured server is not a failed analysis
    """
    game = _game_or_404(db, game_id)

    try:
        outcome = analysis_enqueue.enqueue_game_analysis(db, game)
    except InvalidGameDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    if outcome.result == analysis_enqueue.REUSED:
        response.status_code = 200
    elif outcome.result == analysis_enqueue.CONFLICT:
        response.status_code = 409

    db.commit()
    return game_analysis_state(db, game)
