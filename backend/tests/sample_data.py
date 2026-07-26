"""Shared sample PGN bytes for tests.

Duplicate detection is a *global* uniqueness constraint on
(file_sha256, normalized_player_name), so the transactional test fixture
cannot isolate tests from it: a legitimately saved copy of
sample-games.pgn already committed in the dev database would make every
test that imports the same file under the same player name get a 409
instead of a 201.

Appending a unique PGN escape line (`%` at the start of a line is ignored
by the PGN spec and by python-chess) gives each test session its own file
hash while parsing to byte-identical games.
"""

import uuid
from pathlib import Path

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[2] / "sample-data" / "sample-games.pgn"

_BASE_BYTES = SAMPLE_DATA_PATH.read_bytes()

SAMPLE_PGN_BYTES = _BASE_BYTES + f"\n% pytest-session {uuid.uuid4()}\n".encode("utf-8")
SAMPLE_PGN_TEXT = SAMPLE_PGN_BYTES.decode("utf-8")
