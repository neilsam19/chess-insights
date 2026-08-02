# Chess Insights — Sprint 6 Plan

**Sprint title:** Move Quality and Grounded Training Insights  
**Project state entering sprint:** Sprints 1–5 complete and accepted  
**Primary goal:** Turn stored Stockfish evaluations into useful, personalized chess feedback without re-running Stockfish, changing the worker architecture, or introducing generative AI.  
**Recommended sprint outcome:** A user can see which of their moves lost evaluation, jump directly to those moves on the board, understand aggregate weaknesses across an upload, and receive deterministic training priorities backed by concrete evidence.

---

## 0. Roadmap decision

The original roadmap described Sprint 6 as:

> Grounded AI explanations and personalized training recommendations.

That direction is still valid, but the complete feature is too large for one disciplined sprint now that the required grounding layer is understood.

Grounded explanations cannot be implemented responsibly until the application can first derive and test:

- centipawn loss;
- move-quality classifications;
- mate-specific mistakes;
- exact evidence for every recommendation;
- partial-analysis behavior;
- stable API contracts for those facts.

Therefore Sprint 6 should implement the grounding and recommendation layer, but **not call an LLM**.

### Sprint 6 required scope

1. Compute move quality from existing evaluations on read.
2. Classify the selected player's moves using explicit, tested rules.
3. Show move annotations and a mistake-review workflow in the game viewer.
4. Compute upload-level move-quality aggregates with honest coverage reporting.
5. Generate deterministic, evidence-linked training recommendations.
6. Define an explanation/evidence contract that a future AI feature can consume.

### Deferred to a later sprint

- Free-form LLM explanations.
- Model-provider integration, API keys, cost limits, prompt versions, and retries.
- AI-generated training plans.
- Re-analysis at a different depth.
- Deployment and containerization.

This preserves the original roadmap's intent while preventing Sprint 6 from becoming an analytics sprint, an AI-platform sprint, and a deployment sprint at the same time.

---

## 1. Architectural decisions to make before implementation

Record these decisions in an ADR amendment or a new ADR before feature code.

### Decision 1: Compute derived move-quality values on read

**Decision:** Do not create a move-quality table and do not add a migration.

Centipawn loss, labels, best-move matches, aggregates, and recommendations are derived from:

- the completed analysis run already selected by existing analysis-resolution logic;
- its `position_evaluations`;
- the game's `moves`;
- the selected player's color;
- server-owned classification constants.

Reasons:

- Every existing aggregate is computed on read.
- The current library is small enough that derivation is inexpensive.
- Storing derived rows would require invalidation whenever evaluation selection, thresholds, or formulas change.
- A stored table would be the project's first derived-data exception.
- The source data already exists for 3,236 evaluated positions.
- GET requests must remain side-effect free.

The derivation layer should be pure enough that persistence could be added later as a cache without changing its public meaning.

### Decision 2: Do not introduce a branded “accuracy” score

**Decision:** Sprint 6 exposes transparent metrics rather than inventing or imitating a proprietary accuracy formula.

Required metrics:

- average centipawn loss, or ACPL;
- median centipawn loss;
- exact best-move match count and rate;
- inaccuracy, mistake, and blunder counts;
- scored-move count;
- unscored-move count;
- analysis coverage.

The UI must explain that exact best-move match means the played UCI move equals the single stored `best_move_uci`. A non-match is not automatically a bad move because multiple moves can be similarly strong.

A future sprint may add an explicitly named, versioned application-specific accuracy score, but it must not be called equivalent to Chess.com or Lichess accuracy without deliberately matching and validating a published definition.

### Decision 3: Use fixed, server-owned thresholds for v1

Use simple thresholds that are easy to explain and test:

| Label | Centipawn loss |
|---|---:|
| `good` | 0–49 |
| `inaccuracy` | 50–99 |
| `mistake` | 100–199 |
| `blunder` | 200+ |

Additional states:

- `best` may be shown as a badge when `moves.uci == best_move_uci`, but it does not replace the CPL-based quality label.
- `unscored` is used when the required evaluation pair is unavailable or incompatible.
- Mate transitions use separate labels and never fake a centipawn value.

Keep thresholds as named backend constants with boundary tests. They are not request parameters.

### Decision 4: Grade the selected player's moves in v1

Sprint 6 is a personal analytics feature. Its required output grades only the moves made by the player selected when the PGN was imported.

Reasons:

- `PositionEvaluationOut.score` already provides the deliberate player-relative perspective needed for this.
- Training recommendations should focus on the user's decisions.
- This avoids presenting opponent quality as personalized feedback.
- The scope remains small enough to finish and validate.

The derivation service may be designed so grading both colors can be added later, but the initial API and UI should not promise it.

### Decision 5: Derive from the same completed run the product already treats as current

Do not create a second interpretation of “current analysis.”

Move-quality endpoints must reuse the existing completed-run/config resolution used by `GET /api/games/{id}/analysis`. If multiple depths or engine configurations exist later, move quality must follow that same selected run unless the API is deliberately extended with an explicit server-approved selection mechanism.

### Decision 6: Partial coverage is a first-class state

The library currently has completed analyses for only part of its games. Sprint 6 must not assume every game is analyzed.

Rules:

- Per-game move quality is available only when that game has a completed usable run.
- Upload-level aggregates include completed usable games only.
- Every aggregate response reports numerator and denominator coverage.
- Unanalyzed, queued, running, failed, and cancelled games are counted separately.
- The UI offers the existing Analyze Game or Analyze All Games action instead of showing misleading zeroes.
- Recommendations state the evidence coverage on which they are based.

---

## 2. Move-quality semantics

Implementation must begin with a short repository inspection that confirms how an evaluation row maps to a position and ply. Do not assume the index convention from a field name.

Document and test:

- whether each evaluation describes `fen_before`, `fen_after`, or another indexed position;
- which evaluation contains the `best_move_uci` applicable to a played move;
- how the first gradable move obtains its before/after pair;
- how gaps or truncated games are represented;
- how the player's color is obtained.

The plan below assumes the existing data can provide a before/after player-relative score pair for each gradable player move, as stated in the project status. Adapt indexing to the actual repository semantics without changing the formulas.

### Centipawn positions

For the selected player's move:

```text
centipawn_loss = max(0, player_score_before - player_score_after)
```

Requirements:

- Use the already exposed player-relative score rather than reimplementing color perspective in the frontend.
- Clamp improvements to zero; a move is not assigned negative loss.
- Keep raw before and after scores in the evidence object.
- Never derive CPL by subtracting White-relative values without first applying the correct perspective.
- Compute with integers and return integers.

### Mate positions

Mate remains a distinct type throughout the derivation and API.

Do not convert mate to `±100000`, `±10000`, or any other fake centipawn value.

At minimum identify:

- `missed_mate`: the player had a forced mate before moving and no longer has one afterward;
- `allowed_mate`: the opponent did not have a forced mate before the move and does afterward;
- `mate_sequence`: both sides of the comparison remain in a mate evaluation and no safe CPL is available;
- `unscored`: an ambiguous or incomplete mate transition.

For Sprint 6:

- `missed_mate` and `allowed_mate` are presented as critical review items.
- They count with blunders in high-level “critical mistakes” totals, while retaining their specific type.
- They have `centipawn_loss = null`.
- Changes only in mate distance should not be converted into centipawn loss.

The exact sign/perspective rules must be covered for a player who is White and a player who is Black.

### Best-move comparison

For every gradable player move:

```text
is_exact_best_move = played_move_uci == best_move_uci
```

Requirements:

- Compare normalized UCI strings.
- Promotion suffixes must remain part of the comparison.
- Null or absent `best_move_uci` produces `is_exact_best_move = null`, not `false`.
- Do not classify a move as inaccurate solely because this value is false.

### Unscored moves

A move is unscored rather than guessed when:

- one side of the required evaluation pair is missing;
- score types cannot be safely compared;
- the run is incomplete or inconsistent;
- the move/evaluation mapping is not contiguous;
- the stored move or best move is unavailable where required.

Return a machine-readable reason code. The UI may group these as “not scored,” but tests should distinguish reasons.

---

## 3. Proposed backend contracts

Use the project's existing Pydantic and TypeScript tagged-union conventions. Exact names may be adapted to repository naming, but the separation of facts must remain.

### Per-move output

Suggested shape:

```text
MoveQualityOut
- ply
- move_number
- side
- san
- played_move_uci
- best_move_uci
- is_exact_best_move: bool | null
- score_before
- score_after
- centipawn_loss: int | null
- quality: good | inaccuracy | mistake | blunder | unscored
- special_classification: missed_mate | allowed_mate | mate_sequence | null
- unscored_reason: string | null
```

`score_before` and `score_after` should use the existing score union rather than flattening mate and centipawn values.

### Per-game summary

```text
GameMoveQualitySummaryOut
- game_id
- analysis_run_id
- engine provenance already safe to expose
- player_color
- total_player_moves
- scored_moves
- unscored_moves
- coverage_percent
- average_centipawn_loss
- median_centipawn_loss
- exact_best_move_count
- exact_best_move_rate
- inaccuracies
- mistakes
- blunders
- missed_mates
- allowed_mates
- critical_mistakes
- moves: MoveQualityOut[]
```

Do not average mate events into ACPL. ACPL and median CPL use only moves with numeric CPL. Report the numeric sample size.

### Upload-level summary

Add a dedicated read endpoint such as:

```text
GET /api/uploads/{upload_id}/move-quality
```

Suggested response:

```text
UploadMoveQualitySummaryOut
- upload_id
- coverage
  - total_games
  - complete_games
  - queued_games
  - running_games
  - failed_games
  - cancelled_games
  - not_analyzed_games
  - scored_player_moves
- overall
  - average_centipawn_loss
  - median_centipawn_loss
  - exact_best_move_rate
  - inaccuracies
  - mistakes
  - blunders
  - missed_mates
  - allowed_mates
  - critical_mistakes_per_game
- by_color[]
- by_opening[]
- top_review_moves[]
- recommendations[]
```

The aggregation should be implemented with bounded queries and no N+1 query per game or per move.

### Analysis-state behavior

For a game without a usable completed run, either:

1. expose a dedicated tagged union for move quality; or
2. return a clear conflict/not-ready response that includes the current analysis state.

Prefer a tagged union if it matches the repository's existing client approach:

```text
not_analyzed | queued | running | complete | failed | cancelled
```

Only `complete` carries summaries and move annotations. Reuse `can_cancel` and `can_retry` semantics where appropriate rather than recreating action legality in the client.

### No write side effects

All new endpoints are read-only:

- no jobs are enqueued automatically;
- no derived rows are inserted;
- no run is modified;
- no thresholds are stored;
- no engine process is started.

---

## 4. Deterministic recommendation model

Sprint 6 recommendations are generated from facts, not from an LLM.

Each recommendation must carry both display text and structured evidence.

Suggested contract:

```text
TrainingRecommendationOut
- type
- priority
- title
- explanation
- evidence_summary
- evidence_items[]
- coverage_note
```

Evidence items may include:

```text
- game_id
- game_index or display label
- ply
- san
- played_move_uci
- best_move_uci
- centipawn_loss
- special_classification
- opening_name
```

### Required recommendation types

Generate only recommendations supported by enough data.

#### 1. Review largest mistakes

Example logic:

- Rank the user's blunders, mistakes, missed mates, and allowed mates.
- Return up to five review positions.
- Each item deep-links to the game and ply.
- Tie-break deterministically.

#### 2. Opening priority

Example logic:

- Aggregate numeric CPL and critical mistakes by canonical opening.
- Require a minimum evidence floor, such as at least two completed games and a minimum number of scored player moves.
- Recommend the weakest sufficiently represented opening.
- Do not recommend an opening based on one move or one short game.

#### 3. Color priority

Example logic:

- Compare White and Black only when both have sufficient coverage.
- Recommend review of the weaker color only when the difference is meaningful and supported by sample counts.
- Otherwise omit the recommendation.

#### 4. Tactical-review proxy

Do not claim that Stockfish loss alone proves a tactical weakness.

Permitted wording:

- “Review these high-impact positions.”
- “Your largest evaluation losses occurred in these games.”
- “These moves are the best starting points for calculation practice.”

Prohibited wording without additional evidence:

- “You are weak at forks.”
- “You miss pins.”
- “Your endgames are poor.”
- “You need to study positional sacrifices.”

The system does not yet classify chess motifs, so recommendations must not invent them.

### Recommendation omission is valid

If evidence is insufficient, return fewer recommendations with an explicit coverage note. Never fill space with generic advice disconnected from stored data.

---

## 5. Frontend product behavior

### Game viewer

Add a move-quality review layer to the existing analysis experience.

Required behavior:

- Show a compact game summary: ACPL, best-move match rate, and mistake counts.
- Annotate the selected player's moves in the move list.
- Use accessible text/icons, not color alone.
- Clicking an annotation jumps the existing board to that ply.
- Add “previous issue” and “next issue” navigation.
- Allow filtering to:
  - all player moves;
  - inaccuracies or worse;
  - mistakes or worse;
  - critical mistakes.
- Display played move and engine best move in UCI or translated SAN when safely available.
- Keep the existing evaluation graph and board synchronization working.
- Do not hide mate information behind a fake graph value.
- Provide an explanation of ACPL and exact-best-match semantics.

A non-best move with low CPL should still appear as acceptable. The UI must not imply that exact engine matching is the only way to play well.

### Analysis-state UX

- `not_analyzed`: show Analyze Game.
- `queued` or `running`: show current progress and no fake quality values.
- `failed`: show the existing failure/retry affordance.
- `cancelled`: show existing retry affordance.
- `complete`: show move-quality analytics.
- Missing/gapped evaluations: show partial scored-move coverage within the game.

### Upload dashboard

Add a “Move Quality” or “Training Insights” section.

Required behavior:

- Show analyzed game coverage prominently.
- Show overall metrics only for completed usable games.
- Show by-color and by-opening summaries with sample sizes.
- Show top review positions with deep links to game and ply.
- Show deterministic recommendations with the evidence that caused each one.
- Keep Analyze All Games visible when coverage is incomplete.
- Never make 40 analyzed games look like 79 analyzed games.

### URL/deep-link behavior

Where practical, preserve the selected game and ply in route state or query parameters so a recommendation can open the exact review position and survive refresh.

Do not redesign routing more broadly than necessary.

---

## 6. Checkpoint plan

Keep each checkpoint independently verifiable and committable.

### CP0 — Baseline and data-semantics inspection

Before editing:

```powershell
docker compose up -d
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check
cd ..\frontend
npm test
npm run lint
npm run build
```

Then inspect and document:

- evaluation-to-ply mapping;
- best-move-to-position mapping;
- player-relative score construction;
- completed-run selection;
- game color/player identity;
- existing analysis response types;
- query patterns for evaluations and moves.

Deliverable:

- a short design note or ADR draft;
- no production behavior changed.

Commit concept:

```text
docs: define Sprint 6 move-quality semantics
```

### CP1 — Pure move-quality derivation domain

Implement pure functions or a small domain service that accepts normalized move/evaluation inputs and returns per-move facts.

Required tests:

- White player perspective;
- Black player perspective;
- zero loss;
- evaluation improvement clamped to zero;
- exact threshold boundaries: 49/50, 99/100, 199/200;
- exact best-move match;
- non-match with low CPL remains good;
- promotion UCI comparison;
- missing best move;
- missing before score;
- missing after score;
- non-contiguous evaluation pair;
- centipawn-to-mate transition;
- mate-to-centipawn transition;
- missed mate;
- allowed mate;
- mate sequence;
- no fake centipawn values.

No database access and no Stockfish binary in these tests.

Commit concept:

```text
feat: derive player move quality from stored evaluations
```

### CP2 — Game-level read service and API

Build the service that:

- loads the same completed run treated as current by existing analysis logic;
- loads the required moves and evaluations in bounded queries;
- invokes the pure derivation layer;
- computes game summary metrics;
- returns an analysis-state-aware response.

Required tests:

- complete game response;
- all non-complete analysis states;
- partial/gapped evaluation coverage;
- no completed run;
- legacy nullable `analysis_job_id`;
- correct run/config selection;
- GET performs no writes;
- Stockfish is never invoked;
- no global-count assertions.

Commit concept:

```text
feat: expose game move-quality analysis
```

### CP3 — Game-viewer review workflow

Implement:

- summary cards;
- move-list annotations;
- issue filters;
- previous/next issue navigation;
- jump-to-ply behavior;
- mate-specific labels;
- partial-coverage display;
- loading and error states.

Frontend tests must cover:

- player moves are annotated and opponent moves are not graded;
- filter behavior;
- clicking an issue synchronizes the board;
- exact-best-match is not used as the quality label;
- mate labels render distinctly;
- incomplete analysis does not display zero-valued metrics;
- existing move navigation and graph behavior remain intact.

Commit concept:

```text
feat: add move-quality review to game viewer
```

### CP4 — Upload-level aggregation backend

Add upload summary aggregation using completed usable runs only.

Required aggregates:

- coverage by analysis state;
- overall metrics;
- by color;
- by canonical opening;
- top review moves.

Required tests:

- mixed complete and incomplete library;
- zero completed games;
- one completed game;
- multiple openings;
- unknown opening;
- White-only and Black-only coverage;
- mate events excluded from ACPL but included in critical counts;
- deterministic ordering;
- pagination/list invariants do not regress;
- bounded query count or explicit protection against N+1 behavior.

Commit concept:

```text
feat: aggregate move quality across an upload
```

### CP5 — Deterministic training recommendations

Implement evidence-first recommendation rules.

Required tests:

- top mistakes are ranked correctly;
- low-sample openings are not recommended;
- weakest sufficiently represented opening is selected;
- color recommendation requires sufficient evidence;
- insufficient data returns fewer or no recommendations;
- every recommendation evidence id belongs to the requested upload;
- tie-breaking is deterministic;
- wording does not assert unsupported chess motifs;
- coverage note is present.

Commit concept:

```text
feat: generate grounded training recommendations
```

### CP6 — Upload dashboard and deep links

Implement:

- coverage display;
- aggregate metrics;
- by-color and by-opening sections;
- recommendation cards;
- top review-position list;
- deep links into game and ply;
- Analyze All Games CTA when incomplete.

Frontend tests:

- coverage numerator/denominator is accurate;
- incomplete coverage is visible;
- deep link opens the correct game and position;
- recommendations display evidence;
- empty and unavailable states are distinct;
- aggregate values do not imply unanalyzed games were included.

Commit concept:

```text
feat: add upload-level training insights
```

### CP7 — Grounding contract for future AI explanations

Do not call a model.

Create and document a stable internal evidence payload that could later be passed to a model. It should contain only:

- game metadata needed for context;
- played move;
- best move;
- before/after score;
- CPL or mate event;
- board position/FEN if already safe and useful;
- opening;
- exact source ids;
- engine provenance;
- formula/threshold version.

Add tests that:

- the payload contains no filesystem path;
- it contains no environment values;
- every statement can be traced to stored or derived data;
- it is deterministic for the same source data;
- it distinguishes facts from future generated prose.

This checkpoint preserves the original “grounded AI” direction without introducing the model platform prematurely.

Commit concept:

```text
docs: define evidence contract for future explanations
```

### CP8 — Regression hardening

Run both engine-configured and engine-free suites.

The following Sprint 5 tests are protected infrastructure and must remain capable of failing:

1. `FOR UPDATE SKIP LOCKED` regression test.
2. Background lease-renewal regression test.
3. Configured concurrency-cap regression test.

Do not rewrite these merely to make Sprint 6 tests easier.

Test-fixture rules:

- Use `db_session` for ordinary read/derivation tests.
- Use `committed_db` only for genuine multi-connection concurrency behavior.
- Never assert global database counts.
- Never share a SQLAlchemy Session across threads.
- Never call `db.rollback()` after a read-only query on a caller-supplied session.
- Keep test PGNs collision-proof through the existing per-session UUID mechanism.

Deliberately break at least these new mechanisms and confirm tests fail:

- reverse the player perspective;
- remove the CPL clamp;
- make 200 classify as mistake instead of blunder;
- include unanalyzed games in the aggregate denominator incorrectly;
- remove the recommendation evidence floor;
- flatten mate to a centipawn number.

Commit concept:

```text
test: harden move-quality and recommendation invariants
```

### CP9 — Documentation and acceptance evidence

Update:

- project status;
- API documentation;
- ADR;
- frontend terminology/help text;
- test commands;
- screenshots or a short demo checklist;
- known gaps and deferred work.

Explicitly retain these deferrals:

- re-analysis at a different depth;
- deployment/containerization;
- generative-AI explanations.

Commit concept:

```text
docs: complete Sprint 6 acceptance record
```

---

## 7. Acceptance criteria

Sprint 6 is complete only when all of the following are true.

### Product

- A completed analyzed game shows move-quality annotations for the selected player's moves.
- The user can filter issues and jump directly to them on the board.
- ACPL and mistake counts use stored evaluations and no engine execution.
- Mate errors remain mate errors, not fake CPL values.
- An upload dashboard reports move-quality aggregates with visible coverage.
- Recommendations are personalized, deterministic, and linked to evidence.
- Partial libraries degrade honestly and retain Analyze Game/Analyze All actions.
- No LLM or external AI service is required.

### Data and architecture

- No database migration was added for derived values.
- No derived move-quality rows are stored.
- Existing evaluations and moves are the source of truth.
- GET endpoints have no write side effects.
- API code never starts Stockfish.
- Worker code, leasing, claiming, heartbeat, recovery, and concurrency limits are behaviorally unchanged.
- Existing run/config resolution is reused rather than duplicated.
- Mate and centipawn values remain distinct types.

### Testing

- Pure derivation tests cover both colors, thresholds, gaps, and mates.
- Backend API tests cover every analysis state and partial coverage.
- Frontend tests cover review navigation, filters, and deep links.
- Aggregate tests never depend on global counts.
- Engine-free tests still pass with `STOCKFISH_PATH=""`.
- The three critical Sprint 5 concurrency tests still fail when their mechanisms are deliberately broken.
- New tests are proven capable of failing through deliberate mutation.
- Alembic reports no drift.

### Quality gates

From a clean checkout:

```powershell
docker compose up -d

cd backend
.\.venv\Scripts\python.exe -m pytest -q
$env:STOCKFISH_PATH=""
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check

cd ..\frontend
npm test
npm run lint
npm run build
```

Verify command exit statuses directly. Do not pipe lint or test commands through a command that can mask failure.

---

## 8. Explicit non-goals

Do not add any of the following during the required Sprint 6 checkpoints:

- OpenAI, Anthropic, Gemini, or other model SDKs.
- Prompt storage or prompt experimentation.
- Chat UI.
- Natural-language claims about tactical motifs.
- Multi-PV analysis.
- Re-running Stockfish to obtain move quality.
- User-selected engine options, depth, path, or thresholds.
- A derived move-quality database table.
- Worker refactors unrelated to a demonstrated Sprint 6 requirement.
- Redis, Celery, RabbitMQ, or another queue.
- Authentication.
- Opening-family rollups.
- Deployment, Dockerfiles, or Linux Stockfish substitution.
- Re-analysis controls at another depth.

A small bug discovered in touched code may be fixed with a focused regression test. Unrelated cleanup should be recorded rather than folded into the sprint.

---

## 9. Deferred follow-up roadmap

### Sprint 7 candidate: Grounded explanations

Once Sprint 6 facts and evidence are stable:

- choose a model provider abstraction;
- define prompt and explanation versions;
- send only the evidence contract;
- require structured output with cited move/ply ids;
- validate every referenced fact against the evidence;
- add cost/rate limits and timeouts;
- sanitize provider errors;
- decide whether outputs are cached and how invalidation works;
- provide a deterministic fallback when the provider is unavailable.

The LLM should explain already-derived facts. It must not calculate scores, identify the current run, or invent the recommendation evidence.

### Separate small feature: Re-analysis at another depth

The data model already supports a different depth as a different config hash. Add this later as an isolated UI/API feature, not as part of move-quality correctness.

### Separate infrastructure milestone: Deployment

Deployment must deliberately resolve:

- API process;
- worker process;
- PostgreSQL;
- platform-compatible Stockfish binary;
- engine binary hash and identity;
- persisted environment/configuration;
- migrations;
- worker health and restart behavior.

Do not substitute a Linux engine binary silently for the current Windows executable and then claim old analyses have the same engine identity.

---

## 10. Coding-agent guardrails

1. Read the project status, relevant ADRs, models, analysis services, worker tests, and test fixtures before changing code.
2. Do not infer evaluation indexing; prove it from code and tests.
3. Amend the ADR before production implementation.
4. Keep derivation functions pure and independently tested.
5. Reuse existing analysis-state and run-selection logic.
6. Never flatten mate scores.
7. Never start Stockfish from an HTTP request.
8. Do not touch worker concurrency code unless a failing test proves Sprint 6 requires it.
9. Keep ordinary tests engine-free.
10. Scope every database assertion to ids created by the test.
11. Make partial coverage visible in every aggregate.
12. Attach evidence to every recommendation.
13. Avoid unsupported chess claims.
14. Preserve direct command exit statuses.
15. Stop at each checkpoint, run its focused tests, then run the full affected suite before committing.

---

## 11. Definition of the finished v1 after Sprint 6

After this sprint, Chess Insights is a coherent resume-level v1:

```text
PGN import
→ canonical opening classification
→ durable saved history
→ durable Stockfish analysis
→ resumable batch jobs with progress/cancel/retry
→ evaluation graph and board synchronization
→ player-specific move-quality review
→ upload-level weakness summaries
→ evidence-linked training priorities
```

That is a complete useful product without generative AI.

Generative explanations can strengthen the project later, but they are not required to make the current system finished, technically credible, or demonstrable.
