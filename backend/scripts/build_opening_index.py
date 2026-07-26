"""Generate the committed opening index from the vendored Lichess TSVs.

Run from `backend/`:

    ./.venv/Scripts/python.exe scripts/build_opening_index.py

Reads app/data/openings/{a,b,c,d,e}.tsv (eco, name, pgn - verbatim from
lichess-org/chess-openings) and writes openings_index.tsv with a derived
`epd` and `uci` column, plus MANIFEST.json recording provenance.

The EPD is produced by app.services.epd.board_epd - the same function the
classifier uses on game positions - so dataset and game representations
cannot diverge. Regenerating must be byte-identical for a given snapshot.
"""

import argparse
import io
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import chess
import chess.pgn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.epd import board_epd  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "openings"
VOLUMES = ["a", "b", "c", "d", "e"]
SOURCE_URL = "https://github.com/lichess-org/chess-openings"
INDEX_COLUMNS = ["eco", "name", "pgn", "uci", "epd", "ply"]


class DatasetError(Exception):
    pass


def _replay(pgn_movetext: str) -> tuple[chess.Board, list[str]]:
    """Replay a dataset row's movetext, returning the final board and UCI moves."""
    game = chess.pgn.read_game(io.StringIO(pgn_movetext))
    if game is None:
        raise DatasetError(f"unparseable movetext: {pgn_movetext!r}")

    board = game.board()
    uci_moves = []
    for move in game.mainline_moves():
        if not board.is_legal(move):
            raise DatasetError(f"illegal move in {pgn_movetext!r}")
        uci_moves.append(move.uci())
        board.push(move)

    if not uci_moves:
        raise DatasetError(f"no moves in {pgn_movetext!r}")

    return board, uci_moves


def _read_rows() -> list[dict[str, str]]:
    rows = []
    for volume in VOLUMES:
        path = DATA_DIR / f"{volume}.tsv"
        if not path.exists():
            raise DatasetError(f"missing vendored source file: {path}")

        lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        if header != ["eco", "name", "pgn"]:
            raise DatasetError(f"{path.name}: unexpected header {header}")

        for line_no, line in enumerate(lines[1:], start=2):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                raise DatasetError(f"{path.name}:{line_no}: expected 3 columns")
            rows.append({"eco": parts[0], "name": parts[1], "pgn": parts[2]})
    return rows


def build(write: bool, source_sha: str) -> int:
    rows = _read_rows()
    print(f"read {len(rows)} source rows from {len(VOLUMES)} volumes")

    by_epd: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        board, uci_moves = _replay(row["pgn"])
        entry = {
            "eco": row["eco"],
            "name": row["name"],
            "pgn": row["pgn"],
            "uci": " ".join(uci_moves),
            "epd": board_epd(board),
            "ply": len(uci_moves),
        }
        by_epd[entry["epd"]].append(entry)

    identical = 0
    conflicting: list[tuple[str, list[dict]]] = []
    for epd, entries in by_epd.items():
        if len(entries) == 1:
            continue
        distinct = {(e["eco"], e["name"]) for e in entries}
        if len(distinct) == 1:
            identical += len(entries) - 1
        else:
            conflicting.append((epd, entries))

    print(f"distinct EPDs           : {len(by_epd)}")
    print(f"identical duplicate rows: {identical}")
    print(f"conflicting EPDs        : {len(conflicting)}")
    for epd, entries in conflicting[:10]:
        print(f"  {epd}")
        for e in sorted(entries, key=lambda x: (x["ply"], x["eco"], x["name"])):
            print(f"    ply={e['ply']:>2}  {e['eco']}  {e['name']}  ({e['pgn']})")

    if not write:
        return len(conflicting)

    # Deterministic resolution: shortest line to the position wins, then ECO,
    # then name. Documented in MANIFEST.json so the choice is auditable.
    selected = [
        sorted(entries, key=lambda e: (e["ply"], e["eco"], e["name"]))[0]
        for entries in by_epd.values()
    ]
    selected.sort(key=lambda e: (e["eco"], e["name"], e["epd"]))

    index_path = DATA_DIR / "openings_index.tsv"
    with index_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(INDEX_COLUMNS) + "\n")
        for e in selected:
            fh.write("\t".join(str(e[c]) for c in INDEX_COLUMNS) + "\n")
    print(f"wrote {index_path} ({len(selected)} rows)")

    manifest = {
        "source": SOURCE_URL,
        "source_commit": source_sha,
        "license": "CC0-1.0",
        "retrieved": date.today().isoformat(),
        "source_rows": len(rows),
        "index_rows": len(selected),
        "identical_duplicate_rows": identical,
        "conflicting_epds": len(conflicting),
        "epd_generator": "app.services.epd.board_epd (python-chess Board.epd, en_passant=legal)",
        "collision_policy": "lowest ply, then eco, then name",
        "regenerate": "python scripts/build_opening_index.py --sha <source_commit>",
    }
    manifest_path = DATA_DIR / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return len(conflicting)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", default="", help="upstream commit SHA for the manifest")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    if not args.report_only and not args.sha:
        parser.error("--sha is required when writing the index")

    build(write=not args.report_only, source_sha=args.sha)


if __name__ == "__main__":
    main()
