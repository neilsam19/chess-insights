import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OpeningLabel } from "./OpeningLabel";
import { gameFromSummary } from "../utils/libraryAdapters";
import type { GameSummary } from "../types/library";
import type { Game } from "../types/upload";

describe("OpeningLabel", () => {
  it("renders a classified opening name with its ECO code", () => {
    render(<OpeningLabel name="Sicilian Defense: Najdorf Variation" eco="B90" />);

    expect(
      screen.getByText("Sicilian Defense: Najdorf Variation · B90"),
    ).toBeInTheDocument();
  });

  it("renders the fallback label for an unknown opening", () => {
    render(<OpeningLabel name={null} eco={null} />);

    const label = screen.getByText("Unknown opening");
    expect(label).toBeInTheDocument();
    expect(label).toHaveClass("opening-label-unknown");
  });

  it("does not render broken punctuation when the ECO code is missing", () => {
    render(<OpeningLabel name="Bishop's Opening" eco={null} />);

    const label = screen.getByText("Bishop's Opening");
    expect(label).toBeInTheDocument();
    expect(label.textContent).not.toContain("·");
    expect(label.textContent).not.toContain("null");
    expect(label.textContent).not.toContain("undefined");
  });

  it("never renders a null or undefined placeholder for any field combination", () => {
    const combinations: Array<[string | null, string | null]> = [
      ["Nimzo-Indian Defense", "E20"],
      ["Nimzo-Indian Defense", null],
      [null, "E20"],
      [null, null],
      ["B90", "B90"],
    ];

    for (const [name, eco] of combinations) {
      const { unmount, container } = render(<OpeningLabel name={name} eco={eco} />);
      const text = container.textContent ?? "";

      expect(text.trim()).not.toBe("");
      expect(text).not.toContain("null");
      expect(text).not.toContain("undefined");
      expect(text.trim().endsWith("·")).toBe(false);
      expect(text.trim().startsWith("·")).toBe(false);
      unmount();
    }
  });

  it("renders preview data and saved-analysis data identically", () => {
    // Preview shape, straight off POST /api/uploads/preview.
    const previewGame: Game = {
      index: 0,
      white: "NeilSamant",
      black: "OpponentA",
      white_elo: null,
      black_elo: null,
      date: null,
      event: null,
      result: "1-0",
      eco: "B90",
      opening: "Sicilian Defense: Najdorf Variation",
      opening_source: "lichess",
      opening_matched_ply: 10,
      player_color: "white",
      player_result: "win",
      moves: [],
    };

    // Saved shape, straight off GET /api/uploads/{id}/games.
    const savedSummary: GameSummary = {
      id: "1f1c2b3a-0000-4000-8000-000000000001",
      game_index: 0,
      white_name: "NeilSamant",
      black_name: "OpponentA",
      white_elo: null,
      black_elo: null,
      result: "1-0",
      user_color: "white",
      user_result: "win",
      eco: "B90",
      opening_name: "Sicilian Defense: Najdorf Variation",
      opening_source: "lichess",
      opening_matched_ply: 10,
      move_count: 10,
    };
    const savedGame = gameFromSummary(savedSummary);

    const preview = render(<OpeningLabel name={previewGame.opening} eco={previewGame.eco} />);
    const previewText = preview.container.textContent;
    preview.unmount();

    const saved = render(<OpeningLabel name={savedGame.opening} eco={savedGame.eco} />);
    const savedText = saved.container.textContent;

    expect(savedText).toBe(previewText);
    expect(savedText).toBe("Sicilian Defense: Najdorf Variation · B90");
  });
});
