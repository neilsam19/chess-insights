import json
from pathlib import Path

import pytest

from app.services.opening_dataset import (
    EXPECTED_COLUMNS,
    OpeningDatasetError,
    load_index_from,
    load_opening_index,
)

VALID_ROW = (
    "A00\tAmar Opening\t1. Nh3\tg1h3\t"
    "rnbqkbnr/pppppppp/8/8/8/7N/PPPPPPPP/RNBQKB1R b KQkq -\t1"
)
SECOND_ROW = (
    "B90\tSicilian Defense: Najdorf Variation\t1. e4 c5\te2e4 c7c5\t"
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -\t2"
)


def _write(tmp_path: Path, body: str, manifest: dict | None = None) -> tuple[Path, Path]:
    index_path = tmp_path / "openings_index.tsv"
    index_path.write_text(body, encoding="utf-8")

    manifest_path = tmp_path / "MANIFEST.json"
    payload = {"source_commit": "abc123"} if manifest is None else manifest
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return index_path, manifest_path


def _valid_body(*rows: str) -> str:
    return "\t".join(EXPECTED_COLUMNS) + "\n" + "\n".join(rows) + "\n"


def test_loads_a_valid_snapshot(tmp_path: Path) -> None:
    index = load_index_from(*_write(tmp_path, _valid_body(VALID_ROW, SECOND_ROW)))

    assert len(index) == 2
    assert index.dataset_version == "abc123"

    record = index.lookup("rnbqkbnr/pppppppp/8/8/8/7N/PPPPPPPP/RNBQKB1R b KQkq -")
    assert record is not None
    assert record.eco == "A00"
    assert record.name == "Amar Opening"
    assert record.ply == 1


def test_unknown_epd_returns_none(tmp_path: Path) -> None:
    index = load_index_from(*_write(tmp_path, _valid_body(VALID_ROW)))

    assert index.lookup("8/8/8/8/8/8/8/8 w - -") is None


def test_missing_required_column_is_rejected(tmp_path: Path) -> None:
    body = "eco\tname\tpgn\tuci\tepd\n" + VALID_ROW + "\n"

    with pytest.raises(OpeningDatasetError, match="ply"):
        load_index_from(*_write(tmp_path, body))


def test_malformed_epd_is_rejected(tmp_path: Path) -> None:
    body = _valid_body("A00\tBad\t1. Nh3\tg1h3\tnot-an-epd\t1")

    with pytest.raises(OpeningDatasetError, match="EPD fields"):
        load_index_from(*_write(tmp_path, body))


def test_bad_side_to_move_is_rejected(tmp_path: Path) -> None:
    body = _valid_body(
        "A00\tBad\t1. Nh3\tg1h3\trnbqkbnr/pppppppp/8/8/8/7N/PPPPPPPP/RNBQKB1R x KQkq -\t1"
    )

    with pytest.raises(OpeningDatasetError, match="side-to-move"):
        load_index_from(*_write(tmp_path, body))


def test_non_integer_ply_is_rejected(tmp_path: Path) -> None:
    body = _valid_body(
        "A00\tBad\t1. Nh3\tg1h3\trnbqkbnr/pppppppp/8/8/8/7N/PPPPPPPP/RNBQKB1R b KQkq -\tmany"
    )

    with pytest.raises(OpeningDatasetError, match="non-integer ply"):
        load_index_from(*_write(tmp_path, body))


def test_identical_duplicate_rows_are_deduplicated(tmp_path: Path) -> None:
    index = load_index_from(*_write(tmp_path, _valid_body(VALID_ROW, VALID_ROW)))

    assert len(index) == 1


def test_conflicting_metadata_for_same_epd_is_rejected(tmp_path: Path) -> None:
    conflicting = (
        "B01\tDifferent Opening\t1. Nh3\tg1h3\t"
        "rnbqkbnr/pppppppp/8/8/8/7N/PPPPPPPP/RNBQKB1R b KQkq -\t1"
    )
    body = _valid_body(VALID_ROW, conflicting)

    with pytest.raises(OpeningDatasetError, match="conflicting metadata"):
        load_index_from(*_write(tmp_path, body))


def test_missing_index_file_is_fatal(tmp_path: Path) -> None:
    _, manifest_path = _write(tmp_path, _valid_body(VALID_ROW))

    with pytest.raises(OpeningDatasetError, match="not found"):
        load_index_from(tmp_path / "does_not_exist.tsv", manifest_path)


def test_manifest_without_source_commit_is_fatal(tmp_path: Path) -> None:
    paths = _write(tmp_path, _valid_body(VALID_ROW), manifest={"source": "somewhere"})

    with pytest.raises(OpeningDatasetError, match="source_commit"):
        load_index_from(*paths)


def test_empty_index_is_fatal(tmp_path: Path) -> None:
    body = "\t".join(EXPECTED_COLUMNS) + "\n"

    with pytest.raises(OpeningDatasetError, match="no records"):
        load_index_from(*_write(tmp_path, body))


def test_real_vendored_index_loads_and_is_cached() -> None:
    first = load_opening_index()
    second = load_opening_index()

    # Same object: loaded once per process, not once per call.
    assert first is second
    assert len(first) == 3807
    assert first.dataset_version == "51b886249b9e418498d25b6e39b926c3de99c29a"


def test_real_index_row_count_matches_manifest() -> None:
    from app.services.opening_dataset import MANIFEST_PATH

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert len(load_opening_index()) == manifest["index_rows"]
