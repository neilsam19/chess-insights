"""Backfill canonical opening classifications onto already-saved games.

    python -m app.scripts.reclassify_openings [--force] [--dry-run]

Uploads imported before Sprint 3 - or imported against an older dataset
snapshot - carry no canonical opening. This recomputes classification from
each game's *stored* positions, so nobody has to re-upload a PGN.

Only classification metadata is written. Moves, results, players, and
upload metadata are never touched. Work is committed one upload at a time,
so a failure part-way through leaves earlier uploads done, the failing
upload untouched, and later uploads simply not yet processed.
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import SessionLocal
from app.models.game import Game
from app.models.upload import Upload
from app.services.opening_classifier import classify_opening
from app.services.opening_dataset import OpeningIndex, load_opening_index

# Only these columns are ever written by the backfill.
CLASSIFICATION_COLUMNS = (
    "eco",
    "opening_name",
    "opening_source",
    "opening_matched_ply",
    "opening_dataset_version",
)


@dataclass
class BackfillReport:
    uploads_processed: int = 0
    uploads_failed: int = 0
    games_updated: int = 0
    games_unchanged: int = 0
    by_source: Counter = field(default_factory=Counter)

    @property
    def games_fallback(self) -> int:
        return self.by_source.get("pgn_header", 0)

    @property
    def games_unknown(self) -> int:
        return self.by_source.get("unknown", 0)

    def render(self) -> str:
        return (
            f"uploads processed : {self.uploads_processed}\n"
            f"uploads failed    : {self.uploads_failed}\n"
            f"games updated     : {self.games_updated}\n"
            f"games unchanged   : {self.games_unchanged}\n"
            f"  by source       : {dict(self.by_source)}\n"
            f"  header fallback : {self.games_fallback}\n"
            f"  unknown         : {self.games_unknown}"
        )


def _is_stale(game: Game, dataset_version: str) -> bool:
    """A game needs reclassifying if it was never classified, or was
    classified against a different dataset snapshot."""
    return (
        game.opening_source is None
        or game.opening_dataset_version != dataset_version
    )


def apply_classification(game: Game, index: OpeningIndex) -> bool:
    """Reclassify one game in place. Returns True if anything actually changed.

    Compare-then-write is what makes the backfill idempotent: re-running it
    over already-correct rows reports them as unchanged and issues no UPDATE.
    """
    positions = [move.fen_after for move in sorted(game.moves, key=lambda m: m.ply)]
    result = classify_opening(
        positions,
        eco_header=game.eco,
        opening_header=game.opening_name,
        index=index,
    )

    new_values = {
        "eco": result.eco,
        "opening_name": result.name,
        "opening_source": result.source,
        "opening_matched_ply": result.matched_ply,
        "opening_dataset_version": result.dataset_version,
    }

    changed = False
    for column in CLASSIFICATION_COLUMNS:
        if getattr(game, column) != new_values[column]:
            setattr(game, column, new_values[column])
            changed = True
    return changed


def reclassify_upload(
    db: Session, upload_id, *, index: OpeningIndex, force: bool = False
) -> tuple[int, int, Counter]:
    """Reclassify one upload's games. Caller owns the transaction boundary."""
    games = db.execute(
        select(Game)
        .where(Game.upload_id == upload_id)
        .options(selectinload(Game.moves))
        .order_by(Game.game_index)
    ).scalars().all()

    updated = unchanged = 0
    sources: Counter = Counter()

    for game in games:
        if not force and not _is_stale(game, index.dataset_version):
            unchanged += 1
            sources[game.opening_source] += 1
            continue

        if apply_classification(game, index):
            updated += 1
        else:
            unchanged += 1
        sources[game.opening_source] += 1

    return updated, unchanged, sources


def reclassify_all(
    db: Session,
    *,
    index: OpeningIndex | None = None,
    force: bool = False,
    dry_run: bool = False,
    upload_ids: Sequence | None = None,
) -> BackfillReport:
    """Reclassify every upload, or only `upload_ids` when given."""
    index = index if index is not None else load_opening_index()
    report = BackfillReport()

    stmt = select(Upload.id).order_by(Upload.uploaded_at)
    if upload_ids is not None:
        stmt = stmt.where(Upload.id.in_(list(upload_ids)))
    upload_ids = list(db.execute(stmt).scalars())

    for upload_id in upload_ids:
        try:
            updated, unchanged, sources = reclassify_upload(
                db, upload_id, index=index, force=force
            )
            if dry_run:
                db.rollback()
            else:
                db.commit()
        except Exception:
            # One bad upload must not abort the run or half-update itself.
            db.rollback()
            report.uploads_failed += 1
            continue

        report.uploads_processed += 1
        report.games_updated += updated
        report.games_unchanged += unchanged
        report.by_source.update(sources)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="reclassify every game, not only those from a different snapshot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without committing",
    )
    parser.add_argument(
        "--upload",
        action="append",
        dest="upload_ids",
        help="restrict to one upload id (repeatable); default is every upload",
    )
    args = parser.parse_args()

    index = load_opening_index()
    print(f"dataset snapshot: {index.dataset_version} ({len(index)} openings)")

    with SessionLocal() as db:
        report = reclassify_all(
            db,
            index=index,
            force=args.force,
            dry_run=args.dry_run,
            upload_ids=args.upload_ids,
        )

    if args.dry_run:
        print("(dry run - nothing committed)")
    print(report.render())


if __name__ == "__main__":
    main()
