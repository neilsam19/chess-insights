"""Canonical position normalization, shared by the dataset generator and
the opening classifier.

This module is deliberately tiny and deliberately the *only* place a
position is turned into an EPD string. The vendored opening index is
generated with `board_epd`, and game positions are normalized at
classification time with `position_epd`, so the two representations
cannot drift apart.

The subtlety this exists to contain: our stored `fen_before`/`fen_after`
come from `chess.Board.fen()`, which always emits an en-passant square
after any pawn double-move. The Lichess dataset (and python-chess's
`Board.epd()` default, `en_passant="legal"`) only emits one when an
en-passant capture is actually available. String-slicing a FEN down to
four fields therefore produces an EPD that silently fails to match on
exactly those positions - so we round-trip through a real Board instead.
"""

import chess

# An EPD as used here is the first four FEN fields - board, side to move,
# castling rights, en-passant target - with halfmove/fullmove counters
# dropped, and en passant recorded only when legally available.
EPD_FIELD_COUNT = 4


def board_epd(board: chess.Board) -> str:
    """Canonical EPD for a live board."""
    return board.epd()


def position_epd(fen: str) -> str:
    """Canonical EPD for a stored FEN.

    Rebuilds a Board so en-passant legality is re-evaluated rather than
    copied verbatim out of the FEN text. Raises ValueError on a FEN that
    python-chess cannot parse.
    """
    return chess.Board(fen).epd()
