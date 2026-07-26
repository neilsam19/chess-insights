from types import MappingProxyType

import chess

from app.services.epd import position_epd
from app.services.opening_classifier import (
    SOURCE_LICHESS,
    SOURCE_PGN_HEADER,
    SOURCE_UNKNOWN,
    classify_opening,
)
from app.services.opening_dataset import OpeningIndex

# Every one of the 20 legal first moves is a named opening in the real
# dataset, so a game with any moves at all always matches something. The
# fallback paths are therefore exercised against an empty injected index
# rather than by hunting for an unnamed real position.
EMPTY_INDEX = OpeningIndex(by_epd=MappingProxyType({}), dataset_version="test-v1", max_ply=36)

NAJDORF = ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"]
NAJDORF_TRANSPOSED = ["Nf3", "c5", "e4", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"]
NIMZO = ["d4", "Nf6", "c4", "e6", "Nc3", "Bb4"]
NIMZO_TRANSPOSED = ["c4", "e6", "Nc3", "Nf6", "d4", "Bb4"]


def positions(sans: list[str]) -> list[str]:
    """The FEN after each ply, exactly as pgn_parser stores fen_after."""
    board = chess.Board()
    out = []
    for san in sans:
        board.push_san(san)
        out.append(board.fen())
    return out


def test_classifies_game_with_no_headers() -> None:
    result = classify_opening(positions(NAJDORF))

    assert result.source == SOURCE_LICHESS
    assert result.eco == "B90"
    assert result.name == "Sicilian Defense: Najdorf Variation"
    assert result.matched_ply == 10
    assert result.dataset_version


def test_classifies_game_with_only_an_eco_header() -> None:
    result = classify_opening(positions(NAJDORF), eco_header="B90")

    # The dataset still supplies the full name the header lacked.
    assert result.source == SOURCE_LICHESS
    assert result.name == "Sicilian Defense: Najdorf Variation"


def test_incorrect_pgn_header_is_overridden_by_position_match() -> None:
    result = classify_opening(
        positions(NAJDORF),
        eco_header="A00",
        opening_header="Grob Opening",  # flatly wrong
    )

    assert result.source == SOURCE_LICHESS
    assert result.eco == "B90"
    assert result.name == "Sicilian Defense: Najdorf Variation"


def test_selects_deepest_opening_not_a_parent() -> None:
    deep = classify_opening(positions(NAJDORF))
    shallow = classify_opening(positions(NAJDORF[:6]))

    assert deep.name == "Sicilian Defense: Najdorf Variation"
    assert deep.matched_ply == 10
    # The same game truncated earlier resolves to a shallower opening.
    assert shallow.name != deep.name
    assert shallow.matched_ply < deep.matched_ply


def test_walks_back_past_plies_that_match_nothing() -> None:
    # Ply 9 of this line matches no dataset row, but ply 8 does. Stopping the
    # game at ply 9 must walk back rather than give up.
    result = classify_opening(positions(NAJDORF[:9]))

    assert result.source == SOURCE_LICHESS
    assert result.matched_ply == 8
    assert result.name == "Sicilian Defense: Modern Variations, Main Line"


def test_a_deeper_named_position_wins_over_a_shallower_one() -> None:
    # 6. Be3 is itself a named opening, deeper than the plain Najdorf.
    deeper = classify_opening(positions(NAJDORF + ["Be3"]))

    assert deeper.matched_ply == 11
    assert deeper.name == "Sicilian Defense: Najdorf Variation, English Attack"


def test_recognizes_transposition_by_position() -> None:
    direct = classify_opening(positions(NAJDORF))
    transposed = classify_opening(positions(NAJDORF_TRANSPOSED))

    assert transposed.name == direct.name
    assert transposed.source == SOURCE_LICHESS

    nimzo = classify_opening(positions(NIMZO))
    nimzo_t = classify_opening(positions(NIMZO_TRANSPOSED))
    assert nimzo.name == nimzo_t.name == "Nimzo-Indian Defense"


def test_falls_back_to_pgn_header_when_no_position_matches() -> None:
    result = classify_opening(
        positions(NAJDORF),
        eco_header="Z99",
        opening_header="Totally Made Up Opening",
        index=EMPTY_INDEX,
    )

    assert result.source == SOURCE_PGN_HEADER
    assert result.eco == "Z99"
    assert result.name == "Totally Made Up Opening"
    assert result.matched_ply is None


def test_unknown_when_neither_dataset_nor_headers_help() -> None:
    result = classify_opening(positions(NAJDORF), index=EMPTY_INDEX)

    assert result.source == SOURCE_UNKNOWN
    assert result.eco is None
    assert result.name is None
    assert result.matched_ply is None


def test_placeholder_headers_are_treated_as_absent() -> None:
    result = classify_opening(
        positions(NAJDORF), eco_header="?", opening_header="  ", index=EMPTY_INDEX
    )

    assert result.source == SOURCE_UNKNOWN


def test_dataset_version_is_recorded_even_without_a_match() -> None:
    # Lets a later backfill tell "unknown under the old snapshot" apart from
    # "unknown under the current one".
    result = classify_opening(positions(NAJDORF), index=EMPTY_INDEX)

    assert result.dataset_version == "test-v1"


def test_zero_move_game() -> None:
    assert classify_opening([]).source == SOURCE_UNKNOWN

    with_header = classify_opening([], opening_header="Sicilian Defense")
    assert with_header.source == SOURCE_PGN_HEADER
    assert with_header.name == "Sicilian Defense"


def test_truncated_malformed_game_uses_its_valid_prefix() -> None:
    # Mirrors pgn_parser's recovery behaviour: the game stops early, but the
    # retained prefix still classifies.
    result = classify_opening(positions(NAJDORF[:8]))

    assert result.source == SOURCE_LICHESS
    assert result.matched_ply == 8


def test_unparseable_fen_does_not_raise() -> None:
    good = positions(NAJDORF)
    corrupted = good[:-1] + ["this is not a fen"]

    result = classify_opening(corrupted)

    # Skips the corrupted deepest position and keeps walking back, so one bad
    # position degrades the result rather than failing the import.
    assert result.source == SOURCE_LICHESS
    assert result.matched_ply == 8
    assert result.name == "Sicilian Defense: Modern Variations, Main Line"


def test_castling_rights_are_part_of_the_position() -> None:
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bb5"]:
        board.push_san(san)
    with_rights = position_epd(board.fen())

    # Same piece placement, castling rights removed - must normalize
    # differently, or positions with lost rights would false-match.
    stripped = board.fen().replace(" KQkq ", " - ")
    without_rights = position_epd(stripped)

    assert with_rights != without_rights
    assert with_rights.endswith("KQkq -")
    assert without_rights.endswith("- -")


def test_en_passant_is_normalized_to_legal_availability() -> None:
    # Legal en passant available -> the square is part of the position.
    legal = chess.Board()
    for san in ["e4", "Nf6", "e5", "d5"]:
        legal.push_san(san)
    assert position_epd(legal.fen()).endswith(" d6")

    # Pawn double-move with no capture available -> no en-passant square,
    # even when the FEN was written by a tool that always emits one.
    none_available = chess.Board()
    for san in ["Nh3", "d5", "g3", "e5", "f4"]:
        none_available.push_san(san)

    always_emits_ep = none_available.fen(en_passant="fen")
    assert always_emits_ep.split()[3] == "f3"  # the other FEN convention

    normalized = position_epd(always_emits_ep)
    assert normalized.endswith(" -")

    # And that normalization is what makes it match the dataset: naively
    # truncating the same FEN to four fields does not.
    naive = " ".join(always_emits_ep.split()[:4])
    result = classify_opening([always_emits_ep])
    assert result.source == SOURCE_LICHESS
    assert result.name == "Amar Opening: Paris Gambit"
    assert naive != normalized


def test_does_not_mutate_its_input() -> None:
    original = positions(NAJDORF)
    snapshot = list(original)

    classify_opening(original)

    assert original == snapshot
    assert len(original) == len(snapshot)
