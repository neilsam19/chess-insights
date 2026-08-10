"""Move quality aggregated across one upload.

Read-only, like every other move-quality endpoint: nothing is written, no job
is enqueued, and no engine is started.

Three rules shape the module.

**Bounded queries, arithmetic in Python.** Games, their moves, their runs,
their jobs and their evaluations are each loaded in one query for the whole
upload, then aggregated in memory - the same shape as the existing
`library_stats`. For this library that is roughly three thousand rows. "No
N+1" is a property of the query *count*, and the tests assert it rather than
assume it.

**Coverage is classified exactly as `game_analysis_state` classifies it.**
The bucketing below walks the same precedence, in the same order, using
repository queries documented to return what the single-game lookups return.
A second definition of "the current analysis" would eventually disagree with
the first, and the disagreement would surface as a dashboard that contradicts
the game it links to.

**Only completed, gradable games contribute.** Everything else is counted in
its own bucket. A game being re-analysed reports as running, not complete,
because that is what its own endpoint says - showing its old numbers here
while the game page refuses to show them would be worse than showing neither.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.analysis_job import JOB_ACTIVE_STATUSES, JOB_CANCELLED, JOB_QUEUED
from app.models.analysis_run import (
    STATUS_CANCELLED,
    STATUS_COMPLETE,
    STATUS_RUNNING,
    AnalysisRun,
)
from app.models.analysis_job import AnalysisJob
from app.models.game import Game
from app.models.upload import Upload
from app.repositories import analysis as analysis_repo
from app.repositories import analysis_jobs as jobs_repo
from app.repositories import games as game_repo
from app.schemas.move_quality import PlayerScoreOut
from app.schemas.upload_move_quality import (
    ColorMoveQualityOut,
    MoveQualityMetricsOut,
    OpeningMoveQualityOut,
    ReviewMoveOut,
    UploadCoverageOut,
    UploadMoveQualityOut,
)
from app.services.training_recommendations import build_recommendations
from app.services.move_quality import (
    BLACK,
    GRADABLE_COLORS,
    MOVE_QUALITY_VERSION,
    WHITE,
    MoveQuality,
    PlayerScore,
    StoredScore,
    derive_move_quality,
    is_reviewable,
    review_severity_key,
    summarize_moves,
)
from app.services.move_quality_view import load_played_moves

# Coverage bucket names. These are the response field prefixes, kept as
# constants so a typo cannot silently create a bucket nothing ever reads.
COMPLETE = "complete"
QUEUED = "queued"
RUNNING = "running"
FAILED = "failed"
CANCELLED = "cancelled"
NOT_ANALYZED = "not_analyzed"
NOT_GRADABLE = "not_gradable"

BUCKETS = (
    COMPLETE,
    QUEUED,
    RUNNING,
    FAILED,
    CANCELLED,
    NOT_ANALYZED,
    NOT_GRADABLE,
)

# Enough to act on without turning the dashboard into the move list again.
TOP_REVIEW_MOVE_LIMIT = 10


@dataclass(frozen=True)
class GradedGame:
    """One completed, gradable game and the facts derived from it."""

    game: Game
    facts: list[MoveQuality]


def _first_per_game(rows: list) -> dict[uuid.UUID, object]:
    """Keep the first row seen for each game.

    The repository orders newest-first within each game, so "first" is
    "latest" - the same row the single-game lookup would have returned.
    """
    chosen: dict[uuid.UUID, object] = {}
    for row in rows:
        chosen.setdefault(row.game_id, row)
    return chosen


def _coverage_bucket(
    game: Game,
    *,
    active_job: AnalysisJob | None,
    complete_run: AnalysisRun | None,
    latest_job: AnalysisJob | None,
    latest_run: AnalysisRun | None,
) -> str:
    """Which bucket one game falls in.

    Mirrors `game_analysis_state`'s precedence, with the colour check first for
    the same reason `game_move_quality` puts it first: a game with no
    identifiable player can never be graded however often it is analysed, so
    calling it "not analyzed" would invite work that cannot help.
    """
    if game.user_color not in GRADABLE_COLORS:
        return NOT_GRADABLE

    if active_job is not None:
        return QUEUED if active_job.status == JOB_QUEUED else RUNNING

    if complete_run is not None:
        return COMPLETE

    if latest_job is not None:
        return CANCELLED if latest_job.status == JOB_CANCELLED else FAILED

    # Sprint 4 left runs with no job behind them; they are still reportable.
    if latest_run is not None:
        if latest_run.status == STATUS_RUNNING:
            return RUNNING
        if latest_run.status == STATUS_CANCELLED:
            return CANCELLED
        return FAILED

    return NOT_ANALYZED


def _metrics(entries: list[GradedGame]) -> MoveQualityMetricsOut:
    """Aggregate metrics over a set of games.

    Reuses the per-game summary function unchanged, so an upload's average
    centipawn loss is computed by exactly the arithmetic a single game's is.
    """
    facts = [fact for entry in entries for fact in entry.facts]
    return MoveQualityMetricsOut(games=len(entries), **vars(summarize_moves(facts)))


def _opening_label(game: Game) -> str | None:
    """The name this game's opening is grouped under, or None if unclassified.

    Follows `statistics.opening_label`, except that its "Unknown" sentinel
    becomes a structural null - a bucket the client can recognise rather than a
    string that could collide with a real opening name.
    """
    if game.opening_name:
        return game.opening_name
    if game.eco:
        return game.eco
    return None


def _by_color(entries: list[GradedGame]) -> list[ColorMoveQualityOut]:
    grouped: dict[str, list[GradedGame]] = {WHITE: [], BLACK: []}
    for entry in entries:
        grouped[entry.game.user_color].append(entry)

    return [
        ColorMoveQualityOut(color=color, metrics=_metrics(grouped[color]))
        for color in (WHITE, BLACK)
        if grouped[color]
    ]


def _shared_eco(entries: list[GradedGame]) -> str | None:
    ecos = {entry.game.eco for entry in entries}
    return ecos.pop() if len(ecos) == 1 else None


def _by_opening(entries: list[GradedGame]) -> list[OpeningMoveQualityOut]:
    grouped: dict[str | None, list[GradedGame]] = defaultdict(list)
    for entry in entries:
        grouped[_opening_label(entry.game)].append(entry)

    buckets = [
        OpeningMoveQualityOut(
            opening=opening,
            eco=_shared_eco(group),
            metrics=_metrics(group),
        )
        for opening, group in grouped.items()
    ]

    # Most played first, then alphabetical, with the unclassified bucket last.
    # Every key in the sort is total, so two runs of the same data cannot
    # produce two different orders.
    buckets.sort(key=lambda b: (-b.metrics.games, b.opening is None, b.opening or ""))
    return buckets


def _player_score_out(score: PlayerScore | None) -> PlayerScoreOut | None:
    if score is None:
        return None
    return PlayerScoreOut(kind=score.kind, cp=score.cp, mate_distance=score.mate_distance)


def _ranked_review_moves(entries: list[GradedGame]) -> list[ReviewMoveOut]:
    """Every move worth reviewing, worst first.

    Returned in full rather than truncated: the response shows the first ten,
    but a recommendation scoped to one opening or one colour needs to pick its
    own best positions out of the whole list.
    """
    candidates: list[tuple[GradedGame, MoveQuality]] = [
        (entry, fact)
        for entry in entries
        for fact in entry.facts
        if is_reviewable(fact)
    ]

    # Severity decides the order; game index and ply break every tie, so the
    # list is stable across requests and across equal-severity moves.
    candidates.sort(
        key=lambda pair: (
            review_severity_key(pair[1]),
            pair[0].game.game_index,
            pair[1].ply,
        )
    )

    return [
        ReviewMoveOut(
            game_id=entry.game.id,
            game_index=entry.game.game_index,
            ply=fact.ply,
            move_number=fact.move_number,
            side=fact.side,
            san=fact.san,
            player_color=entry.game.user_color,
            opening=_opening_label(entry.game),
            eco=entry.game.eco,
            best_move_uci=fact.best_move_uci,
            score_before=_player_score_out(fact.score_before),
            score_after=_player_score_out(fact.score_after),
            centipawn_loss=fact.centipawn_loss,
            quality=fact.quality,
            special_classification=fact.special_classification,
        )
        for entry, fact in candidates
    ]


def upload_move_quality(db: Session, upload: Upload) -> UploadMoveQualityOut:
    """Everything the dashboard needs about one upload's move quality."""
    games = game_repo.list_all_games_with_moves(db, upload.id)
    game_ids = [game.id for game in games]

    runs = analysis_repo.list_runs_for_games(db, game_ids)
    jobs = jobs_repo.list_jobs_for_games(db, game_ids)

    latest_run = _first_per_game(runs)
    complete_run = _first_per_game([r for r in runs if r.status == STATUS_COMPLETE])
    latest_job = _first_per_game(jobs)
    active_job = _first_per_game([j for j in jobs if j.status in JOB_ACTIVE_STATUSES])

    counts = {bucket: 0 for bucket in BUCKETS}
    complete_games: list[tuple[Game, AnalysisRun]] = []

    for game in games:
        bucket = _coverage_bucket(
            game,
            active_job=active_job.get(game.id),
            complete_run=complete_run.get(game.id),
            latest_job=latest_job.get(game.id),
            latest_run=latest_run.get(game.id),
        )
        counts[bucket] += 1
        if bucket == COMPLETE:
            complete_games.append((game, complete_run[game.id]))

    evaluations = analysis_repo.list_position_evaluations_for_runs(
        db, [run.id for _, run in complete_games]
    )
    scores_by_run: dict[uuid.UUID, list[StoredScore]] = defaultdict(list)
    for evaluation in evaluations:
        scores_by_run[evaluation.analysis_run_id].append(
            StoredScore(
                ply=evaluation.ply,
                score_cp_white=evaluation.score_cp_white,
                mate_in_white=evaluation.mate_in_white,
                best_move_uci=evaluation.best_move_uci,
            )
        )

    entries = [
        GradedGame(
            game=game,
            facts=derive_move_quality(
                load_played_moves(game), scores_by_run[run.id], game.user_color
            ),
        )
        for game, run in complete_games
    ]

    overall = _metrics(entries)
    by_color = _by_color(entries)
    by_opening = _by_opening(entries)
    ranked_review_moves = _ranked_review_moves(entries)

    coverage = UploadCoverageOut(
        total_games=len(games),
        complete_games=counts[COMPLETE],
        queued_games=counts[QUEUED],
        running_games=counts[RUNNING],
        failed_games=counts[FAILED],
        cancelled_games=counts[CANCELLED],
        not_analyzed_games=counts[NOT_ANALYZED],
        not_gradable_games=counts[NOT_GRADABLE],
        graded_player_moves=overall.graded_moves,
        numeric_cpl_moves=overall.numeric_cpl_moves,
    )

    return UploadMoveQualityOut(
        upload_id=upload.id,
        move_quality_version=MOVE_QUALITY_VERSION,
        coverage=coverage,
        overall=overall,
        by_color=by_color,
        by_opening=by_opening,
        top_review_moves=ranked_review_moves[:TOP_REVIEW_MOVE_LIMIT],
        recommendations=build_recommendations(
            coverage=coverage,
            overall=overall,
            by_color=by_color,
            by_opening=by_opening,
            review_moves=ranked_review_moves,
        ),
    )


__all__ = ["BUCKETS", "TOP_REVIEW_MOVE_LIMIT", "GradedGame", "upload_move_quality"]
