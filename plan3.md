# Chess Insights — Sprint 2 Acceptance and Sprint 3 Plan

## Sprint 2 verdict

**Sprint 2 is accepted.**

The acceptance review is credible because it verified the implementation through several independent layers:

- Source-code inspection
- Automated tests
- A populated development database
- A freshly migrated empty database
- Raw SQL checks independent of the ORM
- A complete Docker restart
- Live API requests
- Frontend build and lint checks

The one discovered defect was real but limited to test isolation. The application behavior was correct; several tests incorrectly assumed that the entire database would be empty.

The fixes were appropriate:

1. Upload pagination now includes `Upload.id.desc()` as a deterministic tiebreaker.
2. Tests compare scoped records or changes from a captured baseline instead of global row counts.
3. Cascade and rollback tests now verify the records created by the test rather than asserting that unrelated tables are globally empty.

## Required Sprint 2 closeout

Before beginning Sprint 3:

- [ ] Commit the three Sprint 2 fixes.
- [ ] Run the complete backend suite against the populated development database.
- [ ] Run the complete backend suite against a freshly migrated empty database.
- [ ] Run `alembic check`.
- [ ] Run the frontend build.
- [ ] Run the frontend lint check.
- [ ] Record the final commit hash in the Sprint 2 acceptance notes - `<47b8b1e>`
- [x] Add the three nonblocking test gaps to the backlog:
  - automated preview-statelessness test
  - migration-from-empty CI check
  - frontend test runner

Once these actions are complete, Sprint 2 should be considered closed.

---

# Sprint 3 — Reliable Opening Classification

## Sprint objective

Replace dependence on inconsistent PGN `ECO` and `Opening` headers with deterministic opening classification based on the positions actually reached in each game.

After Sprint 3, Chess Insights should correctly identify openings even when a PGN:

- has no `Opening` header
- has no `ECO` header
- contains an incorrect opening name
- contains only a generic value such as `ECO B90`
- reaches an opening through a common transposition

The same classification must appear during stateless preview and after the analysis has been saved and reopened.

## User-facing result

Before Sprint 3:

> ECO B90

After Sprint 3:

> Sicilian Defense: Najdorf Variation
> ECO B90

The opening statistics should therefore describe the positions the user actually played rather than the metadata supplied by the PGN exporter.

---

# Source dataset

Use a versioned snapshot of the official `lichess-org/chess-openings` dataset.

The generated Lichess dataset provides:

- `eco`
- `name`
- `pgn`
- `uci`
- `epd`

Lichess recommends classifying a game by walking backward through its positions until a named position is found. The dataset uses EPD positions specifically to support position-based matching and common transpositions. It is distributed under CC0.

## Dataset requirements

- [ ] Store a fixed dataset snapshot or generated distribution inside the repository.
- [ ] Do not download the dataset during an API request.
- [ ] Do not depend on the Lichess website or API being available at runtime.
- [ ] Record the dataset version, date, or source commit in a manifest.
- [ ] Add the appropriate attribution and dataset information to the project README.
- [ ] Provide a documented process for replacing the snapshot with a newer version.
- [ ] Validate required columns when loading the dataset.
- [ ] Fail clearly during startup or testing if the dataset is missing or malformed.

A dataset update must be an intentional code change, not an uncontrolled runtime change.

---

# Classification design

## Required algorithm

Create a pure opening-classification service, such as:

```text
opening_classifier.py
```

The classifier should:

1. Load the Lichess opening records into an in-memory lookup keyed by normalized EPD.
2. Receive the ordered positions reached during a game.
3. Start with the deepest position in the game.
4. Walk backward until it finds a matching named opening position.
5. Return the first match found.
6. Treat that match as the deepest known opening reached in the game.

This is preferable to matching only the beginning of the UCI move string because position matching handles supported transpositions more naturally.

The parser already generates FEN before and after every ply, so Sprint 3 should reuse those positions rather than replaying the game in a second subsystem.

## Canonical position format

The classifier must normalize stored FEN positions into the same EPD format used by the dataset.

The normalization must consistently account for:

- board position
- side to move
- castling rights
- legal en passant state
- removal of halfmove and fullmove counters

Use one canonical conversion function everywhere. Do not duplicate EPD-normalization logic between preview, persistence, tests, and backfill code.

## Suggested result model

The pure classifier should return a structured result similar to:

```python
@dataclass(frozen=True)
class OpeningClassification:
    eco: str | None
    name: str | None
    matched_ply: int | None
    source: str
    dataset_version: str | None
```

Suggested `source` values:

```text
lichess
pgn_header
unknown
```

## Classification precedence

The precedence must be explicit:

1. **Lichess position match**
2. **PGN-header fallback**
3. **Unknown opening**

Therefore:

- A valid position-based match must override an incorrect PGN header.
- PGN headers may be retained as a fallback when no dataset position matches.
- Header values must never override a successful position match.
- A game with no match and no usable header must return a consistent unknown value.
- Missing or unusual opening data must never cause the entire PGN import to fail.

## Naming

Use the complete Lichess opening name as the canonical game-level value.

For example:

```text
Sicilian Defense: Najdorf Variation, English Attack
```

Do not attempt to redesign opening-family aggregation during this sprint. Existing opening statistics may continue grouping by the complete canonical name.

Possible future family-level rollups such as:

```text
Sicilian Defense
  └── Najdorf Variation
      └── English Attack
```

should be deferred until after classification itself is reliable.

---

# Integration requirements

## Parser integration

- [ ] Every parsed game is passed through the opening classifier.
- [ ] The classifier uses the game’s actual positions.
- [ ] The parser retains original PGN headers for fallback and debugging.
- [ ] The parser returns canonical opening information in its `ParsedGame` result.
- [ ] Malformed but recovered games are classified using their valid retained position prefix.
- [ ] A classification failure produces a warning or unknown result rather than failing the complete upload.

## Preview integration

`POST /api/uploads/preview` must:

- remain completely stateless
- classify every previewed game
- return the canonical ECO and opening name
- use exactly the same classifier as confirmed imports
- create no database rows
- make no network request to Lichess

## Save integration

`POST /api/uploads` must:

- run classification on the server
- not trust classification data submitted by the frontend
- persist the canonical game-level classification
- persist enough provenance to identify how the classification was produced
- remain a single all-or-nothing transaction

Previewing and then saving the same PGN under the same player name must produce the same opening classifications.

## Persistence model

Opening classification is a per-game annotation, not an aggregate statistic. It may therefore be stored without violating the rule that aggregate statistics remain derived.

The game record should contain or reuse fields representing:

```text
eco
opening_name
opening_source
opening_matched_ply
opening_dataset_version
```

Exact column names may follow the current project conventions.

Aggregate values such as opening win percentage must not be stored. They must continue to be calculated through the existing pure statistics layer.

## Existing saved analyses

Sprint 3 must not classify only newly uploaded files.

Existing saved analyses must also receive the new canonical classifications.

Implement one explicit, repeatable approach:

### Recommended approach: idempotent backfill command

Create an application command that:

1. Finds saved games requiring classification.
2. Uses their stored move positions.
3. Computes the canonical classification.
4. Updates only the classification metadata.
5. Can safely be run more than once.
6. Reports how many games were updated, unchanged, or could not be classified.
7. Runs transactionally at a sensible unit, such as one upload at a time.

Example conceptual command:

```bash
python -m app.scripts.reclassify_openings
```

Do not require users to delete and re-upload their previous analyses.

## Saved-analysis integration

When an old or new analysis is reopened:

- [ ] Its game summaries show canonical opening names.
- [ ] Its aggregate opening statistics use canonical classifications.
- [ ] Its selected-game view shows the same opening shown in the summary.
- [ ] `library_stats.py` continues to use the existing pure `compute_stats` flow.
- [ ] No aggregate statistics are introduced into the database.

---

# API requirements

Existing API shapes may be extended but should not be unnecessarily redesigned.

Each game summary should expose enough information for the frontend to display:

```json
{
  "eco": "B90",
  "opening_name": "Sicilian Defense: Najdorf Variation",
  "opening_source": "lichess",
  "opening_matched_ply": 10
}
```

`opening_dataset_version` may be returned if useful for diagnostics, but it does not need to be prominent in the normal UI.

## Compatibility requirements

- [ ] Existing endpoints continue to work.
- [ ] Existing pagination behavior remains unchanged.
- [ ] Game-summary responses still exclude full move arrays.
- [ ] Full moves remain lazily loaded.
- [ ] Duplicate detection remains unchanged.
- [ ] Preview remains stateless.
- [ ] Import rollback behavior remains unchanged.
- [ ] Existing upload IDs and routes remain valid.
- [ ] Unknown openings serialize consistently rather than producing frontend errors.

---

# Frontend requirements

The frontend changes should remain small.

## Required UI behavior

- [ ] Show the canonical opening name on each game summary.
- [ ] Show the ECO code alongside the opening when available.
- [ ] Show the same opening in the selected-game viewer.
- [ ] Use canonical names in the opening-performance table.
- [ ] Handle unknown openings cleanly.
- [ ] Do not display `null`, `undefined`, or blank opening labels.
- [ ] Do not require the frontend to classify or replay moves.
- [ ] Preserve existing preview, save, history, pagination, and lazy-loading behavior.

Suggested display:

```text
Sicilian Defense: Najdorf Variation · B90
```

Unknown fallback:

```text
Unknown opening
```

PGN-header fallback may be shown normally. The source does not need to be visible unless a debug or details view already exists.

---

# Frontend test foundation

Sprint 3 is the appropriate point to add a minimal frontend test runner because the UI is beginning to depend on more structured domain data.

Add:

- Vitest
- React Testing Library
- a test setup file
- an `npm test` or equivalent package script

Minimum frontend tests:

- [ ] A classified opening name and ECO code render correctly.
- [ ] An unknown opening renders the intended fallback.
- [ ] A missing optional ECO code does not render broken punctuation.
- [ ] Preview data and saved-analysis data use the same opening display component.
- [ ] Selecting a game still triggers lazy move loading.

This does not require comprehensive testing of every Sprint 1 and Sprint 2 component.

---

# Backend test requirements

## Dataset-loader tests

- [ ] Loads a valid dataset snapshot.
- [ ] Rejects a missing required column.
- [ ] Rejects or clearly handles malformed EPD.
- [ ] Handles identical duplicate records deterministically.
- [ ] Does not silently accept conflicting metadata for the same EPD.
- [ ] Loads once and reuses the cached index.

## Classifier unit tests

- [ ] Classifies a game with no opening headers.
- [ ] Classifies a game with only an ECO header.
- [ ] Overrides an incorrect PGN opening header.
- [ ] Selects the deepest matching opening rather than a parent opening.
- [ ] Recognizes a supported transposition by position.
- [ ] Returns the PGN-header fallback when no position matches.
- [ ] Returns unknown when neither the dataset nor headers provide a result.
- [ ] Handles a zero-move game.
- [ ] Handles a truncated malformed game using its valid prefix.
- [ ] Correctly normalizes castling rights.
- [ ] Correctly normalizes legal en passant state.
- [ ] Does not mutate its input game or moves.

## Service and route tests

- [ ] Preview returns classified openings.
- [ ] Preview still writes zero database rows.
- [ ] Confirmed import persists the same classification returned by preview.
- [ ] An injected persistence failure still rolls back the entire import.
- [ ] Saved game summaries return canonical opening values.
- [ ] Saved aggregate statistics use canonical opening values.
- [ ] Missing opening data does not change route status codes.
- [ ] Existing duplicate-import behavior still returns `409`.
- [ ] Existing deletion and cascade behavior still passes.

## Backfill tests

- [ ] Existing games are classified without being re-uploaded.
- [ ] Running the backfill twice produces no harmful changes.
- [ ] The backfill does not alter moves, results, players, or upload metadata.
- [ ] A failure in one upload does not leave that upload partially updated.
- [ ] The command reports updated, unchanged, fallback, and unknown counts.

---

# Manual acceptance cases

Create a small fixture PGN containing at least these games:

1. Correct opening headers
2. Missing opening headers
3. Incorrect opening headers
4. A generic ECO-only header
5. A known transposition
6. A game ending before a detailed variation is reached
7. A malformed game that retains a valid prefix
8. A game with no known opening match

Run the fixture through both workflows:

```text
preview
```

and:

```text
save → restart application → reopen analysis
```

Verify:

- [ ] Preview and saved classifications are identical.
- [ ] Incorrect headers are overridden.
- [ ] Missing headers do not prevent classification.
- [ ] The deepest recognized opening is selected.
- [ ] The transposed game receives the expected opening.
- [ ] Unknown games display safely.
- [ ] Opening statistics use the new canonical names.
- [ ] Existing analyses receive the same behavior after backfill.
- [ ] No request contacts an external service.
- [ ] Data survives a complete Docker restart.

---

# Performance expectations

Sprint 3 does not need elaborate optimization, but it must avoid obviously inefficient behavior.

Required:

- Load the opening dataset once per application process.
- Use a dictionary or equivalent indexed lookup for EPD matching.
- Avoid scanning the complete opening dataset for every move.
- Avoid replaying moves when their FEN positions are already available.
- Avoid loading the dataset separately for every game.
- Avoid network access during classification.
- Test with a representative multi-game PGN, not only one-game fixtures.

Classification should be approximately proportional to the number of positions in the imported games, not:

```text
number of game positions × number of dataset entries
```

---

# Sprint 3 non-goals

The following should not be added during Sprint 3:

- Stockfish
- centipawn evaluations
- mate scores
- best-move calculation
- blunder, mistake, or inaccuracy labels
- asynchronous analysis jobs
- background workers
- engine-analysis progress bars
- opening recommendations
- repertoire building
- LLM-generated explanations
- natural-language weakness reports
- family-level opening analytics redesign
- live Lichess API integration
- automatic downloading of newer datasets
- authentication or multi-user accounts

Sprint 3 should solve one problem completely:

> Determine the opening from the moves actually played.

---

# Definition of done

Sprint 3 is complete only when all of the following are true:

## Classification

- [ ] Opening classification is based on reached positions.
- [ ] The deepest known opening position is selected.
- [ ] Supported transpositions are recognized.
- [ ] Incorrect PGN headers cannot override a dataset match.
- [ ] Missing headers do not prevent classification.
- [ ] Fallback behavior is explicit and deterministic.

## Architecture

- [ ] The classifier is a pure, independently tested service.
- [ ] Dataset loading is isolated from classification.
- [ ] EPD normalization exists in one location.
- [ ] Preview and import use the same classifier.
- [ ] The frontend contains no chess-classification logic.
- [ ] Aggregate statistics remain derived.

## Persistence

- [ ] New imports persist canonical classification metadata.
- [ ] Existing imports can be backfilled.
- [ ] Backfill is idempotent.
- [ ] Dataset provenance is recorded.
- [ ] Existing records remain accessible.

## Regression safety

- [ ] All existing backend tests pass.
- [ ] All new classifier tests pass.
- [ ] All integration tests pass against a populated database.
- [ ] All integration tests pass against a freshly migrated database.
- [ ] Preview statelessness has an automated regression test.
- [ ] `alembic check` passes.
- [ ] Frontend tests pass.
- [ ] Frontend build passes.
- [ ] Frontend lint passes.
- [ ] Docker restart persistence still passes.

## End-to-end behavior

- [ ] A PGN with missing opening headers is correctly classified.
- [ ] A PGN with incorrect opening headers is correctly overridden.
- [ ] A transposed opening is correctly classified.
- [ ] Preview and reopened saved analysis show identical values.
- [ ] Opening statistics use canonical names.
- [ ] Unknown openings render without errors.
- [ ] Existing saved analyses are updated without re-uploading.

When every item passes, the final recommendation should be:

```text
Sprint 3 accepted
```

---

# Strict Sprint 3 acceptance-review prompt

Use the following prompt after implementation:

```text
Perform a strict Sprint 3 acceptance review of the current Chess Insights
repository. Do not implement Sprint 4 functionality.

Verify behavior using the actual code, automated tests, a populated PostgreSQL
database, a freshly migrated empty PostgreSQL database, and live API requests.

1. Verify that a versioned snapshot of the lichess-org/chess-openings dataset
   exists locally and that classification requires no runtime network access.

2. Verify that dataset loading validates the expected schema and creates an
   indexed EPD lookup.

3. Verify that the dataset is loaded once per application process rather than
   once per request or game.

4. Verify that FEN-to-EPD normalization is implemented in one shared location
   and correctly handles side to move, castling rights, and legal en passant.

5. Verify that the classifier walks positions backward and returns the deepest
   recognized opening position.

6. Verify classification using:
   - a game with no ECO or Opening headers
   - a game with correct headers
   - a game with incorrect headers
   - an ECO-only game
   - a known transposition
   - a short game reaching only a parent opening
   - a malformed game with a retained valid prefix
   - a game with no known match
   - a zero-move game

7. Verify the required precedence:
   Lichess position match → PGN-header fallback → unknown.

8. Verify that an incorrect PGN header never overrides a successful
   position-based classification.

9. Verify that preview remains stateless and creates no upload, game, or move
   rows. Confirm this with an automated test and a live populated database.

10. Verify that preview and confirmed import produce identical classifications
    for the same file and player.

11. Verify that POST /api/uploads performs classification on the server and does
    not trust classification values supplied by the frontend.

12. Verify that canonical ECO, opening name, source, matched ply, and dataset
    provenance are persisted or otherwise represented according to the Sprint 3
    design.

13. Verify that aggregate opening statistics remain derived and are not stored
    as database columns.

14. Verify that existing saved analyses can be reclassified without requiring
    the user to upload the PGN again.

15. Verify that the backfill process is idempotent, preserves unrelated data,
    and cannot leave one upload partially updated.

16. Verify that reopened analyses, game summaries, selected-game views, and
    opening-statistics tables all use canonical opening classifications.

17. Verify that unknown openings and missing ECO values serialize and render
    safely.

18. Verify that game-summary endpoints still exclude full move arrays and that
    moves remain lazily fetched.

19. Verify that the existing duplicate, rollback, pagination, restart,
    deletion, and cascade behavior remains unchanged.

20. Verify that the frontend has a working test runner and tests classified,
    fallback, and unknown opening rendering.

21. Run:
    - the complete backend test suite against the populated dev database
    - the complete backend test suite against a freshly migrated empty database
    - the opening backfill against existing data
    - alembic upgrade head from an empty database
    - alembic check
    - frontend tests
    - frontend build
    - frontend lint

22. Perform a complete Docker restart and reopen an analysis that was classified
    before the restart.

Return:
- PASS or FAIL for every numbered item
- supporting file paths and relevant functions/classes
- the exact dataset version or source commit
- test and command output summaries
- any missing automated coverage
- any behavior that depends on PGN headers
- any accidental Sprint 4 functionality
- a final recommendation of either:
  `Sprint 3 accepted`
  or
  `Sprint 3 needs fixes`

Only fix genuine Sprint 3 defects. Do not add Stockfish, engine evaluations,
move-quality labels, background jobs, or LLM features.
```

---

# What comes after Sprint 3

Sprint 3 creates trustworthy opening labels. It does not yet explain why games were won or lost.

The next stages should move from descriptive analytics toward diagnostic analytics.

## Sprint 4 — Single-game Stockfish analysis

### Goal

Prove the engine-analysis path on one selected saved game.

### Likely capabilities

- Start and communicate with Stockfish from the backend.
- Analyze every position in one selected game.
- Normalize evaluations to the player’s perspective.
- Handle centipawn and mate evaluations.
- Record engine version, depth or node limit, and analysis settings.
- Persist one evaluation per analyzed ply.
- Avoid recalculating positions that already have results under the same engine configuration.
- Display an evaluation graph or per-move evaluation list.
- Clearly distinguish:
  - not analyzed
  - analysis in progress
  - analysis complete
  - analysis failed

### Important boundary

Sprint 4 should analyze one game reliably before attempting an entire upload.

## Sprint 5 — Persistent engine jobs and bulk analysis

### Goal

Make engine work reliable for complete analyses containing many games.

### Likely capabilities

- Analysis-job records
- queued, running, complete, and failed states
- progress tracking
- retry behavior
- cancellation
- bounded concurrency
- engine-process cleanup
- resumable work after application restart
- bulk analysis of selected games or an entire upload
- configuration and result caching

This is where the existing `moves` table begins functioning as an engine-analysis work queue.

## Sprint 6 — Move-quality classification

### Goal

Translate raw engine evaluations into chess concepts.

### Likely capabilities

- centipawn-loss calculation
- best-move comparison
- inaccuracy, mistake, and blunder thresholds
- mate-aware classification
- player-relative move quality
- opening, middlegame, and endgame phase assignment
- critical-position detection
- game-level accuracy summaries

The thresholds must be documented and tested rather than copied blindly from another platform.

## Sprint 7 — Recurring weakness detection

### Goal

Aggregate engine results into personal patterns across many games.

Possible insights:

- opening variations with repeated early mistakes
- positions where the user consistently loses the advantage
- performance after leaving known opening theory
- tactical versus positional mistakes
- mistakes by game phase
- mistakes as White versus Black
- weaknesses against specific pawn structures
- time-control-specific patterns
- recurring move or square themes
- openings where results look good but move quality is poor
- openings where results look poor despite reasonable play

This is the point where Chess Insights begins answering:

> What do I repeatedly misunderstand?

rather than only:

> What were my results?

## Sprint 8 — Explanations and training recommendations

### Goal

Turn validated structured findings into understandable advice.

Possible capabilities:

- explain critical mistakes in natural language
- summarize recurring patterns
- suggest positions to review
- produce a focused study queue
- create opening-specific recommendations
- link explanations to exact board positions
- generate personalized review sessions
- optionally use an LLM over engine-backed structured evidence

The LLM should explain verified findings. It should not invent chess conclusions directly from raw PGN text.

---

# Product trajectory

The progression should remain:

```text
Sprint 1
PGN → parsed games → descriptive statistics → board viewer

Sprint 2
PGN → persistent analyses → history → reopenable dashboards

Sprint 3
Move sequence → trustworthy opening classification

Sprint 4
One saved game → Stockfish evaluations

Sprint 5
Complete upload → reliable persistent engine analysis

Sprint 6
Evaluations → move-quality and critical-position labels

Sprint 7
Many games → recurring personal weakness detection

Sprint 8
Verified weaknesses → explanations and training recommendations
```

The key milestone after Sprint 3 is that all future analysis can rely on a stable answer to:

> What opening position did this game actually reach?

That gives the engine-analysis stages a reliable way to connect individual mistakes to openings and variations rather than to inconsistent PGN metadata.
