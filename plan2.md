# Plan 2: Persistent Analysis Library (Sprint 2)

## Progress so far (resume point)

Done and verified (25/25 pytest green, `alembic check` clean):

- `compose.yaml` - Postgres service. **Host port is 5433, not 5432** - this
  machine has a native Windows Postgres service already squatting on 5432
  (see dev-env memory / `plan2.md` history below if that ever changes).
- `backend/.env` + `.env.example` - `DATABASE_URL` pointing at port 5433.
- `backend/app/core/config.py` - added `database_url` setting.
- `backend/app/core/database.py` - sync engine + `get_db()` session
  dependency (commit/rollback at request boundary).
- `backend/app/models/{base,upload,game,move}.py` - SQLAlchemy 2.x typed
  models for all three tables, cascading FKs, the two unique constraints,
  both indexes. `app/models/__init__.py` re-exports all of them for
  Alembic's `target_metadata`.
- `backend/alembic/` initialized, `env.py` wired to `settings.database_url`
  and `Base.metadata`. Initial migration
  `78f347d304f9_create_uploads_games_and_moves.py` generated, reviewed,
  and applied (`alembic upgrade head`). All 3 tables + `alembic_version`
  confirmed present in the DB.
- `backend/app/services/pgn_parser.py` - extended `ParsedGame` with
  `site`, `round`, `raw_pgn` (needed for the `games` table's NOT NULL
  `raw_pgn` and nullable `site`/`round` columns; `raw_pgn` is produced via
  `game.accept(chess.pgn.StringExporter(...))`). Updated
  `tests/test_statistics.py`'s `make_game` helper for the new required
  fields.

Also done (write path, verified against real Postgres, 33/33 tests green):

- `app/repositories/uploads.py` + `games.py` - thin query functions
  (`find_by_file_and_player`, `create_upload`, `list_uploads`, `get_upload`,
  `delete_upload`, `list_game_summaries`, `get_game_with_moves`).
- `app/services/import_service.py` - `import_pgn()`: hashes the file,
  checks `(file_sha256, normalized_player_name)` for a duplicate (raises
  `DuplicateUploadError` with the existing id), parses with the existing
  `pgn_parser`, classifies with the existing `statistics.classify_game`,
  builds `Game`+`Move` ORM rows and adds them - never commits itself
  (that's the request-scoped session's job at the boundary, so a mid-loop
  failure rolls back everything).
- `app/schemas/library.py` - `UploadOut` so far (more schemas needed once
  the retrieval endpoints are built).
- `POST /api/uploads` in `app/api/routes/uploads.py` - the confirmed
  import endpoint; `/uploads/preview` untouched/still stateless. Shared
  file-validation logic factored into `_read_validated_file()`.
- `backend/tests/conftest.py` - `db_session`/`client` fixtures using a
  real Postgres connection wrapped in a transaction + SAVEPOINT
  (`join_transaction_mode="create_savepoint"`) that rolls back after each
  test - integration tests run against the real DB but never leave data
  behind.
- `backend/tests/test_import_service.py` - 8 tests: persistence,
  ply-ordering, duplicate detection (409 + existing id), same file/
  different player, blank player_name rejection, multiple independent
  uploads, and transaction-rollback-on-simulated-failure.

Also done (retrieval endpoints, verified against real Postgres, 43/43
tests green, `alembic check` clean):

- `app/schemas/library.py` - `UploadListItemOut`, `PaginatedUploadsOut`,
  `GameSummaryOut`, `PaginatedGameSummariesOut`, `GameDetailOut` (reuses
  `MoveOut` from `schemas/upload.py`, which now has `from_attributes`
  enabled for ORM validation).
- `app/repositories/games.py` - added `list_all_games` (unpaginated, for
  stats aggregation).
- `app/services/library_stats.py` - `compute_upload_stats()`: adapts
  persisted `Game` rows back into `ParsedGame` shape so the existing,
  already-tested `statistics.compute_stats` can be reused unchanged
  (no duplicated aggregation logic, no persisted aggregates).
- `app/api/routes/uploads.py` - added `GET /uploads` (paginated),
  `GET /uploads/{id}`, `GET /uploads/{id}/stats`,
  `GET /uploads/{id}/games` (paginated summaries), `DELETE /uploads/{id}`.
  Renamed the import-endpoint function `create_upload` -> `import_upload`
  to avoid ambiguity with the repository's `create_upload`; route
  functions now call `upload_repo.*`/`game_repo.*` (module-qualified
  imports) to avoid name collisions with same-named route functions.
- `app/api/routes/games.py` (new file) - `GET /games/{id}` (full game +
  ordered moves), wired into `main.py`.
- `backend/tests/test_library_routes.py` - 10 tests: pagination (both
  uploads and games lists), metadata fetch, stats correctness, full game
  detail with move ordering, cascade delete (verified both via direct DB
  query and via 404 read-back), and 404s for missing upload/game ids.

**Not started yet:**
- The entire frontend half (routes, history page, saved dashboard, lazy
  game loading) - this is all of Sprint 2's remaining work.

**Next concrete step when resuming:** frontend - add react-router-dom
routing (`/` and `/uploads/:uploadId`), a "Save analysis" action after
the existing preview/name-picker flow, a recent-uploads history list on
`/`, a saved-dashboard page that fetches metadata+stats+paginated game
summaries, and lazy-load a single game's moves via `GET /api/games/{id}`
only when it's selected (feed into the existing `ChessGameViewer`
unchanged). See "Frontend steps" section below (steps 12-19).

## Resuming a stopped session

Postgres is a Docker container (`docker compose down` stops it but keeps
the `chess_postgres_data` volume - the schema/migration state survives).
To resume:

```powershell
docker compose up -d      # from the repo root
```

Then just tell Claude to continue Sprint 2 - this file's "Progress so
far" section above has everything needed to pick the thread back up
without re-explaining.

## Where v1 left off

Stateless upload -> preview flow, fully working and committed:

- `POST /api/uploads/preview` parses a PGN (multipart `file` + optional
  `player_name`), classifies every game (color/result) relative to
  `player_name`, and returns full stats + every game's full move list
  (SAN/UCI/FEN before+after) in one response. Nothing persists - a page
  refresh loses everything.
- Frontend: upload -> name picker (auto-suggests most frequent name) ->
  dashboard (stat cards + opening table) -> game list -> board viewer
  (react-chessboard, Start/Prev/Next/End, click-to-jump move list, flip
  board, orientation defaults to the user's color).
- 25 backend tests (parser, statistics, route), frontend `npm run
  build`/`npm run lint` both clean.
- No DB, no Docker for the app itself, no Stockfish, no opening-name
  dataset beyond the PGN's own ECO/Opening headers.

## Sprint 2 goal

Save imported PGNs in PostgreSQL so a user can close the app, come back
later, browse previous uploads in an "Analysis History" list, and reopen
any saved upload or game without re-uploading the file. Only PostgreSQL
persistence this sprint - no opening dataset, no Stockfish, no auth, no
background jobs, no deployment. Persistence should be solid before engine
analysis starts generating much more stored data.

### Target end-to-end flow

```
Upload PGN -> preview names -> select player name -> permanently import
    -> upload appears in "Analysis History" -> reopen later
    -> select a game -> only that game's moves are fetched (not the whole upload)
```

The "only that game's moves are fetched" part matters: v1 can return every
move from every game because it's a one-shot preview. Once uploads can
hold hundreds/thousands of games, the frontend must not pull all move
data up front.

## Database schema

**uploads**
```
id                      UUID primary key
original_filename       text
file_sha256             text
player_name             text
normalized_player_name  text
uploaded_at             timestamptz
game_count              integer
warnings                JSONB
```

**games**
```
id                UUID primary key
upload_id         UUID FK -> uploads, cascade delete
game_index        integer
event             text nullable
site              text nullable
round             text nullable
played_date_text  text nullable   -- keep as text: PGN dates can be "2026.07.??"
white_name        text
black_name        text
white_elo         integer nullable
black_elo         integer nullable
result            text
user_color        text
user_result       text
eco               text nullable
opening_name      text nullable
move_count        integer
raw_pgn           text
game_hash         text
```

**moves**
```
id          bigint primary key
game_id     UUID FK -> games, cascade delete
ply         integer
san         text
uci         text
fen_before  text
fen_after   text
```

**Constraints/indexes**
- `UNIQUE (game_id, ply)` on moves
- index `games(upload_id)`, index `moves(game_id)`
- `UNIQUE (file_sha256, normalized_player_name)` on uploads - same file
  can be imported again under a different player's perspective, but not
  re-imported for the same file+player combo. Duplicate hit -> `409`
  with the existing upload's id.

## Backend steps

1. **PostgreSQL locally via Docker only** (FastAPI/React keep running
   normally, not containerized yet). Add `compose.yaml` service, plus
   `backend/.env` + `backend/.env.example` for `DATABASE_URL`.
2. Install: `sqlalchemy`, `alembic`, `psycopg[binary]` into the existing
   venv; refresh `requirements.txt` (watch for the UTF-16 `pip freeze`
   redirect issue we hit in Sprint 1 - use `Set-Content -Encoding ascii`).
3. `app/core/database.py` - SQLAlchemy engine + a request-scoped session
   dependency. Synchronous SQLAlchemy (no async - not worth the
   complexity yet). One session per request, commit/rollback at the
   request boundary (standard web-app pattern), so a whole upload saves
   atomically.
4. `app/models/{upload,game,move}.py` - SQLAlchemy 2.x typed declarative
   mappings for the three tables above.
5. `app/repositories/{uploads,games}.py` - a handful of focused DB
   functions, not a generic repository framework.
6. `app/services/import_service.py` - hash the uploaded bytes, check for
   an existing `(file_sha256, normalized_player_name)`, parse with the
   *existing* `pgn_parser`/`statistics` services (don't rewrite them),
   classify, and persist upload+games+moves in one transaction. Any
   failure rolls back the whole thing - never a half-imported upload.
7. Alembic: `alembic init alembic`, then
   `alembic revision --autogenerate -m "Create uploads games and moves"`,
   inspect the generated migration by hand before applying,
   `alembic upgrade head`. Add `alembic check` to the normal verification
   routine. Never call `Base.metadata.create_all()` on startup.
8. Keep `POST /api/uploads/preview` exactly as-is (stateless, no DB
   writes) - it's how the name-picker step keeps working.
9. Add `POST /api/uploads` (file + confirmed `player_name`): parse, hash,
   duplicate-check (409 + existing upload id if hit), persist
   transactionally, return the new upload.
10. Add retrieval endpoints:
    - `GET /api/uploads` - paginated history (`id`, `filename`,
      `player_name`, `uploaded_at`, `game_count` - compact, no moves)
    - `GET /api/uploads/{upload_id}` - metadata
    - `GET /api/uploads/{upload_id}/stats` - reuses the existing,
      already-tested `statistics` service against DB-loaded games (do
      **not** persist aggregate stats - they're always derived, so they
      can't drift out of sync with the underlying games)
    - `GET /api/uploads/{upload_id}/games?page=&page_size=` - paginated
      game *summaries only*, no FEN/move data
    - `GET /api/games/{game_id}` - one full game + ordered moves (what
      the board viewer fetches on selection)
    - `DELETE /api/uploads/{upload_id}` - cascades to games+moves
11. Proper 404/409/422/500s; never leak DB internals in error responses.
    Keep transaction/session management out of low-level model/repo
    functions (push it to the service/route layer).

## Frontend steps

12. Add routing (react-router-dom is already installed, unused so far):
    `/` (upload form + recent analyses) and `/uploads/:uploadId` (saved
    dashboard).
13. Keep the existing preview + name-picker flow; retain the browser
    `File` object after preview so it can be re-submitted to
    `POST /api/uploads` once the user confirms their name (parsing twice
    is fine at this scale - avoids staging tables).
14. Add "Save analysis" action next to the existing preview -> on
    success, `navigate('/uploads/' + uploadId)`.
15. Home page: recent-analyses list - filename, player name, upload
    date, game count, Open button, Delete button (with confirmation
    state).
16. Saved dashboard route: fetch upload metadata + stats + first page of
    game summaries. Do **not** fetch every game's moves up front.
17. On game selection: call `GET /api/games/{gameId}`, show a loading
    state in the board area while it resolves, then feed it into the
    existing `ChessGameViewer` unchanged (keep move nav, flip board,
    click-to-jump, color-based orientation - all already built).
18. Add loading / empty / not-found / duplicate-upload / db-error /
    delete-confirmation states.
19. Keep response types in the existing `types/` modules; no new
    component framework.

## Testing

Keep all 25 existing tests green (parser + statistics stay pure unit
tests, no DB needed for those). Add **integration tests against real
PostgreSQL** (not SQLite) for:

- saving one upload + its games + its moves
- reopening a saved upload
- retrieving a game with moves in correct ply order
- multiple uploads
- pagination
- duplicate-upload detection
- the same file imported under a different player name (should succeed)
- transaction rollback on a simulated persistence failure
- cascading deletion
- missing upload/game id -> 404
- `alembic check` / migrating from an empty database

Also run: backend pytest, frontend `npm run build` + `npm run lint`,
`alembic check`.

## Sprint acceptance criteria

- [ ] PostgreSQL starts via Docker Compose
- [ ] Alembic builds the full schema from an empty DB
- [ ] Preview never writes to the DB
- [ ] Confirmed import persists upload + games + moves atomically
- [ ] Restarting React/FastAPI/Postgres doesn't lose data
- [ ] Home page shows previous uploads
- [ ] A previous dashboard reopens without the original file
- [ ] Game lists are paginated
- [ ] Full moves fetched only when a game is selected
- [ ] Duplicate uploads handled deliberately (409 + existing id)
- [ ] Deleting an upload cascades to its games/moves
- [ ] DB failures can't create partial imports
- [ ] All v1 behavior/tests still pass
- [ ] README documents DB/migration setup accurately

## Explicitly out of scope for Sprint 2

Lichess opening dataset, Stockfish, evaluation graphs, LLM explanations,
authentication, cross-upload combined statistics, Celery/Redis/background
jobs, cloud deployment, model training. Only minor refactors needed to
support persistence are in-bounds.

## Suggested commit sequence

```
chore: add PostgreSQL development environment
feat: add SQLAlchemy database configuration
feat: define upload game and move models
feat: add initial Alembic migration
feat: persist parsed PGN uploads transactionally
feat: add upload history and game retrieval endpoints
feat: add saved analysis frontend routes
feat: lazy load selected game moves
test: add PostgreSQL persistence integration tests
docs: document database and migration setup
```

## Starting point tomorrow

Work incrementally, same cadence as Sprint 1: inspect the repo first,
propose the model/migration shape, build backend up to a testable point
before touching the frontend, verify (pytest + `alembic check`) at each
step, then wire up the frontend routes/lazy-loading last.
