# ADR 0002 — Move quality derived from stored evaluations

**Status:** Accepted
**Date:** 2026-08-02
**Sprint:** 6
**Builds on:** ADR 0001, which produced the evaluations this ADR interprets

This is the Checkpoint 0 deliverable of
`CHESS-INSIGHTS-SPRINT-6-PLAN-REVISED.md`. No checkpoint after this one may
leave an index convention, a score comparison, a threshold, or a response shape
undecided.

---

## 1. Context

Sprint 5 produced 3,236 position evaluations across 40 of 79 games, each row
carrying a canonical White-perspective score and the engine's best move. None
of it is interpreted. The product shows an evaluation graph and nothing that
answers "which of my moves were bad, and where should I study".

Sprint 6 turns those rows into per-move quality, upload-level weakness
summaries, and deterministic training recommendations — without re-running
Stockfish, without touching the worker, and without calling a language model.

The original roadmap put grounded AI explanations here. That is deferred one
sprint, because an explanation layer cannot be built responsibly on facts that
do not exist yet. This sprint builds the facts and the evidence contract an
explanation layer would consume.

---

## 2. Decision

**Move quality is derived on read from existing rows. Nothing is stored, and
no migration is added.**

Every value in this sprint — centipawn loss, quality labels, best-move matches,
aggregates, recommendations — is computed from:

- the completed analysis run already selected by existing analysis resolution;
- that run's `position_evaluations`;
- the game's `moves`;
- the game's `user_color`;
- server-owned constants declared in this ADR.

Rationale:

- Every existing aggregate in the project is computed on read. Storing these
  would be the first derived-data exception.
- Stored rows would need invalidation whenever run selection, thresholds, or
  formulas changed — and this sprint changes all three from nothing to
  something.
- The library is small. Deriving an upload's worth of move quality is a few
  thousand rows of arithmetic.
- `GET` must stay side-effect free.

The derivation layer is kept pure enough that persistence could later be added
as a cache without changing its public meaning.

---

## 3. Verified data semantics

These were established by reading the code and querying the live database, not
assumed from field names. They are recorded here so no later checkpoint has to
re-derive them.

### 3.1 Index conventions

From [`analysis_executor.build_positions`](../../backend/app/services/analysis_executor.py):

```python
positions = [moves[0].fen_before] + [move.fen_after for move in moves]
```

| Thing | Convention |
|---|---|
| `Move.ply` | 1..N. Odd = White's move. |
| `PositionEvaluation.ply` | 0..N. The position **after** j plies; ply 0 is the start. |
| Move at ply `k` | transitions position `k-1` → position `k` |
| Before-score for move `k` | evaluation at ply `k-1` |
| After-score for move `k` | evaluation at ply `k` |
| Best move applicable to move `k` | `evaluation[k-1].best_move_uci` — the best move **from** the position the player faced |
| The player's moves | `user_color == "white"` → odd `k`; `"black"` → even `k` |
| Side to move at evaluation ply `j` | White if `j` is even |
| Side that moved **into** evaluation ply `j` | White if `j` is odd |

Verified across all 40 complete runs: each has exactly `move_count + 1`
evaluations, plies 0..N, no gaps, and exactly one complete run per game.

### 3.2 Score storage

Storage is canonical White-perspective. Exactly one of `score_cp_white` /
`mate_in_white` is populated, enforced by a check constraint. Perspective
conversion for the wire happens in `analysis_view._score_out` by multiplying by
`-1` for a Black player.

### 3.3 Completeness is all-or-nothing

`evaluate_positions` accumulates every evaluation in memory and persists only
after the whole game succeeds. A failed, abandoned, or cancelled attempt stores
zero rows (ADR 0001, invariant 5).

**Consequence:** a completed run cannot have gaps. Within-game partial coverage
is not a state this system can produce. The `unscored` machinery below is
retained as a defensive contract and for the genuinely missing-best-move case,
but no UI is built for within-game gaps. The partial coverage that is real is
*across an upload* — currently 40 games of 79.

---

## 4. Decisions

### D1 — Derive from model rows, not from the wire format

The derivation layer reads `score_cp_white`, `mate_in_white`, the evaluation
ply, and the player's colour, and normalizes them itself.

It does **not** consume `PositionEvaluationOut.score`, despite that field
existing for exactly this purpose. The reason is D2: a delivered checkmate is
stored as `mate_in_white = 0`, and `-1 × 0 == 0`, so the player-relative wire
value cannot distinguish "I mated" from "I was mated".

`PositionEvaluationOut.score` remains on the wire unchanged, for the frontend
and for clients that want the player-relative view of an ordinary position. The
existing analysis response shape is not modified by this sprint.

### D2 — `mate_in_white = 0` is resolved by ply parity

python-chess reports checkmate-on-the-board as `Mate(0)`, identically whichever
side was mated. Four such rows exist in the database, all at a final ply, all
with a null `best_move_uci`.

A naive sign test misclassifies **the winning checkmate as a missed mate**,
because zero is neither positive nor negative.

**Rule:** `mate_in_white == 0` at evaluation ply `j` means the side that moved
into ply `j` delivered mate. That side is White when `j` is odd.

Verified against all four rows:

| eval ply | parity | game result | reading |
|---:|---|---|---|
| 7 | odd | 1-0 | White moved, White mated |
| 4 | even | 0-1 | Black moved, Black mated |
| 89 | odd | 1-0 | White moved, White mated |
| 85 | odd | 1-0 | White moved, White mated |

`mate 0` at ply 0 is impossible and is treated as malformed
(`unscored_reason = "ambiguous_mate"`) rather than guessed.

`frontend/src/utils/evaluation.ts` already resolves this ambiguity for the
graph by carrying the previous direction forward. That is display geometry.
It must not be reused for grading, where parity gives the exact answer.

### D3 — Scores normalize into three kinds, never into a number

Every stored score is normalized before comparison into one of:

| Kind | Meaning | Carries |
|---|---|---|
| `cp` | ordinary assessment | player-relative centipawns |
| `mate_for` | the player has a forced mate | distance (0 = mate on the board) |
| `mate_against` | the player is being mated | distance (0 = mate on the board) |

Collapsing the sign into the kind makes the transition table in D4 a direct
3×3 lookup and makes it structurally impossible to subtract a mate from a
centipawn score.

Mate is never converted to `±10000`, `±100000`, or any other centipawn stand-in,
at any layer, for any purpose. Changes in mate *distance* are never converted
into centipawn loss.

### D4 — The full mate transition table

| before ↓ / after → | `cp` | `mate_for` | `mate_against` |
|---|---|---|---|
| **`cp`** | CPL, labelled by D5 | `good`, cpl null *(found a forced mate)* | `allowed_mate`, cpl null |
| **`mate_for`** | `missed_mate`, cpl null | `unscored` + `mate_sequence` | `allowed_mate`, cpl null |
| **`mate_against`** | `good`, cpl null *(escaped)* | `good`, cpl null | `unscored` + `mate_sequence` |

Three of these are ordinary events that will occur in real games and are not
mistakes: finding a mate, escaping a mate, and being already lost and staying
lost. The diagonal `mate_sequence` cells are not gradable — they are excluded
from ACPL, from critical counts, and from the mistake totals.

`missed_mate` and `allowed_mate` are **critical review items**. They count with
blunders in the critical-mistakes total while keeping their specific type, and
they always carry `centipawn_loss = null`.

### D5 — Fixed, server-owned thresholds

```text
centipawn_loss = max(0, before.cp - after.cp)     # player-relative, integers
```

| Label | Centipawn loss |
|---|---:|
| `good` | 0–49 |
| `inaccuracy` | 50–99 |
| `mistake` | 100–199 |
| `blunder` | 200+ |

- Improvements clamp to zero. A move is never assigned negative loss.
- Computed with integers, returned as integers.
- Named backend constants with boundary tests at 49/50, 99/100, 199/200.
- **Never a request parameter.** A client cannot choose thresholds, any more
  than it can choose an engine path or a depth.

### D6 — No branded accuracy score

This sprint exposes transparent metrics, not an invented or imitated accuracy
formula: average and median centipawn loss, exact best-move count and rate,
inaccuracy / mistake / blunder counts, missed and allowed mates, graded and
ungraded move counts, and coverage.

A future sprint may add an explicitly named, versioned application-specific
score. It must not be described as equivalent to Chess.com or Lichess accuracy
without deliberately matching and validating a published definition.

### D7 — Exact best-move match is evidence, not a grade

```text
is_exact_best_move = normalized(played_uci) == normalized(best_move_uci)
```

- Compare normalized UCI strings; promotion suffixes stay part of the
  comparison (`e7e8q` ≠ `e7e8`).
- A null or absent `best_move_uci` yields `None`, never `False`.
- A move is **never** classified as inaccurate merely because this is false.
  Several moves can be similarly strong; the UI must say so.
- The rate's denominator excludes moves with a null best move.

In the current data the only null best moves are the four at final plies, and a
final position is never a *before* position — so this path is defensive, not
routine.

### D8 — Only the selected player's moves are graded

Grading covers the moves of the player chosen when the PGN was imported. The
opponent's moves are not graded and do not enter any count.

The derivation service may be written so both colours could be graded later,
but neither the API nor the UI promises it in this sprint.

### D9 — `user_color == "unknown"` is not gradable

`user_color` is `"unknown"` when the confirmed player name matched neither side,
reachable for any upload saved under a name absent from the file.
`perspective_for_game` falls back to the White view, which is correct for
displaying a score and wrong for personal feedback: it would present White's
moves as the user's.

Such a game returns a distinct `not_gradable` state, is excluded from upload
aggregates, and is counted in its own coverage bucket.

### D10 — Derive from the run the product already treats as current

Move-quality endpoints reuse `analysis_repo.get_latest_complete_run_for_game`
and the existing config resolution. This sprint does not create a second
definition of "the current analysis". If multiple depths ever coexist, move
quality follows whatever that resolution selects.

### D11 — Partial coverage is a first-class state

- Per-game move quality exists only for a game with a completed usable run.
- Upload aggregates include completed usable games only.
- Every aggregate response reports numerator **and** denominator.
- Unanalyzed, queued, running, failed, cancelled, and not-gradable games are
  counted separately.
- The UI offers the existing Analyze Game / Analyze All Games actions instead
  of showing misleading zeroes.
- 40 analysed games must never render as 79.

### D12 — Move quality gets its own endpoint

`GET /api/games/{game_id}/move-quality`, separate from
`GET /api/games/{game_id}/analysis`.

`GameAnalysisPanel` polls the analysis endpoint every 1.5 s while a job is
active. Folding move quality into that response would run the derivation on
every poll, for data that cannot change while the job is running. A separate
endpoint also lets the non-complete states carry their own shape without
disturbing a response that four Sprint 5 test files depend on.

### D13 — Every new endpoint is read-only

No job is enqueued, no row is inserted or modified, no threshold is stored, and
no engine process is started. API code never starts Stockfish — that remains
the worker's sole responsibility, unchanged from ADR 0001.

### D14 — Three counts, not one

The distinction matters because a move can be graded without having a numeric
centipawn loss (every non-diagonal mate cell in D4).

| Count | Definition |
|---|---|
| `total_player_moves` | every move by the selected player |
| `graded_moves` | quality label is not `unscored` |
| `numeric_cpl_moves` | has an integer `centipawn_loss` — **the ACPL and median sample** |
| `unscored_moves` | `total_player_moves - graded_moves` |

`coverage_percent` uses `graded_moves / total_player_moves`. ACPL and median
report `numeric_cpl_moves` alongside them as their own sample size. A game with
zero numeric moves reports **null** ACPL, never `0` — "ACPL 0" reads as perfect
play.

---

## 5. Unscored reason codes

A move is reported as unscored with a machine-readable reason rather than
guessed. The UI may group these as "not scored"; the tests distinguish them.

| Code | Cause |
|---|---|
| `missing_before_evaluation` | no evaluation at ply `k-1` |
| `missing_after_evaluation` | no evaluation at ply `k` |
| `mate_sequence` | both sides of the comparison are mate in the same direction (D4 diagonal) |
| `ambiguous_mate` | a `mate 0` whose direction cannot be resolved, e.g. at ply 0 |
| `missing_move_data` | the stored move or its UCI is unavailable |

---

## 6. Response contracts

Shapes follow the project's existing Pydantic / TypeScript tagged-union
convention. Names may be adapted to repository naming; the separation of facts
may not.

### Per move

```text
MoveQualityOut
  ply, move_number, side, san, played_move_uci
  best_move_uci, is_exact_best_move: bool | null
  score_before: ScoreOut, score_after: ScoreOut     # existing score union
  centipawn_loss: int | null
  quality: good | inaccuracy | mistake | blunder | unscored
  special_classification: missed_mate | allowed_mate | mate_sequence | null
  unscored_reason: string | null
```

### Per game

```text
GameMoveQualityOut  (status = "complete")
  game_id, analysis_run_id, engine provenance, player_color
  total_player_moves, graded_moves, numeric_cpl_moves, unscored_moves
  coverage_percent
  average_centipawn_loss: int | null
  median_centipawn_loss: int | null
  exact_best_move_count, exact_best_move_rate
  inaccuracies, mistakes, blunders
  missed_mates, allowed_mates, critical_mistakes
  moves: MoveQualityOut[]
```

Tagged union for the endpoint:

```text
not_analyzed | queued | running | failed | cancelled | not_gradable | complete
```

Only `complete` carries summaries and annotations. The non-complete variants
reuse the existing `game_analysis_state` shapes, including `can_cancel` and
`can_retry`, rather than recreating action legality in the client.

### Per upload

`GET /api/uploads/{upload_id}/move-quality`

```text
UploadMoveQualityOut
  upload_id
  coverage { total_games, complete_games, queued_games, running_games,
             failed_games, cancelled_games, not_analyzed_games,
             not_gradable_games, graded_player_moves, numeric_cpl_moves }
  overall  { average_centipawn_loss, median_centipawn_loss,
             exact_best_move_rate, inaccuracies, mistakes, blunders,
             missed_mates, allowed_mates, critical_mistakes_per_game }
  by_color[]        # with sample sizes
  by_opening[]      # with sample sizes
  top_review_moves[]
  recommendations[]
```

Aggregation uses bounded queries — games with their complete run, moves for
those games, evaluations for those runs — then aggregates in Python, matching
the existing `library_stats` pattern. No query per game and none per move.

---

## 7. Recommendations

Deterministic, generated from facts, never from a model. Each carries display
text **and** structured evidence.

```text
TrainingRecommendationOut
  type, priority, title, explanation
  evidence_summary, evidence_items[], coverage_note
```

Four types: review largest mistakes, opening priority, colour priority, and a
tactical-review proxy.

**List sizes.** The review-mistakes recommendation carries up to 5 evidence
items; the dashboard's standalone "top review positions" list shows up to 10.
Ranking is by centipawn loss descending, with mate events ranked above all
numeric losses, and ties broken deterministically by `(game_index, ply)`.

**Evidence floors.** An opening is recommended only with at least 2 completed
games **and** at least 20 graded player moves in that opening. A colour is
recommended only when both colours have sufficient coverage and the difference
is meaningful.

These floors will frequently produce no opening recommendation for the current
library: 40 analysed games spread across roughly 30 canonical openings, largest
bucket 5. That is the designed behaviour, not a defect. The by-opening table is
still shown with its sample counts; only the *recommendation* is gated.
Opening-family rollups, where the sample size actually lives, are out of scope
this sprint.

**Omission is valid.** Insufficient evidence returns fewer recommendations with
an explicit coverage note. Space is never filled with generic advice
disconnected from stored data.

**Wording.** The system does not classify chess motifs, so recommendations must
not invent them. "Review these high-impact positions" is permitted. "You are
weak at forks", "you miss pins", "your endgames are poor" are not. This is
enforced by a test asserting against a denylist of motif terms, so it cannot
regress through a string edit.

---

## 8. Versioning

```python
MOVE_QUALITY_VERSION = 1
```

Carried in the evidence contract. It changes when a threshold, a formula, the
mate transition table, or a coverage definition changes — so a stored or
transmitted evidence payload can always be traced to the rules that produced it.

Independent of `ANALYSIS_BEHAVIOR_VERSION`, which identifies how evaluations
were *produced*. This one identifies how they are *interpreted*.

---

## 9. Non-goals

Not built in this sprint:

- Any model SDK, prompt storage, chat UI, or generated prose.
- Natural-language claims about tactical motifs.
- Multi-PV analysis, or re-running Stockfish to obtain move quality.
- User-selected engine options, depth, path, or thresholds.
- A derived move-quality table, or any migration.
- Grading the opponent's moves.
- Opening-family rollups.
- Within-game partial-coverage UI (§3.3 — the state cannot occur).
- Worker refactors not proven necessary by a failing test.
- Redis, Celery, or another queue. Authentication.
- Re-analysis at another depth; deployment and containerization — both
  deliberately deferred again, with the reasoning in PROJECT-STATUS §8.

---

## 10. Invariants this ADR adds

1. No migration is added; `alembic check` stays clean.
2. No derived move-quality row is ever stored.
3. `GET` endpoints perform no writes.
4. API code never starts an engine process.
5. Mate and centipawn values remain distinct types at every layer.
6. A mate score is never converted into a centipawn number.
7. `mate 0` is resolved by ply parity, never by sign.
8. Only the selected player's moves are graded.
9. A game with an unknown player colour is never graded.
10. Thresholds and formulas are server-owned and never request parameters.
11. Every aggregate reports numerator and denominator coverage.
12. Every recommendation carries evidence ids belonging to the requested upload.
13. Run selection reuses the existing resolution; no second definition exists.
14. Worker, leasing, claiming, heartbeat, recovery, and concurrency behaviour is
    unchanged.
