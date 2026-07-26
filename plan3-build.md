# Plan 3 (build): Staged execution plan for Sprint 3

`plan3.md` is the **spec** - what Sprint 3 must do and how it will be
accepted. This file is the **execution plan** - the order we build it in,
and what "done" means at each checkpoint.

Same cadence as Sprints 1 and 2: one stage at a time, verified before
moving on. Nothing in a later stage starts until the previous stage's
checkpoint is green.

---

## Stage 0 - Prerequisites (blocked on you)

Two things I can't do from here.

### 0a. Vendor the dataset (requires your shell)

My sandbox cannot reach GitHub - `curl`, `python urllib`, and `git clone`
all fail TLS validation with `unable to get local issuer certificate`.
Something on this machine intercepts HTTPS and its root isn't in the trust
stores those tools use. Your own shell is fine (your push succeeded), so
please run:

```powershell
cd C:\Users\Neil Samant\Documents\codingstuff\chess-insights
git clone --depth 1 https://github.com/lichess-org/chess-openings ..\chess-openings-snapshot
cd ..\chess-openings-snapshot
git rev-parse HEAD    # <- paste this commit SHA to me, it goes in the manifest
```

Leave the clone **outside** the repo. Stage 1 copies only the five source
TSVs in and records that SHA. I'll take it from there.

I deliberately am not working around the TLS failure by disabling
certificate verification - that's your machine's security posture, not
mine to switch off.

### 0b. plan3.md is still untracked

`git status` still shows `?? plan3.md`. Your closeout notes and the
recorded hash aren't in git yet:

```powershell
git add plan3.md
git commit -m "docs: add Sprint 3 plan and close out Sprint 2 acceptance"
git push
```

---

## Key decisions (flagging these before I build on them)

**1. Vendor the source TSVs and derive EPD ourselves - not `dist/`.**

Upstream `dist/` ships a ready-made `epd` column, which looks like less
work. I'm choosing against it. If *we* generate every dataset EPD with the
exact same function the classifier later uses on game positions, then
"EPD normalization exists in one location" (`plan3.md:141`) is true *by
construction* rather than by hope. Using upstream's EPDs means trusting
that their normalization matches python-chess's byte-for-byte, and a
silent mismatch there fails as "opening not found" - the hardest possible
bug to notice, because it looks like a legitimate miss.

Cost of my approach: one generation script. Worth it.

**2. The en-passant trap is real and is the main technical risk.**

Our stored `fen_before`/`fen_after` come from `board.fen()`, which always
emits an en-passant square after any pawn double-move. Lichess EPDs use
*legal* en-passant - the square appears only if a capture is actually
available. So **string-truncating a stored FEN into an EPD is wrong** and
will silently fail to match on exactly those positions.

The canonical function must reconstruct a `Board` from the FEN and call
`.epd()` (python-chess defaults to `en_passant="legal"`). Stage 2 gets a
dedicated test for this, using a real double-pawn-push position with and
without an available capture.

**3. Derived index is committed, not built at startup.**

Generation is a deliberate, re-runnable script; the app only ever reads
the committed artifact. Keeps startup fast and makes dataset changes show
up in code review as a diff.

---

## Stage 1 - Dataset: vendor, generate, load

**Goal:** a validated in-memory EPD lookup, with provenance. No app
integration yet.

- `backend/app/data/openings/{a,b,c,d,e}.tsv` - copied verbatim from your
  clone (eco, name, pgn)
- `backend/app/data/openings/MANIFEST.json` - upstream commit SHA, source
  URL, retrieval date, row count, license (CC0), generator version
- `backend/scripts/build_opening_index.py` - replays each row's PGN,
  emits `openings_index.tsv` (eco, name, pgn, uci, epd, ply)
- `backend/app/data/openings/openings_index.tsv` - the committed artifact
- `backend/app/services/opening_dataset.py` - `load_opening_index()`,
  cached once per process, validates required columns, fails loudly on
  missing/malformed data

**Checkpoint:** `pytest tests/test_opening_dataset.py` green - loads a
valid snapshot, rejects a missing column, rejects malformed EPD, handles
duplicate records deterministically, refuses conflicting metadata for the
same EPD, and loads exactly once when called twice.

**Done when:** row count in the manifest matches the loaded index, and
regenerating the artifact produces a byte-identical file.

---

## Stage 2 - Classifier (pure, no I/O)

**Goal:** the core algorithm, fully unit-tested in isolation.

- `backend/app/services/opening_classifier.py`
  - `position_epd(fen: str) -> str` - **the single canonical conversion**
  - `classify_opening(positions: list[str], headers) -> OpeningClassification`
  - walks positions **backward**, returns first (deepest) match
  - precedence: lichess match -> pgn_header -> unknown
- `OpeningClassification(eco, name, matched_ply, source, dataset_version)`,
  frozen

**Checkpoint:** the twelve classifier cases in `plan3.md:395-406`, plus
explicitly:
- castling-rights normalization
- **legal-vs-FEN en passant** (decision 2 above)
- deepest match wins over parent opening
- a wrong PGN header does not override a position match
- zero-move game, truncated malformed game
- classifier does not mutate its inputs

**Done when:** all pass, and a wrong-header test proves override works.

---

## Stage 3 - Preview integration (stateless path first)

**Goal:** classification visible end-to-end without touching the database.

- `pgn_parser.ParsedGame` carries the classification
- `POST /api/uploads/preview` returns canonical eco/name/source
- classifier called once per game, dataset loaded once per process

**Checkpoint:** preview returns canonical openings; **automated test that
preview still writes zero rows** (this also closes the Sprint 2 backlog
gap); no network access during classification.

---

## Stage 4 - Persistence

**Goal:** saved imports store the canonical classification.

- Alembic migration adding `opening_source`, `opening_matched_ply`,
  `opening_dataset_version` to `games` (reusing existing `eco`,
  `opening_name`)
- `import_service` classifies **server-side**; frontend-supplied values
  are never trusted
- still one all-or-nothing transaction

**Checkpoint:** `alembic upgrade head` from empty + `alembic check` clean;
preview and save produce **identical** classifications for the same file
and player; rollback-on-failure still passes; duplicate/409 unchanged.

---

## Stage 5 - Backfill

**Goal:** your existing 76 saved games get classified without re-upload.

- `backend/app/scripts/reclassify_openings.py`
- reads stored move FENs, transactional per upload, idempotent
- reports updated / unchanged / fallback / unknown counts

**Checkpoint:** runs against your real dev data; running it twice changes
nothing the second time; moves, results, players, and upload metadata are
provably untouched; a failure mid-upload leaves that upload unchanged.

---

## Stage 6 - API + frontend display

**Goal:** the user actually sees it.

- game summaries expose eco/name/source/matched_ply (still **no** move
  arrays - lazy loading unchanged)
- one shared opening-display component used by both preview and saved
  views
- `Sicilian Defense: Najdorf Variation · B90`, unknown -> `Unknown opening`
- never render `null`/`undefined`/dangling separators

**Checkpoint:** `npm run build` + `npm run lint` clean; opening-stats
table uses canonical names.

---

## Stage 7 - Frontend test runner

**Goal:** close the last Sprint 2 backlog gap, now that the UI depends on
structured domain data.

- Vitest + React Testing Library + setup file + `npm test`
- the five tests in `plan3.md:372-376`

**Checkpoint:** `npm test` green.

---

## Stage 8 - Full acceptance

Run the strict review prompt at `plan3.md:584-687` and the manual fixture
cases (`plan3.md:432-466`), including the 8-game fixture PGN and a real
Docker restart.

**Checkpoint:** backend suite green against **both** a populated and a
freshly migrated empty database, `alembic check`, frontend test/build/lint,
backfill idempotent, restart persistence intact.

---

## Risk notes

- **En passant / castling normalization** - highest technical risk. Stage
  2 is where it gets caught; do not let it slide to Stage 8.
- **Dataset licensing** - CC0, so vendoring is fine, but attribution goes
  in the README (`plan3.md:97`).
- **Scope creep** - `plan3.md:492-511` lists the non-goals. No Stockfish,
  no evaluations, no move-quality labels, no family-level rollups.
- **Existing data** - your two real uploads (76 games) are the backfill
  test bed. Back up the volume before Stage 5 if you care about them:
  `docker compose exec db pg_dump -U chess_user chess_insights > backup.sql`
