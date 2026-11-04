/**
 * Which analyses a reader is offered, and what they are called on screen. Ids and record titles stay
 * as they are: Reproducibility shows the id, and the twin's Limits shows the record's title.
 */

/** The study the product ships with: the 15-minute camera run its held-out gate decided. */
export const DEFAULT_ANALYSIS = "cv-ngsim-int2-15min-study";
/** The 60 s development window refined by SAM 3.1. It has no gate of its own. */
export const SAM_ANALYSIS = "cv-hero-sam";

const NAMES: Readonly<Record<string, string>> = {
  [DEFAULT_ANALYSIS]: "15-minute study (checked)",
  [SAM_ANALYSIS]: "60 s with SAM 3.1 masks",
  "ref-ngsim-int2-full": "NGSIM published reference, 15 minutes",
  "ref-ngsim-int2-960-1020": "NGSIM published reference, 60 s",
};

/**
 * Runs written before their gate or their refinement, which their successors replace. The 15-minute
 * one covers the window the gate read, so offering it would put a figure the gate withheld back on
 * screen, with a record that says nothing checked it.
 */
const SUPERSEDED: ReadonlySet<string> = new Set(["cv-ngsim-int2-15min", "cv-ngsim-int2-15min-gated", "cv-ngsim-int2-15min-checked", "cv-hero-dev"]);

export function offeredAnalyses<Row extends { id: string }>(rows: readonly Row[]): Row[] {
  return rows.filter((row) => !SUPERSEDED.has(row.id));
}

interface Named {
  id: string;
  title: string;
  mode: "precomputed" | "live";
}

/** A name a person can read. A run with no name of its own keeps its title, less the product's prefix. */
export function analysisName({ id, title, mode }: Named): string {
  const named = NAMES[id];
  if (named) return named;
  const rest = title.replace(/^CairoFlow CV,\s*/i, "");
  return mode === "live" ? `Live run, ${rest}` : rest;
}
