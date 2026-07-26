import { UNKNOWN_OPENING_LABEL, formatOpeningLabel } from "../utils/openings";

interface OpeningLabelProps {
  name: string | null;
  eco: string | null;
  className?: string;
}

/**
 * Shared by the preview flow and saved analyses so a game's opening reads
 * identically wherever it appears.
 */
export function OpeningLabel({ name, eco, className }: OpeningLabelProps) {
  const label = formatOpeningLabel(name, eco);
  const isUnknown = label === UNKNOWN_OPENING_LABEL;

  const classNames = ["opening-label"];
  if (isUnknown) classNames.push("opening-label-unknown");
  if (className) classNames.push(className);

  return <span className={classNames.join(" ")}>{label}</span>;
}
