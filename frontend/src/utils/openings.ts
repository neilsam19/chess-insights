/**
 * Opening display formatting - the single place opening text is composed.
 *
 * The backend already decided *what* the opening is; the frontend never
 * classifies, it only renders. Both the preview flow and saved analyses go
 * through here so the two can't drift apart.
 */

const ECO_CODE_PATTERN = /^[A-E]\d{2}$/;

export const UNKNOWN_OPENING_LABEL = "Unknown opening";

/**
 * Compose a game's opening label, e.g.
 *   "Sicilian Defense: Najdorf Variation · B90"
 *
 * Never emits a dangling separator, a bare `null`, or an empty string - a
 * missing ECO simply drops the suffix, and a missing name falls back to the
 * code or to an explicit unknown label.
 */
export function formatOpeningLabel(
  name: string | null | undefined,
  eco: string | null | undefined,
): string {
  const openingName = name?.trim() ?? "";
  const ecoCode = eco?.trim() ?? "";

  if (openingName && ecoCode) {
    // When classification fell back to an ECO code, name and code are the
    // same value - render it once, not as "B90 · B90".
    if (openingName === ecoCode) {
      return `ECO ${ecoCode}`;
    }
    return `${openingName} · ${ecoCode}`;
  }

  if (openingName) {
    return ECO_CODE_PATTERN.test(openingName) ? `ECO ${openingName}` : openingName;
  }

  if (ecoCode) {
    return `ECO ${ecoCode}`;
  }

  return UNKNOWN_OPENING_LABEL;
}

/**
 * The opening-statistics table groups by name only - the backend has already
 * resolved each group to a canonical name (or, failing that, a bare ECO code).
 */
export function formatOpeningStatName(name: string): string {
  return formatOpeningLabel(name, null);
}
