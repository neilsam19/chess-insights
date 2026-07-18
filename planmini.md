# Mini Plan: First Vertical Slice

Goal: Upload a PGN -> FastAPI parses it -> React shows stats + lets the user step
through one game on a board. No database, auth, Stockfish, Docker, or LLM yet.

## Backend

1. `backend/app/core/config.py` - Settings (CORS origin for Vite, max upload size in bytes).
2. `backend/app/main.py` - FastAPI app instance, CORS middleware, mounts routers.
3. `backend/app/api/routes/health.py` - `GET /api/health` -> `{"status": "ok"}`.
4. `backend/app/schemas/upload.py` - Pydantic response models: `MoveOut`, `GameOut`
   (headers, color, result, moves), `UploadStatsOut` (totals, W/D/L, win %, score %,
   color splits, openings), `UploadPreviewResponse` (games, stats, warnings).
5. `backend/app/services/pgn_parser.py` - Pure function(s): PGN text -> list of parsed
   games (headers, per-ply SAN/UCI/FEN-before/FEN-after). Skips unreadable individual
   games and collects warnings instead of failing the whole file.
6. `backend/app/services/statistics.py` - Pure function(s): list of parsed games + the
   user's player name -> aggregate stats (wins/draws/losses/unknown, win %, score %,
   white/black counts, opening frequency from ECO/Opening headers).
7. `backend/app/api/routes/uploads.py` - `POST /api/uploads/preview`, multipart
   (`file`, `player_name`). Validates: file present, non-empty, under size limit,
   at least one game parses. Wires parser -> statistics -> response schema. No disk
   writes, nothing persisted.
8. `backend/tests/test_pgn_parser.py` - one valid game, multiple games in one file,
   missing optional headers, castling/promotion, malformed game recovery.
9. `backend/tests/test_statistics.py` - win/draw/loss as White, win/draw/loss as
   Black, unknown result, score % math.
10. `backend/tests/test_uploads_route.py` - empty upload rejected, oversized upload
    rejected, valid upload returns expected shape (using `sample-data/sample-games.pgn`).

## Frontend

11. `frontend/src/types/upload.ts` - TS interfaces mirroring the backend schemas
    exactly (`Move`, `Game`, `UploadStats`, `UploadPreviewResponse`).
12. `frontend/src/api/uploads.ts` - `postUploadPreview(file, playerName)` using native
    `fetch` + `FormData`, throws a typed error on non-2xx.
13. `frontend/src/components/FileUpload.tsx` - player name input + `.pgn` file input +
    submit; surfaces loading/validation/server-error states to its parent.
14. `frontend/src/components/StatCard.tsx` - small reusable stat tile.
15. `frontend/src/components/DashboardSummary.tsx` - grid of `StatCard`s (totals,
    W/D/L, win %, score %, White vs Black) + an opening frequency table.
16. `frontend/src/components/GameList.tsx` - selectable list of parsed games
    (White vs Black, result), emits the selected game index.
17. `frontend/src/components/MoveList.tsx` - move list, highlights the current ply,
    click-to-jump.
18. `frontend/src/components/ChessGameViewer.tsx` - `react-chessboard` driven purely
    by `positions[currentPly]` from the backend FENs; start/prev/next/end controls.
19. `frontend/src/pages/UploadPage.tsx` - owns the upload result + selected game
    state, composes `FileUpload` -> `DashboardSummary` + `GameList` ->
    `ChessGameViewer`/`MoveList`.
20. Wire `UploadPage` as the app's single route in `App.tsx` (no router complexity
    needed yet).

## Verification (must pass before calling the milestone done)

- `pytest` in `backend/` green.
- `npm run build` (tsc) and `npm run lint` clean in `frontend/`.
- Manual pass via `bash dev.sh`: enter a player name, upload
  `sample-data/sample-games.pgn`, confirm stats + opening table render, select a
  game, step through it with start/prev/next/end and click-to-jump in the move list.

## Explicitly out of scope for this slice

PostgreSQL/SQLAlchemy/Alembic, Docker, Stockfish, authentication, charts, LLM
explanations, deployment.
