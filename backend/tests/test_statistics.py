from app.services.pgn_parser import ParsedGame
from app.services.statistics import classify_game, compute_stats

PLAYER = "NeilSamant"


def make_game(
    white: str,
    black: str,
    result: str,
    opening: str | None = None,
    eco: str | None = None,
) -> ParsedGame:
    return ParsedGame(
        white=white,
        black=black,
        white_elo=None,
        black_elo=None,
        date=None,
        event=None,
        site=None,
        round=None,
        result=result,
        eco=eco,
        opening=opening,
        moves=[],
        raw_pgn="",
    )


def test_classify_win_as_white() -> None:
    game = make_game(PLAYER, "Opponent", "1-0")
    classification = classify_game(game, PLAYER)
    assert classification.color == "white"
    assert classification.result == "win"


def test_classify_win_as_black() -> None:
    game = make_game("Opponent", PLAYER, "0-1")
    classification = classify_game(game, PLAYER)
    assert classification.color == "black"
    assert classification.result == "win"


def test_classify_loss_as_white() -> None:
    game = make_game(PLAYER, "Opponent", "0-1")
    classification = classify_game(game, PLAYER)
    assert classification.color == "white"
    assert classification.result == "loss"


def test_classify_loss_as_black() -> None:
    game = make_game("Opponent", PLAYER, "1-0")
    classification = classify_game(game, PLAYER)
    assert classification.color == "black"
    assert classification.result == "loss"


def test_classify_draw() -> None:
    game = make_game(PLAYER, "Opponent", "1/2-1/2")
    classification = classify_game(game, PLAYER)
    assert classification.result == "draw"


def test_classify_unknown_player_is_unknown() -> None:
    game = make_game("PlayerX", "PlayerY", "1-0")
    classification = classify_game(game, PLAYER)
    assert classification.color == "unknown"
    assert classification.result == "unknown"


def test_classify_is_case_and_whitespace_insensitive() -> None:
    game = make_game("  neilsamant ", "Opponent", "1-0")
    classification = classify_game(game, PLAYER)
    assert classification.color == "white"


def test_compute_stats_aggregates_win_draw_loss_and_percentages() -> None:
    games = [
        make_game(PLAYER, "OpponentA", "1-0"),  # win as white
        make_game("OpponentB", PLAYER, "0-1"),  # win as black
        make_game(PLAYER, "OpponentC", "1/2-1/2"),  # draw as white
        make_game(PLAYER, "OpponentD", "0-1"),  # loss as white
    ]

    _, stats = compute_stats(games, PLAYER)

    assert stats.total_games == 4
    assert stats.wins == 2
    assert stats.draws == 1
    assert stats.losses == 1
    assert stats.unknown_results == 0
    assert stats.win_percentage == 50.0
    assert stats.score_percentage == 62.5
    assert stats.games_as_white == 3
    assert stats.games_as_black == 1


def test_compute_stats_with_no_known_games_avoids_division_by_zero() -> None:
    games = [make_game("PlayerX", "PlayerY", "1-0")]

    _, stats = compute_stats(games, PLAYER)

    assert stats.unknown_results == 1
    assert stats.win_percentage == 0.0
    assert stats.score_percentage == 0.0


def test_compute_stats_aggregates_openings() -> None:
    games = [
        make_game(PLAYER, "OpponentA", "1-0", opening="Sicilian Defense"),
        make_game(PLAYER, "OpponentB", "0-1", opening="Sicilian Defense"),
        make_game(PLAYER, "OpponentC", "1-0", opening="Italian Game"),
    ]

    _, stats = compute_stats(games, PLAYER)

    assert len(stats.openings) == 2
    sicilian = next(o for o in stats.openings if o.name == "Sicilian Defense")
    assert sicilian.games == 2
    assert sicilian.wins == 1
    assert sicilian.losses == 1
    assert sicilian.score_percentage == 50.0
    # Sorted by games descending, so the 2-game opening comes first.
    assert stats.openings[0].name == "Sicilian Defense"
