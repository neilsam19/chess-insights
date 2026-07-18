from pathlib import Path

from app.services.pgn_parser import parse_pgn_text

SAMPLE_DATA_PATH = Path(__file__).resolve().parents[2] / "sample-data" / "sample-games.pgn"

SCHOLARS_MATE = """[Event "Casual Game"]
[White "NeilSamant"]
[Black "OpponentA"]
[Result "1-0"]
[Date "2026.07.01"]
[ECO "C23"]
[Opening "Bishop's Opening"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

MISSING_HEADERS = """[Event "Casual Game"]
[White "PlayerA"]
[Black "PlayerB"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

PROMOTION_FIXTURE = """[Event "Test Position"]
[White "NeilSamant"]
[Black "OpponentD"]
[Result "*"]
[SetUp "1"]
[FEN "8/P6k/8/8/8/8/8/6K1 w - - 0 1"]

1. a8=Q *
"""

MALFORMED_THEN_VALID = """[Event "Bad Game"]
[White "PlayerX"]
[Black "PlayerY"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf7 4. Bxz9 1-0

[Event "Good Game"]
[White "NeilSamant"]
[Black "OpponentZ"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""


def test_parses_single_valid_game() -> None:
    result = parse_pgn_text(SCHOLARS_MATE)

    assert result.warnings == []
    assert len(result.games) == 1

    game = result.games[0]
    assert game.white == "NeilSamant"
    assert game.black == "OpponentA"
    assert game.result == "1-0"
    assert game.eco == "C23"
    assert game.opening == "Bishop's Opening"
    assert len(game.moves) == 7

    first_move = game.moves[0]
    assert first_move.ply == 1
    assert first_move.san == "e4"
    assert first_move.uci == "e2e4"
    assert first_move.fen_before == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    last_move = game.moves[-1]
    assert last_move.san == "Qxf7#"


def test_parses_multiple_games_in_one_file() -> None:
    pgn_text = SAMPLE_DATA_PATH.read_text(encoding="utf-8")

    result = parse_pgn_text(pgn_text)

    assert result.warnings == []
    assert len(result.games) == 3
    assert [g.result for g in result.games] == ["1-0", "1/2-1/2", "0-1"]


def test_missing_optional_headers_default_to_none() -> None:
    result = parse_pgn_text(MISSING_HEADERS)

    game = result.games[0]
    assert game.white_elo is None
    assert game.black_elo is None
    assert game.eco is None
    assert game.opening is None
    assert game.date is None
    assert game.event == "Casual Game"


def test_castling_is_parsed_as_san() -> None:
    pgn_text = SAMPLE_DATA_PATH.read_text(encoding="utf-8")
    result = parse_pgn_text(pgn_text)

    qgd_game = result.games[1]
    castling_move = qgd_game.moves[-1]
    assert castling_move.san == "O-O"


def test_promotion_is_parsed_correctly() -> None:
    result = parse_pgn_text(PROMOTION_FIXTURE)

    game = result.games[0]
    assert len(game.moves) == 1
    promotion_move = game.moves[0]
    assert promotion_move.san == "a8=Q"
    assert promotion_move.uci == "a7a8q"


def test_recovers_from_malformed_game_and_keeps_valid_ones() -> None:
    result = parse_pgn_text(MALFORMED_THEN_VALID)

    assert len(result.games) == 2
    assert len(result.warnings) == 1
    assert "Game 1" in result.warnings[0]

    bad_game, good_game = result.games
    assert len(bad_game.moves) == 5  # truncated at the illegal move
    assert len(good_game.moves) == 7
    assert good_game.white == "NeilSamant"


def test_empty_pgn_text_returns_no_games() -> None:
    result = parse_pgn_text("")

    assert result.games == []
    assert result.warnings == []
