import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.repositories import games as game_repo
from app.repositories import uploads as upload_repo
from app.schemas.analysis import BatchCreatedOut, EngineSummaryOut
from app.services import analysis_batches
from app.services.engine import EngineUnavailableError, resolve_engine_config
from app.schemas.library import (
    GameSummaryOut,
    PaginatedGameSummariesOut,
    PaginatedUploadsOut,
    UploadListItemOut,
    UploadOut,
)
from app.schemas.upload import (
    GameOut,
    MoveOut,
    OpeningStatOut,
    UploadPreviewResponse,
    UploadStatsOut,
)
from app.services.import_service import (
    DuplicateUploadError,
    InvalidEncodingError,
    NoValidGamesError,
    import_pgn,
)
from app.services.library_stats import compute_upload_stats
from app.services.pgn_parser import parse_pgn_text
from app.services.statistics import compute_stats

router = APIRouter()


async def _read_validated_file(file: UploadFile) -> bytes:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file was uploaded.")

    raw_bytes = await file.read()

    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(raw_bytes) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")

    return raw_bytes


@router.post("/uploads/preview", response_model=UploadPreviewResponse)
async def preview_upload(
    file: UploadFile,
    player_name: str = Form(""),
) -> UploadPreviewResponse:
    raw_bytes = await _read_validated_file(file)

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
            # Canonical values from position-based classification - the raw
            # PGN headers stay on the ParsedGame for debugging only.
            eco=game.classification.eco if game.classification else game.eco,
            opening=game.classification.name if game.classification else game.opening,
            opening_source=game.classification.source if game.classification else None,
            opening_matched_ply=(
                game.classification.matched_ply if game.classification else None
            ),
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


@router.post("/uploads", response_model=UploadOut, status_code=201)
async def import_upload(
    file: UploadFile,
    player_name: str = Form(...),
    db: Session = Depends(get_db),
) -> UploadOut:
    if not player_name.strip():
        raise HTTPException(status_code=400, detail="player_name is required.")

    raw_bytes = await _read_validated_file(file)

    try:
        upload = import_pgn(
            db,
            original_filename=file.filename,
            raw_bytes=raw_bytes,
            player_name=player_name,
        )
    except DuplicateUploadError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This file has already been imported for this player.",
                "existing_upload_id": str(exc.existing_upload_id),
            },
        ) from exc
    except NoValidGamesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidEncodingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UploadOut.model_validate(upload)


@router.get("/uploads", response_model=PaginatedUploadsOut)
def list_uploads(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedUploadsOut:
    items, total = upload_repo.list_uploads(db, page=page, page_size=page_size)
    return PaginatedUploadsOut(
        items=[UploadListItemOut.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)) -> UploadOut:
    upload = upload_repo.get_upload(db, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return UploadOut.model_validate(upload)


@router.post(
    "/uploads/{upload_id}/analysis", response_model=BatchCreatedOut, status_code=202
)
def create_upload_analysis(
    upload_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
) -> BatchCreatedOut:
    """Queue every game in an upload that still needs analysing.

    The batch and all its jobs are written in one transaction, then the
    request returns; the worker does the work at its configured concurrency,
    so this cannot start an unbounded number of engines however many games are
    involved.

    Safe to press twice. Games with a matching completed analysis are skipped
    and games already queued are left alone, and the response says how many of
    each - so a second press is visibly uneventful rather than silently
    re-running everything.

      202  at least one game was queued
      200  nothing needed queueing
      404  upload not found
      503  the engine is unusable, and nothing was queued
    """
    upload = upload_repo.get_upload(db, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found.")

    try:
        outcome = analysis_batches.enqueue_upload_analysis(db, upload)
    except EngineUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    config = resolve_engine_config()
    status = (
        analysis_batches.batch_status(db, outcome.batch)
        if outcome.batch is not None
        else "complete"
    )
    if outcome.queued == 0:
        response.status_code = 200

    db.commit()

    return BatchCreatedOut(
        batch_id=outcome.batch.id if outcome.batch is not None else None,
        upload_id=upload_id,
        status=status,
        queued=outcome.queued,
        skipped_complete=outcome.skipped_complete,
        already_active=outcome.already_active,
        ineligible=outcome.ineligible,
        total_games=outcome.total_games,
        engine=EngineSummaryOut(
            requested_depth=config.depth, options=dict(config.options)
        ),
    )


@router.get("/uploads/{upload_id}/stats", response_model=UploadStatsOut)
def get_upload_stats(upload_id: uuid.UUID, db: Session = Depends(get_db)) -> UploadStatsOut:
    upload = upload_repo.get_upload(db, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found.")

    games = game_repo.list_all_games(db, upload_id)
    stats = compute_upload_stats(games, upload.player_name)

    return UploadStatsOut(
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


@router.get("/uploads/{upload_id}/games", response_model=PaginatedGameSummariesOut)
def get_upload_games(
    upload_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedGameSummariesOut:
    upload = upload_repo.get_upload(db, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found.")

    items, total = game_repo.list_game_summaries(db, upload_id, page=page, page_size=page_size)
    return PaginatedGameSummariesOut(
        items=[GameSummaryOut.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.delete("/uploads/{upload_id}", status_code=204)
def delete_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    upload = upload_repo.get_upload(db, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    upload_repo.delete_upload(db, upload)
