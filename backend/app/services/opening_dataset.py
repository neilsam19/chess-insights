"""Loads the committed opening index into an EPD-keyed lookup.

Loading is separate from classification on purpose: this module knows how
to read and validate the vendored dataset, and nothing about chess. The
index is read once per process and reused - classification is a dict
lookup, never a scan.
"""

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "openings"
INDEX_PATH = DATA_DIR / "openings_index.tsv"
MANIFEST_PATH = DATA_DIR / "MANIFEST.json"

EXPECTED_COLUMNS = ["eco", "name", "pgn", "uci", "epd", "ply"]
EPD_FIELD_COUNT = 4


class OpeningDatasetError(Exception):
    """Raised when the vendored dataset is missing or malformed.

    Deliberately fatal: a silently-empty index would degrade every
    classification to "unknown", which is far harder to notice than a
    startup failure.
    """


@dataclass(frozen=True)
class OpeningRecord:
    eco: str
    name: str
    pgn: str
    uci: str
    epd: str
    ply: int


@dataclass(frozen=True)
class OpeningIndex:
    by_epd: Mapping[str, OpeningRecord]
    dataset_version: str
    max_ply: int
    """Deepest line in the dataset. A position beyond this can't be a named
    opening, so the classifier's backward walk starts here instead of at the
    end of a long game."""

    def lookup(self, epd: str) -> OpeningRecord | None:
        return self.by_epd.get(epd)

    def __len__(self) -> int:
        return len(self.by_epd)


def _validate_epd(epd: str, line_no: int) -> None:
    fields = epd.split(" ")
    if len(fields) != EPD_FIELD_COUNT:
        raise OpeningDatasetError(
            f"line {line_no}: expected {EPD_FIELD_COUNT} EPD fields, got {len(fields)}: {epd!r}"
        )
    if len(fields[0].split("/")) != 8:
        raise OpeningDatasetError(f"line {line_no}: malformed EPD board: {epd!r}")
    if fields[1] not in ("w", "b"):
        raise OpeningDatasetError(f"line {line_no}: bad side-to-move: {epd!r}")


def load_index_from(index_path: Path, manifest_path: Path) -> OpeningIndex:
    """Uncached loader - the cached entry point is `load_opening_index`."""
    if not index_path.exists():
        raise OpeningDatasetError(f"opening index not found: {index_path}")
    if not manifest_path.exists():
        raise OpeningDatasetError(f"opening manifest not found: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OpeningDatasetError(f"malformed manifest: {exc}") from exc

    dataset_version = manifest.get("source_commit")
    if not dataset_version:
        raise OpeningDatasetError("manifest is missing source_commit")

    lines = index_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise OpeningDatasetError(f"opening index is empty: {index_path}")

    header = lines[0].split("\t")
    if header != EXPECTED_COLUMNS:
        missing = [c for c in EXPECTED_COLUMNS if c not in header]
        raise OpeningDatasetError(
            f"unexpected columns {header}"
            + (f"; missing required {missing}" if missing else "")
        )

    by_epd: dict[str, OpeningRecord] = {}
    for line_no, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) != len(EXPECTED_COLUMNS):
            raise OpeningDatasetError(
                f"line {line_no}: expected {len(EXPECTED_COLUMNS)} columns, got {len(parts)}"
            )

        eco, name, pgn, uci, epd, ply_text = parts
        _validate_epd(epd, line_no)

        try:
            ply = int(ply_text)
        except ValueError as exc:
            raise OpeningDatasetError(f"line {line_no}: non-integer ply {ply_text!r}") from exc

        record = OpeningRecord(eco=eco, name=name, pgn=pgn, uci=uci, epd=epd, ply=ply)

        existing = by_epd.get(epd)
        if existing is not None:
            if existing == record:
                continue  # byte-identical duplicate row - harmless, keep one
            raise OpeningDatasetError(
                f"line {line_no}: conflicting metadata for EPD {epd!r}: "
                f"{existing.eco}/{existing.name} vs {eco}/{name}"
            )

        by_epd[epd] = record

    if not by_epd:
        raise OpeningDatasetError(f"opening index contains no records: {index_path}")

    return OpeningIndex(
        by_epd=MappingProxyType(by_epd),
        dataset_version=dataset_version,
        max_ply=max(record.ply for record in by_epd.values()),
    )


@functools.lru_cache(maxsize=1)
def load_opening_index() -> OpeningIndex:
    """The vendored index, loaded once per process and cached thereafter."""
    return load_index_from(INDEX_PATH, MANIFEST_PATH)
