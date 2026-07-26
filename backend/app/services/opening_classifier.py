"""Pure opening classification from the positions a game actually reached.

No I/O, no database, no network. Give it the ordered positions of a game
and its PGN headers; it returns what opening the game reached and how that
conclusion was arrived at.

The algorithm is the one Lichess recommends: walk the game's positions
backward and take the first that names an opening. That is the *deepest*
opening reached, and it handles transpositions for free - two different
move orders arriving at the same position classify identically, because
matching is on position, not on move sequence.
"""

from dataclasses import dataclass
from typing import Sequence

from app.services.epd import position_epd
from app.services.opening_dataset import OpeningIndex, load_opening_index

SOURCE_LICHESS = "lichess"
SOURCE_PGN_HEADER = "pgn_header"
SOURCE_UNKNOWN = "unknown"

# PGN header values that mean "absent" rather than a real value.
_PLACEHOLDERS = {"", "?", "-", "*"}


@dataclass(frozen=True)
class OpeningClassification:
    eco: str | None
    name: str | None
    matched_ply: int | None
    source: str
    dataset_version: str | None


def _clean_header(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return None if stripped in _PLACEHOLDERS else stripped


def classify_opening(
    position_fens: Sequence[str],
    *,
    eco_header: str | None = None,
    opening_header: str | None = None,
    index: OpeningIndex | None = None,
) -> OpeningClassification:
    """Classify the opening reached by a game.

    `position_fens` are the positions after each ply, in order, so
    `position_fens[i]` is the position after ply `i + 1`.

    Precedence is strict: a dataset position match always wins, PGN headers
    are only a fallback, and neither means unknown. A wrong `Opening`
    header therefore cannot override a real position match.

    Never raises on bad input - an unclassifiable game returns an unknown
    result so that one odd game cannot fail an entire import.
    """
    index = index if index is not None else load_opening_index()

    # A position deeper than the dataset's longest line can't be a named
    # opening, so start the backward walk there rather than at the end of a
    # possibly very long game.
    start_ply = min(len(position_fens), index.max_ply)

    for ply in range(start_ply, 0, -1):
        try:
            epd = position_epd(position_fens[ply - 1])
        except ValueError:
            # An unparseable stored FEN shouldn't abort classification -
            # keep walking back toward the opening.
            continue

        record = index.lookup(epd)
        if record is not None:
            return OpeningClassification(
                eco=record.eco,
                name=record.name,
                matched_ply=ply,
                source=SOURCE_LICHESS,
                dataset_version=index.dataset_version,
            )

    eco = _clean_header(eco_header)
    name = _clean_header(opening_header)

    # dataset_version is recorded even when the dataset didn't match, so a
    # later backfill can tell "unknown under the old snapshot" apart from
    # "unknown under the current one" and reclassify only what's stale.
    if eco is not None or name is not None:
        return OpeningClassification(
            eco=eco,
            name=name,
            matched_ply=None,
            source=SOURCE_PGN_HEADER,
            dataset_version=index.dataset_version,
        )

    return OpeningClassification(
        eco=None,
        name=None,
        matched_ply=None,
        source=SOURCE_UNKNOWN,
        dataset_version=index.dataset_version,
    )
