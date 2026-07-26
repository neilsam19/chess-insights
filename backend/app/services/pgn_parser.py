import io
from dataclasses import dataclass, field

import chess.pgn

from app.services.opening_classifier import OpeningClassification, classify_opening
from app.services.opening_dataset import OpeningIndex, load_opening_index


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
    site: str | None
    round: str | None
    result: str
    # eco/opening stay exactly as the PGN declared them, however wrong - they
    # are the fallback and the debugging record. Canonical values live on
    # `classification`.
    eco: str | None
    opening: str | None
    moves: list[ParsedMove]
    raw_pgn: str
    classification: OpeningClassification | None = field(default=None)


@dataclass
class ParseResult:
    games: list[ParsedGame]
    warnings: list[str]


def parse_pgn_text(pgn_text: str, *, index: OpeningIndex | None = None) -> ParseResult:
    """Parse a PGN and classify every game's opening from its positions.

    `index` is injectable for tests; in normal use the vendored index is
    loaded once per process and shared across every game in the file.
    """
    games: list[ParsedGame] = []
    warnings: list[str] = []

    # Resolved once so every game in the file classifies against the same
    # snapshot, rather than re-resolving per game.
    index = index if index is not None else load_opening_index()

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

        # A recovered game classifies from whatever prefix survived, which is
        # why this runs after the truncation warning rather than instead of it.
        parsed.classification = classify_opening(
            [move.fen_after for move in parsed.moves],
            eco_header=parsed.eco,
            opening_header=parsed.opening,
            index=index,
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

    raw_pgn = game.accept(chess.pgn.StringExporter(headers=True, variations=True, comments=True))

    return ParsedGame(
        white=headers.get("White", "Unknown"),
        black=headers.get("Black", "Unknown"),
        white_elo=_parse_optional_int(headers.get("WhiteElo")),
        black_elo=_parse_optional_int(headers.get("BlackElo")),
        date=_clean_placeholder(headers.get("Date")),
        event=_clean_placeholder(headers.get("Event")),
        site=_clean_placeholder(headers.get("Site")),
        round=_clean_placeholder(headers.get("Round")),
        result=headers.get("Result", "*"),
        eco=headers.get("ECO"),
        opening=headers.get("Opening"),
        moves=moves,
        raw_pgn=raw_pgn,
    )
