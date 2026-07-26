import socket

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.game import Game
from app.models.move import Move
from app.models.upload import Upload
from app.services.opening_dataset import load_opening_index
from app.services.pgn_parser import parse_pgn_text

from sample_data import SAMPLE_PGN_BYTES, SAMPLE_PGN_TEXT

# A game whose PGN headers are simply absent - classification must not
# depend on them.
NO_HEADERS = """[Event "Casual Game"]
[White "NeilSamant"]
[Black "OpponentA"]
[Result "*"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 *
"""

# Deliberately wrong Opening/ECO headers on a Najdorf.
WRONG_HEADERS = """[Event "Casual Game"]
[White "NeilSamant"]
[Black "OpponentA"]
[Result "*"]
[ECO "A00"]
[Opening "Grob Opening"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 *
"""


def _preview(client: TestClient, pgn: bytes, player_name: str = "NeilSamant") -> dict:
    response = client.post(
        "/api/uploads/preview",
        files={"file": ("games.pgn", pgn, "application/x-chess-pgn")},
        data={"player_name": player_name},
    )
    assert response.status_code == 200
    return response.json()


def test_parser_classifies_every_game() -> None:
    result = parse_pgn_text(SAMPLE_PGN_TEXT)

    assert len(result.games) == 3
    for game in result.games:
        assert game.classification is not None
        assert game.classification.source == "lichess"
        assert game.classification.name
        assert game.classification.matched_ply >= 1


def test_parser_retains_raw_headers_alongside_classification() -> None:
    result = parse_pgn_text(SAMPLE_PGN_TEXT)

    qgd = result.games[1]
    # Raw header preserved for debugging...
    assert qgd.eco == "D30"
    # ...while the canonical classification is more precise.
    assert qgd.classification.eco == "D53"
    assert qgd.classification.name == "Queen's Gambit Declined"


def test_classification_works_without_any_opening_headers() -> None:
    result = parse_pgn_text(NO_HEADERS)

    game = result.games[0]
    assert game.eco is None
    assert game.opening is None
    assert game.classification.source == "lichess"
    assert game.classification.name == "Sicilian Defense: Najdorf Variation"
    assert game.classification.eco == "B90"


def test_wrong_headers_are_overridden_by_position() -> None:
    result = parse_pgn_text(WRONG_HEADERS)

    game = result.games[0]
    assert game.eco == "A00"  # raw header, wrong
    assert game.opening == "Grob Opening"
    assert game.classification.source == "lichess"
    assert game.classification.eco == "B90"
    assert game.classification.name == "Sicilian Defense: Najdorf Variation"


def test_truncated_malformed_game_is_classified_from_its_prefix() -> None:
    # Nf7 is parseable but illegal, so the parser truncates there and warns.
    malformed = """[Event "Bad"]
[White "NeilSamant"]
[Black "OpponentA"]
[Result "*"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf7 5. Nc3 a6 *
"""
    result = parse_pgn_text(malformed)

    assert len(result.warnings) == 1
    game = result.games[0]
    assert len(game.moves) == 7  # truncated at the illegal move
    assert game.classification.source == "lichess"
    assert game.classification.matched_ply == 6
    assert game.classification.name == "Sicilian Defense"


def test_preview_returns_canonical_opening_fields(client: TestClient) -> None:
    body = _preview(client, SAMPLE_PGN_BYTES)

    first = body["games"][0]
    assert first["opening"] == "Bishop's Opening"
    assert first["eco"] == "C23"
    assert first["opening_source"] == "lichess"
    assert first["opening_matched_ply"] == 3

    # Header said A00 "Barnes Opening"; the position is more specific.
    third = body["games"][2]
    assert third["opening"] == "Barnes Opening: Fool's Mate"
    assert third["opening_source"] == "lichess"


def test_preview_overrides_wrong_headers(client: TestClient) -> None:
    body = _preview(client, WRONG_HEADERS.encode("utf-8"))

    game = body["games"][0]
    assert game["eco"] == "B90"
    assert game["opening"] == "Sicilian Defense: Najdorf Variation"
    assert game["opening_source"] == "lichess"


def test_preview_opening_stats_use_canonical_names(client: TestClient) -> None:
    body = _preview(client, SAMPLE_PGN_BYTES)

    names = {opening["name"] for opening in body["stats"]["openings"]}
    assert "Barnes Opening: Fool's Mate" in names
    # The bare header name is no longer what the table groups under.
    assert "Barnes Opening" not in names


def test_preview_writes_zero_database_rows(client: TestClient, db_session) -> None:
    """Closes the Sprint 2 backlog gap: preview statelessness had no
    automated regression test, only a manual check."""

    def counts() -> tuple[int, int, int]:
        return (
            db_session.scalar(select(func.count()).select_from(Upload)),
            db_session.scalar(select(func.count()).select_from(Game)),
            db_session.scalar(select(func.count()).select_from(Move)),
        )

    before = counts()
    for _ in range(3):
        _preview(client, SAMPLE_PGN_BYTES)
    assert counts() == before


def test_classification_makes_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dataset loading and classification must be entirely local - the cache
    is cleared first so even the initial load is covered."""

    def _blocked(*args, **kwargs):
        raise AssertionError("classification attempted network access")

    load_opening_index.cache_clear()
    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    result = parse_pgn_text(SAMPLE_PGN_TEXT)

    assert len(result.games) == 3
    assert all(game.classification.source == "lichess" for game in result.games)
