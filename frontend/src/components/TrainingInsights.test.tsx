import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TrainingInsights } from "./TrainingInsights";
import type {
  MoveQualityMetrics,
  ReviewMove,
  TrainingRecommendation,
  UploadCoverage,
  UploadMoveQuality,
} from "../types/uploadMoveQuality";

const UPLOAD_ID = "aaaaaaaa-0000-4000-8000-000000000000";
const GAME_ID = "bbbbbbbb-0000-4000-8000-000000000001";

function metrics(overrides: Partial<MoveQualityMetrics> = {}): MoveQualityMetrics {
  return {
    games: 40,
    total_player_moves: 1600,
    graded_moves: 1587,
    numeric_cpl_moves: 1580,
    unscored_moves: 13,
    coverage_percent: 99,
    average_centipawn_loss: 22,
    median_centipawn_loss: 5,
    exact_best_move_count: 800,
    exact_best_move_comparable: 1587,
    exact_best_move_percent: 53,
    inaccuracies: 90,
    mistakes: 40,
    blunders: 12,
    missed_mates: 0,
    allowed_mates: 2,
    mate_sequences: 13,
    critical_mistakes: 14,
    ...overrides,
  };
}

/** The real shape of the developer's library: 40 of 79 analysed. */
function coverage(overrides: Partial<UploadCoverage> = {}): UploadCoverage {
  return {
    total_games: 79,
    complete_games: 40,
    queued_games: 0,
    running_games: 0,
    failed_games: 0,
    cancelled_games: 0,
    not_analyzed_games: 39,
    not_gradable_games: 0,
    graded_player_moves: 1587,
    numeric_cpl_moves: 1580,
    ...overrides,
  };
}

function reviewMove(overrides: Partial<ReviewMove> = {}): ReviewMove {
  return {
    game_id: GAME_ID,
    game_index: 10,
    ply: 122,
    move_number: 61,
    side: "white",
    san: "f3",
    player_color: "white",
    opening: "Queen's Gambit Declined",
    eco: "D30",
    best_move_uci: "g1f3",
    score_before: { kind: "cp", cp: 250, mate_distance: null },
    score_after: { kind: "cp", cp: -305, mate_distance: null },
    centipawn_loss: 555,
    quality: "blunder",
    special_classification: null,
    ...overrides,
  };
}

function recommendation(
  overrides: Partial<TrainingRecommendation> = {},
): TrainingRecommendation {
  return {
    type: "review_mistakes",
    priority: 1,
    title: "Review your highest-impact positions",
    explanation: "These are the moves after which the assessment fell furthest.",
    evidence_summary: "Your 5 costliest moves.",
    evidence_items: [reviewMove()],
    coverage_note: "Based on 40 of 79 games with completed analysis.",
    ...overrides,
  };
}

function insights(overrides: Partial<UploadMoveQuality> = {}): UploadMoveQuality {
  return {
    upload_id: UPLOAD_ID,
    move_quality_version: 1,
    coverage: coverage(),
    overall: metrics(),
    by_color: [
      { color: "white", metrics: metrics({ games: 22, graded_moves: 900 }) },
      { color: "black", metrics: metrics({ games: 18, graded_moves: 687 }) },
    ],
    by_opening: [
      {
        opening: "Sicilian Defense",
        eco: "B90",
        metrics: metrics({ games: 5, graded_moves: 220 }),
      },
    ],
    top_review_moves: [reviewMove()],
    recommendations: [recommendation()],
    ...overrides,
  };
}

describe("coverage", () => {
  it("shows the numerator and the denominator together", async () => {
    render(<TrainingInsights insights={insights()} />);

    expect(screen.getByText("Based on 40 of 79 games")).toBeInTheDocument();
    expect(screen.getByText(/1587 of your moves graded/)).toBeInTheDocument();
    expect(screen.getByText(/39 games not analysed yet/)).toBeInTheDocument();
  });

  it("never presents the library size as the analysed size", () => {
    // The regression this whole coverage block exists to prevent: metrics
    // computed over 40 games rendered as though they described 79.
    render(<TrainingInsights insights={insights()} />);

    const headline = screen.getByText("Based on 40 of 79 games");

    expect(headline).toBeInTheDocument();
    expect(screen.queryByText("Based on 79 of 79 games")).not.toBeInTheDocument();
  });

  it("counts games still being analysed separately from ones never started", () => {
    render(
      <TrainingInsights
        insights={insights({
          coverage: coverage({
            complete_games: 10,
            queued_games: 5,
            running_games: 2,
            not_analyzed_games: 62,
          }),
        })}
      />,
    );

    expect(screen.getByText(/7 games still being analysed/)).toBeInTheDocument();
    expect(screen.getByText(/62 games not analysed yet/)).toBeInTheDocument();
  });

  it("declares games that cannot be graded at all", () => {
    render(
      <TrainingInsights
        insights={insights({
          coverage: coverage({ not_gradable_games: 3, not_analyzed_games: 36 }),
        })}
      />,
    );

    expect(screen.getByText(/3 games were saved under a name/)).toBeInTheDocument();
  });
});

describe("states with no figures", () => {
  it("distinguishes a library with no analysis from a failed request", () => {
    const { rerender } = render(
      <TrainingInsights
        insights={insights({
          coverage: coverage({ complete_games: 0, not_analyzed_games: 79 }),
          overall: metrics({ games: 0 }),
          by_color: [],
          by_opening: [],
          top_review_moves: [],
          recommendations: [],
        })}
      />,
    );

    expect(screen.getByText(/Nothing has been analysed yet/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Still honest about the library size while it has nothing to report.
    expect(screen.getByText("Based on 0 of 79 games")).toBeInTheDocument();

    rerender(<TrainingInsights insights={null} error="Could not load your training insights." />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not load");
    expect(screen.queryByText(/Nothing has been analysed yet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Based on/)).not.toBeInTheDocument();
  });

  it("shows no metrics at all before the request finishes", () => {
    render(<TrainingInsights insights={null} isLoading />);

    expect(screen.getByRole("status")).toHaveTextContent("Working out your training insights");
    expect(screen.queryByText("Average centipawn loss")).not.toBeInTheDocument();
  });

  it("says so plainly when nothing crossed the mistake threshold", () => {
    render(
      <TrainingInsights insights={insights({ top_review_moves: [], recommendations: [] })} />,
    );

    expect(screen.getByText(/No move in your analysed games crossed/)).toBeInTheDocument();
    expect(screen.getByText(/not yet enough analysed data/)).toBeInTheDocument();
  });
});

describe("the tables", () => {
  it("puts a sample size beside every figure", () => {
    render(
      <TrainingInsights
        insights={insights({
          by_color: [
            {
              color: "white",
              metrics: metrics({
                games: 22,
                graded_moves: 900,
                average_centipawn_loss: 19,
                critical_mistakes: 8,
              }),
            },
            {
              color: "black",
              metrics: metrics({
                games: 18,
                graded_moves: 687,
                average_centipawn_loss: 26,
                critical_mistakes: 6,
              }),
            },
          ],
        })}
      />,
    );

    const byColor = within(screen.getByRole("table", { name: "By colour" }));
    const cells = (name: RegExp) =>
      within(byColor.getByRole("row", { name })).
        getAllByRole("cell").
        map((cell) => cell.textContent);

    // Games and graded moves sit in the row, not behind a tooltip: an average
    // over 900 moves and one over 40 are not the same claim.
    expect(cells(/^White/)).toEqual(["White", "22", "900", "19", "8"]);
    expect(cells(/^Black/)).toEqual(["Black", "18", "687", "26", "6"]);
  });

  it("puts every heading in one row, above the values it names", () => {
    // A nested <tr> is repaired by the browser rather than rejected, so the
    // only symptom is columns one place out of line - which no assertion
    // about the body rows can see.
    render(<TrainingInsights insights={insights()} />);

    for (const [name, first] of [
      ["By colour", "Colour"],
      ["By opening", "Opening"],
    ] as const) {
      const table = within(screen.getByRole("table", { name }));

      expect(table.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
        first,
        "Games",
        "Graded moves",
        "Average loss",
        "Critical",
      ]);
      // One header row plus one row per entry, and nothing extra.
      expect(table.getAllByRole("row")).toHaveLength(
        1 + (name === "By colour" ? 2 : 1),
      );
    }
  });

  it("labels the unclassified opening bucket rather than naming it", () => {
    render(
      <TrainingInsights
        insights={insights({
          by_opening: [
            { opening: null, eco: null, metrics: metrics({ games: 3, graded_moves: 120 }) },
          ],
        })}
      />,
    );

    expect(screen.getByText("Not classified")).toBeInTheDocument();
  });

  it("reports an absent average as a gap rather than a zero", () => {
    render(
      <TrainingInsights
        insights={insights({
          overall: metrics({ average_centipawn_loss: null, exact_best_move_percent: null }),
          by_color: [],
          by_opening: [],
        })}
      />,
    );

    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });
});

describe("recommendations", () => {
  it("shows the claim, the evidence, and the sample behind it", () => {
    render(<TrainingInsights insights={insights()} />);

    expect(
      screen.getByRole("heading", { name: "Review your highest-impact positions" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Your 5 costliest moves.")).toBeInTheDocument();
    expect(
      screen.getByText("Based on 40 of 79 games with completed analysis."),
    ).toBeInTheDocument();
  });

  it("names each evidence position with its cost", () => {
    render(<TrainingInsights insights={insights()} />);

    const evidence = within(
      screen.getByRole("list", { name: /^Evidence for/ }),
    );

    expect(
      evidence.getByText("Game 11, 61. f3, Blunder, lost 5.55 pawns"),
    ).toBeInTheDocument();
  });

  it("never prices a mate error in pawns", () => {
    render(
      <TrainingInsights
        insights={insights({
          recommendations: [
            recommendation({
              evidence_items: [
                reviewMove({
                  centipawn_loss: null,
                  quality: "critical",
                  special_classification: "allowed_mate",
                }),
              ],
            }),
          ],
          top_review_moves: [],
        })}
      />,
    );

    const evidence = within(screen.getByRole("list", { name: /^Evidence for/ }));
    const item = evidence.getByRole("listitem");

    expect(item.textContent).toContain("Allowed mate");
    expect(item.textContent).not.toMatch(/pawns/);
  });

  it("keeps its coverage note on every card, not once per page", () => {
    render(
      <TrainingInsights
        insights={insights({
          recommendations: [
            recommendation(),
            recommendation({
              type: "opening_priority",
              priority: 2,
              title: "Spend your next session on the Sicilian Defense",
            }),
          ],
        })}
      />,
    );

    const cards = screen.getAllByRole("listitem");
    const notes = screen.getAllByText("Based on 40 of 79 games with completed analysis.");

    expect(cards.length).toBeGreaterThanOrEqual(2);
    expect(notes).toHaveLength(2);
  });
});

describe("opening a position", () => {
  it("hands back the game and ply that were clicked", async () => {
    const user = userEvent.setup();
    const onOpenPosition = vi.fn();

    render(<TrainingInsights insights={insights()} onOpenPosition={onOpenPosition} />);

    const positions = within(screen.getByRole("list", { name: "Positions to review" }));
    await user.click(positions.getByRole("button", { name: /61\. f3/ }));

    expect(onOpenPosition).toHaveBeenCalledWith(GAME_ID, 122);
  });

  it("renders positions as plain text when there is nowhere to open them", () => {
    render(<TrainingInsights insights={insights()} />);

    const positions = within(screen.getByRole("list", { name: "Positions to review" }));

    expect(positions.getByRole("listitem").textContent).toContain("Game 11, 61. f3");
    expect(positions.queryByRole("button")).not.toBeInTheDocument();
  });
});
