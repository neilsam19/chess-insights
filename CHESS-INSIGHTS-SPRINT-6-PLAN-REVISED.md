# Chess Insights — Sprint 6 Plan (revised)

**Source:** `CHESS-INSIGHTS-SPRINT-6-PLAN.md`
**Sprint title:** Move Quality and Grounded Training Insights
**Revision date:** 2026-08-01
**Nothing in this file has been implemented.** It is a plan.

The original plan is sound. Its scope decision — build the grounding and
recommendation layer, do not call a model — is right, and its six architectural
decisions should all stand. This revision does three things:

1. Records what the codebase and the live database actually say about the
   data semantics the original plan told the implementer to go and verify
   (its §2 and CP0). That inspection is done; the answers are below, and two
   of them change the plan.
2. Reports findings where the plan's assumptions do not match reality.
3. Re-cuts the checkpoints so no single one is larger than a sitting.

---

## Part 1 — Data semantics, verified

This is the CP0 inspection, already carried out against the code and against
the 3,236 real evaluations in the database. It is written here so the ADR in
CP0 becomes a transcription job rather than an investigation.

### Indexing

From [analysis_executor.py:75](backend/app/services/analysis_executor.py#L75):

```python
positions = [moves[0].fen_before] + [move.fen_after for move in moves]
```

| Thing | Convention |
|---|---|
| `Move.ply` | 1..N. Odd = White's move. |
| `PositionEvaluation.ply` | 0..N. **The position *after* j plies.** Ply 0 is the start. |
| Move at ply `k` | transitions position `k-1` → position `k` |
| `score_before` for move `k` | evaluation at ply `k-1` |
| `score_after` for move `k` | evaluation at ply `k` |
| Best move applicable to move `k` | `evaluation[k-1].best_move_uci` — the best move *from* the position the player faced |
| The player's moves | `user_color == "white"` → odd `k`; `"black"` → even `k` |

Verified in the database: all 40 complete runs have exactly `move_count + 1`
evaluations, plies 0..N, no gaps, and exactly one complete run per game.

### Score storage

From [position_evaluation.py](backend/app/models/position_evaluation.py) and
[engine.py:8-17](backend/app/services/engine.py#L8-L17): storage is canonical
White-perspective. Exactly one of `score_cp_white` / `mate_in_white` is
populated, enforced by a check constraint. Perspective conversion happens in
[analysis_view.py:65](backend/app/services/analysis_view.py#L65) by multiplying
by `-1` for a Black player.

### Live data shape

| Measure | Value |
|---|---|
| Evaluations | 3,236 |
| Mate evaluations | 32 |
| `best_move_uci IS NULL` | 4 — **all four at a final ply**, none elsewhere |
| Games with a complete run | 40 of 79 |
| `user_color` distribution | 42 white, 37 black, 0 unknown |
| Games with a null `opening_name` | 0 |
| Distinct openings among the 40 analysed games | ~30; largest bucket 5 games |

---

## Part 2 — Findings

### F1 — `mate_in_white = 0` is sign-ambiguous, and the plan's mate rules misread it *(blocking)*

python-chess reports `Mate(0)` — checkmate on the board — identically whichever
side was mated. The database confirms it: four rows with `mate_in_white = 0`,
all at the final ply of their game, all with `best_move_uci` null.

The plan's `missed_mate` rule is "the player had a forced mate before moving and
no longer has one afterward". A player who *delivers* mate has a before-score of
`mate +N` and an after-score of `mate 0`. Zero is neither positive nor negative,
so a naive sign check classifies **the winning checkmate as a missed mate**. The
existing perspective flip cannot save it: `-1 × 0 == 0`, so a Black player's
mating move is equally ambiguous.

**Ply parity resolves it exactly.** `mate 0` at evaluation ply `j` means the
side that just moved at ply `j` delivered mate. Checked against all four rows:

| eval ply | parity | game result | reading |
|---:|---|---|---|
| 7 | odd | 1-0 | White moved, White mated |
| 4 | even | 0-1 | Black moved, Black mated |
| 89 | odd | 1-0 | White moved, White mated |
| 85 | odd | 1-0 | White moved, White mated |

Rule to adopt: normalize every stored score into a comparable value
`(kind, sign, distance)` before any classification, where `mate 0` at ply `j`
takes `sign = +1` if `j` is odd else `-1`, and `distance = 0`. Never reconstruct
a signed mate by giving `mate 0` a distance of 1 — that would collide with a
genuine mate-in-1.

Note that [evaluation.ts:55-63](frontend/src/utils/evaluation.ts#L55-L63)
already handles this ambiguity for the graph by carrying the previous
direction forward. That is a *display* heuristic. It must not be reused for
grading, where parity gives the exact answer.

### F2 — The backend derivation must read model rows, not `PositionEvaluationOut.score` *(blocking)*

The plan's §2 says "use the already exposed player-relative score rather than
reimplementing color perspective in the frontend". Correct for the frontend.
Wrong for the backend derivation, because of F1: the exposed player-relative
score for a delivered checkmate is `{type: "mate", value: 0}` from both
perspectives, so it cannot express "I mated" versus "I was mated".

The derivation layer takes `score_cp_white`, `mate_in_white`, the evaluation
ply, and the player's colour, and produces the normalized triple itself. The
wire format in `analysis_view` stays exactly as it is — nothing about the
existing analysis response changes.

### F3 — Within-game evaluation gaps are structurally impossible *(scope reduction)*

[analysis_executor.py:105-137](backend/app/services/analysis_executor.py#L105-L137)
accumulates every evaluation in memory and persists only after the whole game
succeeds. A failed or abandoned attempt stores zero rows — that is Sprint 5
invariant 5, and it is why the comment on that function says it is "what keeps
Sprint 6's inputs trustworthy". Confirmed against all 40 complete runs: zero
gaps, zero short runs.

So the plan's "partial/gapped evaluation coverage" test (CP2) and its
"show partial scored-move coverage within the game" UI requirement (§5) defend
against a state the architecture prevents.

**Recommendation:** keep the `unscored` label and its machine-readable reason
codes — they are needed anyway for the missing-`best_move_uci` case, they cost
almost nothing at the pure layer, and they are the honest response if the
invariant is ever broken. Test them with synthetic inputs in CP1. But do **not**
build within-game partial-coverage UI. The partial coverage that is real is
across the upload — 40 of 79 — and that is where the effort belongs.

### F4 — `user_color` can be `"unknown"`, and grading it would silently lie *(correctness)*

[analysis_view.py:54-62](backend/app/services/analysis_view.py#L54-L62) documents
it: `user_color` is `"unknown"` when the confirmed player name matched neither
side, which is reachable for any upload saved under a name absent from the file.
The existing fallback reports the canonical White view, which is the right call
for a score display.

It is the wrong call for move quality. Grading such a game would present
*White's* moves as "your moves". Current data has no unknown rows, so this
is cheap to get right now and expensive to discover later.

**Rule:** `user_color == "unknown"` → the move-quality endpoint returns a
distinct non-gradable state; the game is excluded from upload aggregates and
counted in its own coverage bucket. One test.

### F5 — The mate transition matrix has nine cells; the plan enumerates four *(specification gap)*

The plan names `missed_mate`, `allowed_mate`, `mate_sequence`, `unscored`. After
normalizing per F1, before and after each fall into one of three kinds —
centipawn, mate-for-the-player, mate-against-the-player — giving nine
transitions. The full table, which the ADR should adopt verbatim:

| before ↓ / after → | cp | mate **for** player | mate **against** player |
|---|---|---|---|
| **cp** | normal CPL | `good`, cpl null *(found a forced mate)* | `allowed_mate`, cpl null |
| **mate for player** | `missed_mate`, cpl null | `unscored` / `mate_sequence`, cpl null | `allowed_mate`, cpl null |
| **mate against player** | `good`, cpl null *(escaped)* | `good`, cpl null | `unscored` / `mate_sequence`, cpl null |

Three cells the plan does not cover are ordinary and will occur: finding a mate,
escaping a mate, and being already lost and staying lost. None of them is a
mistake, and none of them may be assigned a centipawn value. The diagonal
`mate_sequence` cells are `unscored` — not gradable, not a mistake, excluded
from ACPL, excluded from critical counts.

### F6 — The opening recommendation will usually be omitted, and that must be the designed-for case *(product)*

Among the 40 analysed games there are roughly 30 distinct canonical openings.
The largest bucket is 5 games (Sicilian Defense: French Variation); only about
five openings reach two games at all. Sample size does exist in the Sicilian
family — but §8 explicitly forbids opening-family rollups, which is where that
sample lives.

This is not a defect in the plan's evidence floor; the floor is right. It means
the by-opening section should be **built empty-first**: show the table with
sample counts whatever the coverage, and emit a *recommendation* only above the
floor. An omitted opening recommendation is the expected outcome for this
library, not a bug to chase.

Suggested floor: at least 2 completed games **and** at least 20 scored player
moves in that opening.

### F7 — Move quality needs its own endpoint, because the analysis endpoint is polled *(design)*

[GameAnalysisPanel.tsx:79-98](frontend/src/components/GameAnalysisPanel.tsx#L79-L98)
polls `GET /api/games/{id}/analysis` every 1.5 s while a job is active. Adding a
`move_quality` block to `CompleteAnalysisOut` would run the derivation on every
poll of every watched game, for data that cannot change while the job is running.

Use a separate read endpoint with its own tagged union, as the plan's §3
suggests. It also keeps the two responses independently cacheable and lets the
non-complete states carry a different shape without disturbing an API that four
Sprint 5 test files depend on.

### F8 — Deep links need one contained change, not a routing redesign *(scoping)*

[App.tsx](frontend/src/App.tsx) has exactly two routes and
`react-router-dom` 7.18 is already a dependency.
[SavedDashboardPage.tsx:26-27](frontend/src/pages/SavedDashboardPage.tsx#L26-L27)
selects a game by `game_index` held in component state, and pages games 25 at a
time — so a deep link to a game on page 3 would not find its summary in
`gameSummaries` and `handleSelectGame` would do nothing.

Minimal fix, no new routes: add `?game=<uuid>&ply=<n>` search params; on load,
if `game` is present, call the existing `getGame(id)` directly rather than
looking the summary up in the current page. `ChessGameViewer` gains an optional
`initialPly`. That is the whole change.

### F9 — Two checkpoints are larger than any Sprint 5 checkpoint *(sizing)*

The plan's CP3 bundles summary cards, move-list annotations, four filters,
previous/next navigation, jump-to-ply, mate labels, partial coverage, and
loading/error states, with seven required frontend test areas. Its CP6 bundles
six dashboard sections plus deep links. Both are two sittings. Part 3 splits
them.

### Smaller notes

- `MoveList` is shared with the *preview* (unsaved) game path, which has no
  game id and no analysis. Annotations must be an optional prop, and the
  existing preview tests must keep passing untouched.
- `EngineMetadataOut` already exposes name, version, `binary_sha256`, depth and
  options — and **not** the engine path. CP10's "no filesystem path in the
  evidence payload" test is asserting something the current shape already
  satisfies; keep the test, it is guarding a property, not fixing a bug.
- The existing upload aggregation
  ([library_stats.py](backend/app/services/library_stats.py)) loads rows and
  aggregates in Python rather than in SQL. Move-quality aggregation should
  follow that pattern: three bounded queries per upload — games with their
  complete run, moves for those games, evaluations for those runs — then
  aggregate in Python. For this library that is ~3,200 rows. "No N+1" is
  satisfied by query *count*, not by pushing arithmetic into SQL.
- The 4 null `best_move_uci` rows are all at final plies, and a final position
  is never a *before* position for any move. So `is_exact_best_move` is null in
  practice zero times out of 3,196 gradable positions. Test the null path;
  do not build UI for it.

---

## Part 3 — Checkpoints

Twelve, matching the Sprint 5 cadence. Each is independently verifiable and
committable, and each ends with its focused tests plus the full affected suite.

Sizing is relative to Sprint 5 checkpoints, which is the only calibration that
has actually been observed.

| CP | Title | Size | Layer |
|---|---|---|---|
| 0 | Baseline and ADR 0002 | S | docs |
| 1 | Pure per-move derivation | L | backend |
| 2 | Pure game summary | S | backend |
| 3 | Game move-quality endpoint | M | backend |
| 4 | Viewer: summary and annotations | M | frontend |
| 5 | Viewer: filters and issue navigation | M | frontend |
| 6 | Upload aggregation backend | L | backend |
| 7 | Deterministic recommendations | L | backend |
| 8 | Dashboard and deep links | L | frontend |
| 9 | Evidence contract | S | backend |
| 10 | Regression hardening | M | tests |
| 11 | Documentation and acceptance | S | docs |

Logical stopping points: after CP3 (backend facts complete and callable), after
CP5 (the game viewer is a finished feature on its own), after CP7 (all backend
work done), after CP8 (the product is feature-complete).

---

### CP0 — Baseline and ADR 0002

**Build.** Run the full baseline first:

```powershell
docker compose up -d
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check
cd ..\frontend
npm test; npm run lint; npm run build
```

Then write `docs/adr/0002-move-quality-derivation.md` recording Decisions 1–6
from the original plan, plus, from Part 1 and Part 2 of this file: the indexing
table, the mate-0 parity rule (F1), the nine-cell transition matrix (F5), the
unknown-colour rule (F4), the threshold constants, and the separate-endpoint
decision (F7). Record `MOVE_QUALITY_VERSION = 1` as the formula/threshold
version that the evidence contract will carry.

**Acceptance.** Baseline matches PROJECT-STATUS (376 / 370+6 / 54, alembic
clean, lint and build exit 0). No production file changed.

**Commit.** `docs: define sprint 6 move-quality semantics`

---

### CP1 — Pure per-move derivation

No database, no engine, no I/O. New module `app/services/move_quality.py`
holding the constants and pure functions.

**Build.**
- `MoveQualityThresholds`: 0–49 `good`, 50–99 `inaccuracy`, 100–199 `mistake`,
  200+ `blunder`. Named module constants, never request parameters.
- `normalize_score(score_cp_white, mate_in_white, ply, *, player_is_white)`
  → the `(kind, sign, distance)` triple, applying the F1 parity rule.
- `classify_move(...)` → per-move facts: `centipawn_loss`, `quality`,
  `special_classification`, `is_exact_best_move`, `unscored_reason`.
- Reason-code constants for every unscored path.

**Tests** — `backend/tests/test_move_quality.py`:
- White player perspective; Black player perspective.
- Zero loss; evaluation improvement clamped to zero.
- Threshold boundaries 49/50, 99/100, 199/200 — both sides of each.
- Exact best-move match; non-match with low CPL stays `good`.
- Promotion suffix retained in the UCI comparison (`e7e8q` ≠ `e7e8`).
- Missing `best_move_uci` → `is_exact_best_move is None`, not `False`.
- Missing before score; missing after score; non-contiguous pair — each with a
  distinct reason code.
- **All nine cells of the F5 matrix.**
- **Mate 0 delivered by White at an odd ply → not `missed_mate`.**
- **Mate 0 delivered by Black at an even ply → not `missed_mate`.**
- No output path ever produces a centipawn number from a mate score.

**Acceptance.** `test_move_quality.py` passes with `STOCKFISH_PATH=""`. No
import of `Session`, `chess.engine`, or any repository in the new module.

**Commit.** `feat: derive player move quality from stored evaluations`

---

### CP2 — Pure game summary

Still no I/O. Aggregation over the CP1 per-move facts.

**Build.** `summarize_moves(facts)` → total player moves, scored, unscored,
coverage percent, ACPL, median CPL, exact-best-move count and rate,
inaccuracies, mistakes, blunders, missed mates, allowed mates, critical
mistakes.

**Tests:**
- ACPL and median use only moves with a numeric CPL; the numeric sample size is
  reported separately from the move count.
- Mate events are **excluded from ACPL** and **included in critical mistakes**.
- `mate_sequence` counts as neither.
- Zero scored moves → nulls, not zeros. *(A game where every move is unscored
  must not report "ACPL 0", which reads as perfect play.)*
- Median with an even and an odd sample count.
- Exact-best-move rate denominator excludes moves with a null best move.

**Acceptance.** Engine-free. Pure functions only.

**Commit.** `feat: summarize game move quality`

---

### CP3 — Game move-quality endpoint

**Build.**
- `GET /api/games/{game_id}/move-quality`, its own tagged union:
  `not_analyzed | queued | running | complete | failed | cancelled | not_gradable`.
  `not_gradable` is the F4 unknown-colour state.
- A read service that resolves the current run through the **existing**
  `analysis_repo.get_latest_complete_run_for_game`, loads moves and evaluations
  in bounded queries, and calls CP1/CP2.
- Non-complete states reuse the existing `game_analysis_state` shapes for
  `can_cancel` / `can_retry` rather than recreating action legality.

**Tests** — `backend/tests/test_move_quality_routes.py`:
- Complete game response, White player and Black player.
- Every non-complete analysis state.
- No completed run.
- Legacy nullable `analysis_job_id` (Sprint 4 rows).
- `user_color == "unknown"` → `not_gradable`.
- Opponent moves are not graded and do not appear in the counts.
- GET performs no writes — row counts for the ids the test created are
  unchanged afterwards.
- Stockfish is never invoked (no engine factory is called).
- Scope every assertion to ids the test created. No global counts.

**Acceptance.** Full backend suite green, engine-configured and engine-free.
`alembic check` still clean — **no migration was added.**

**Manual check to hand back:** open a completed game, hit the new endpoint in
the browser, confirm the counts look plausible against the evaluation graph.

**Commit.** `feat: expose game move-quality analysis`

---

### CP4 — Viewer: summary and annotations

Display only. No filtering, no new navigation.

**Build.**
- `frontend/src/types/moveQuality.ts` mirroring the union.
- `frontend/src/api/moveQuality.ts`.
- A summary card: ACPL, exact-best-move rate, inaccuracy / mistake / blunder /
  critical counts, with a short explanation of what ACPL and "exact best move"
  mean.
- `MoveList` gains an **optional** annotation prop; annotations use text or a
  glyph plus an accessible label, never colour alone.
- Mate events render with their own label, never a number.

**Tests:**
- The player's moves are annotated; the opponent's are not.
- Exact-best-match is shown as a badge and is **not** the quality label.
- A non-best move with low CPL still reads as acceptable.
- Mate labels render distinctly from centipawn labels.
- Incomplete analysis shows no zero-valued metrics.
- The preview (unsaved) game path renders `MoveList` unchanged.

**Acceptance.** `npm test`, `npm run lint`, `npm run build` all exit 0.
Existing board and graph behaviour unchanged.

**Commit.** `feat: show move quality in the game viewer`

---

### CP5 — Viewer: filters and issue navigation

**Build.** Filters (all player moves / inaccuracies or worse / mistakes or
worse / critical), previous-issue and next-issue buttons, click-an-annotation
jumps the board to that ply.

**Tests:**
- Each filter shows exactly the expected set.
- Clicking an annotation sets `currentPly` and the board follows.
- Next/previous issue respects the active filter and stops at the ends.
- Keyboard reachable.
- The existing move navigation and evaluation graph still work.

**Acceptance.** Frontend suite green, lint and build exit 0.

**Manual check to hand back:** open an analysed game, filter to critical
mistakes, step through them, confirm the board lands on the right position each
time and the graph marker follows.

**Commit.** `feat: add move-quality review navigation`

---

### CP6 — Upload aggregation backend

**Build.** `GET /api/uploads/{upload_id}/move-quality`. Coverage by analysis
state, overall metrics, by colour, by opening, top review moves. Three bounded
queries per upload (F-notes), aggregated in Python.

**Tests** — `backend/tests/test_upload_move_quality.py`:
- Mixed complete and incomplete library — the denominator is complete games
  only, and the coverage block reports both numbers.
- Zero completed games; exactly one completed game.
- Multiple openings; a null-opening game lands in an explicit unknown bucket.
- White-only and Black-only coverage.
- An unknown-colour game is excluded and counted separately (F4).
- Mate events excluded from ACPL, included in critical counts.
- Deterministic ordering, including a deliberate tie.
- Existing upload list and pagination behaviour does not regress.
- Query count is bounded — assert it, do not assume it.

**Acceptance.** Backend suite green both ways. No migration.

**Commit.** `feat: aggregate move quality across an upload`

---

### CP7 — Deterministic recommendations

**Build.** Four rule types from the original plan: review largest mistakes,
opening priority, colour priority, tactical-review proxy. Each carries display
text plus structured `evidence_items[]` and a `coverage_note`.

**Tests:**
- Top mistakes ranked correctly, with a deterministic tie-break.
- Low-sample openings are not recommended (F6 floor: ≥2 games and ≥20 scored
  moves).
- The weakest sufficiently represented opening is the one selected.
- Colour recommendation requires sufficient coverage on both sides.
- Insufficient data → fewer or zero recommendations, never filler.
- **Every evidence id belongs to the requested upload.**
- Wording asserts no chess motif — assert against a denylist of terms
  (`fork`, `pin`, `endgame`, `positional`, …) so this cannot regress by
  someone editing a string.
- A coverage note is present on every recommendation.

**Acceptance.** Backend suite green both ways.

**Commit.** `feat: generate grounded training recommendations`

---

### CP8 — Dashboard and deep links

**Build.** A Training Insights section on the saved dashboard: coverage
displayed prominently, overall metrics, by-colour and by-opening tables with
sample sizes, recommendation cards showing their evidence, top review
positions. "Analyze All Games" stays visible while coverage is incomplete.
Deep links per F8: `?game=<uuid>&ply=<n>`, resolved by direct `getGame` fetch,
plus an `initialPly` prop on `ChessGameViewer`.

**Tests:**
- Coverage numerator and denominator are accurate and visible.
- **40 analysed games never render as 79.**
- A deep link opens the correct game at the correct ply, including a game not
  on the currently loaded page.
- Recommendations display their evidence.
- Empty (no analysis yet) and unavailable (request failed) states are visually
  and semantically distinct.

**Acceptance.** Frontend suite green, lint and build exit 0.

**Manual check to hand back:** the full dashboard against the real 40-of-79
library — confirm the coverage line is honest, click a recommendation through
to its position, and reload the page on that URL.

**Commit.** `feat: add upload-level training insights`

---

### CP9 — Evidence contract

No model call. A stable internal payload a future sprint could hand to one.

**Build.** A serializer producing, per evidence item: game metadata, played
move, best move, before/after score in the existing score union, CPL or mate
event, FEN, opening, exact source ids, engine provenance, and
`MOVE_QUALITY_VERSION`. Document it in the ADR.

**Tests:**
- No filesystem path anywhere in the payload.
- No environment values.
- Every field traces to a stored row or a CP1/CP2 derivation.
- Deterministic for the same source data — serialize twice, compare.
- Facts are structurally separated from any future generated prose.

**Commit.** `docs: define evidence contract for future explanations`

---

### CP10 — Regression hardening

**Build.** Nothing. Break things and confirm the tests notice.

Deliberately break each of these, confirm the named test goes red, revert:

| Break | Expected failure |
|---|---|
| Reverse the player perspective | Black-perspective CPL tests |
| Remove the CPL clamp | improvement-clamped-to-zero test |
| Make 200 classify as `mistake` | boundary test at 199/200 |
| Give `mate 0` a fixed sign instead of parity | **F1 White-mates and Black-mates tests** |
| Include unanalysed games in the aggregate denominator | coverage test |
| Remove the recommendation evidence floor | low-sample-opening test |
| Flatten mate to a centipawn number | mate-excluded-from-ACPL test |

Then confirm the three Sprint 5 protected tests still fail when *their*
mechanisms are broken: `SKIP LOCKED`, background lease renewal, concurrency cap.
Do not rewrite them to make Sprint 6 tests easier.

Run both suites, engine-configured and engine-free.

**Commit.** `test: harden move-quality and recommendation invariants`

---

### CP11 — Documentation and acceptance

Update PROJECT-STATUS, README, the ADR, `docs/sprint-6-acceptance.md` with an
automated and a manual checklist in the Sprint 5 format, and the frontend help
text. Explicitly retain the three deferrals: re-analysis at another depth,
deployment/containerization, generative-AI explanations.

**Commit.** `docs: complete sprint 6 acceptance record`

---

## Part 4 — Standing constraints

These carry forward from Sprint 5 and are not negotiable within this sprint.

**Security and data integrity**
- Never accept a client-supplied engine path, UCI command, engine option, FEN,
  evaluation, job state, progress value, or **threshold**.
- Never assemble a shell string from request data.
- Sanitize every stored error message: no filesystem paths, no tracebacks, no
  environment values.
- Never hold a database transaction while Stockfish runs.
- The opening dataset stays vendored; nothing is fetched at runtime.
- TLS certificate verification stays enabled.
- `docker compose down` **without** `-v`.

**Architecture**
- No migration. No derived move-quality table. `alembic check` stays clean.
- Every new endpoint is read-only: no job enqueued, no row inserted, no run
  modified, no engine started.
- Reuse the existing run/config resolution. Do not create a second definition
  of "the current analysis".
- Mate and centipawn stay distinct types end to end.
- Worker, leasing, claiming, heartbeat, recovery, and concurrency code is not
  touched unless a failing test proves Sprint 6 requires it.

**Testing**
- Scope every database assertion to ids the test created. **Never assert a
  global count.**
- `db_session` for ordinary tests; `committed_db` only for genuine
  multi-connection concurrency.
- Never share a SQLAlchemy Session across threads.
- Never call `db.rollback()` after a read-only query on a caller-supplied
  session.
- Ordinary tests stay engine-free; the suite must pass with
  `STOCKFISH_PATH=""`.
- Verify lint and test exit status directly, never through a pipe.
  In PowerShell, `$env:STOCKFISH_PATH=""` **deletes** the variable rather than
  emptying it — use the Bash tool for the engine-free run.

**Working practice**
- One checkpoint at a time. Focused tests, then the full affected suite, then
  commit.
- New mechanisms are proven capable of failing by deliberate mutation, not
  assumed correct because the test is green.

---

## Part 5 — Open questions for the start of CP0

1. **`good` versus `best` in the move list.** The plan says `best` is a badge,
   not a label. Confirm the viewer shows both simultaneously (quality label +
   optional badge) rather than letting the badge replace the label — the tests
   in CP4 assert this, so it should be a conscious choice.
2. **Should `mate_sequence` moves count against coverage?** They are ungradable
   through no fault of the position's data. Proposal: they count as `unscored`
   in coverage but are reported with their own sub-count so a lost game does not
   look like a data problem.
3. **Top review moves across an upload — how many?** The plan says up to five
   for the recommendation. The dashboard's "top review positions" list could be
   longer. Proposal: 5 in the recommendation, 10 in the list.
