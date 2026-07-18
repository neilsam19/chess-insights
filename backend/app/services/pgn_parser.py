import io
from dataclasses import dataclass

import chess.pgn


@dataclass
class ParsedMove:
    ply: int
    san: str
    uci: str
    fen_before: str
    fen_after: str


@dataclass
class ParsedGame:
    white: str
    black: str
    white_elo: int | None
    black_elo: int | None
    date: str | None
    event: str | None
    result: str
    eco: str | None
    opening: str | None
    moves: list[ParsedMove]


@dataclass
class ParseResult:
    games: list[ParsedGame]
    warnings: list[str]


def parse_pgn_text(pgn_text: str) -> ParseResult:
    games: list[ParsedGame] = []
    warnings: list[str] = []

    stream = io.StringIO(pgn_text)
    game_index = 0

    while True:
        game = chess.pgn.read_game(stream)

        if game is None:
            break

        game_index += 1

        try:
            parsed = _convert_game(game)
        except Exception as exc:  # noqa: BLE001 - any conversion failure is recoverable
            warnings.append(f"Game {game_index}: skipped ({exc})")
            continue

        if not parsed.moves and parsed.white == "?" and parsed.black == "?":
            # python-chess returns an empty placeholder Game for input with no
            # recognizable PGN structure at all - not a real game to report.
            continue

        if game.errors:
            warnings.append(
                f"Game {game_index}: recovered with {len(game.errors)} "
                f"move error(s), truncated at the first bad move"
            )

        games.append(parsed)

    return ParseResult(games=games, warnings=warnings)


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _clean_placeholder(value: str | None) -> str | None:
    """python-chess fills unset Seven Tag Roster headers (e.g. Date) with
    placeholders like '????.??.??' rather than omitting them."""
    if value is None:
        return None
    if not value.strip("?."):
        return None
    return value


def _convert_game(game: chess.pgn.Game) -> ParsedGame:
    headers = game.headers
    board = game.board()
    moves: list[ParsedMove] = []

    for ply, move in enumerate(game.mainline_moves(), start=1):
        fen_before = board.fen()
        san = board.san(move)
        uci = move.uci()
        board.push(move)
        fen_after = board.fen()
        moves.append(
            ParsedMove(
                ply=ply,
                san=san,
                uci=uci,
                fen_before=fen_before,
                fen_after=fen_after,
            )
        )

    return ParsedGame(
        white=headers.get("White", "Unknown"),
        black=headers.get("Black", "Unknown"),
        white_elo=_parse_optional_int(headers.get("WhiteElo")),
        black_elo=_parse_optional_int(headers.get("BlackElo")),
        date=_clean_placeholder(headers.get("Date")),
        event=_clean_placeholder(headers.get("Event")),
        result=headers.get("Result", "*"),
        eco=headers.get("ECO"),
        opening=headers.get("Opening"),
        moves=moves,
    )
