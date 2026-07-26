import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import games as game_repo
from app.schemas.library import GameDetailOut

router = APIRouter()


@router.get("/games/{game_id}", response_model=GameDetailOut)
def get_game(game_id: uuid.UUID, db: Session = Depends(get_db)) -> GameDetailOut:
    game = game_repo.get_game_with_moves(db, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    return GameDetailOut.model_validate(game)
