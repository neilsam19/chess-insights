from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.core.config import settings
from app.schemas.upload import (
    GameOut,
    MoveOut,
    OpeningStatOut,
    UploadPreviewResponse,
    UploadStatsOut,
)
from app.services.pgn_parser import parse_pgn_text
from app.services.statistics import compute_stats

router = APIRouter()


@router.post("/uploads/preview", response_model=UploadPreviewResponse)
async def preview_upload(
    file: UploadFile,
    player_name: str = Form(...),
) -> UploadPreviewResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file was uploaded.")

    if not player_name.strip():
        raise HTTPException(status_code=400, detail="player_name is required.")

    raw_bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(raw_bytes) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")

    try:
        pgn_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text.") from exc

    parse_result = parse_pgn_text(pgn_text)

    if not parse_result.games:
        raise HTTPException(
            status_code=422, detail="No valid games could be parsed from the file."
        )

    classifications, stats = compute_stats(parse_result.games, player_name)

    games_out = [
        GameOut(
            index=index,
            white=game.white,
            black=game.black,
            white_elo=game.white_elo,
            black_elo=game.black_elo,
            date=game.date,
            event=game.event,
            result=game.result,
            eco=game.eco,
            opening=game.opening,
            player_color=classification.color,
            player_result=classification.result,
            moves=[MoveOut(**vars(move)) for move in game.moves],
        )
        for index, (game, classification) in enumerate(
            zip(parse_result.games, classifications)
        )
    ]

    stats_out = UploadStatsOut(
        total_games=stats.total_games,
        wins=stats.wins,
        draws=stats.draws,
        losses=stats.losses,
        unknown_results=stats.unknown_results,
        win_percentage=stats.win_percentage,
        score_percentage=stats.score_percentage,
        games_as_white=stats.games_as_white,
        games_as_black=stats.games_as_black,
        openings=[
            OpeningStatOut(
                name=opening.name,
                games=opening.games,
                wins=opening.wins,
                draws=opening.draws,
                losses=opening.losses,
                score_percentage=opening.score_percentage,
            )
            for opening in stats.openings
        ],
    )

    return UploadPreviewResponse(
        games=games_out, stats=stats_out, warnings=parse_result.warnings
    )
