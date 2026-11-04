/**
 * Reading a registered gate out of a record. A value that failed its gate is withheld, and that is the
 * product doing its job, so it is stated as plainly as a value: which check, what it measured, what
 * the limit was. Nothing here computes or substitutes a figure: a withheld value stays absent, and a
 * reference count is never offered in its place.
 */
import type { AnalysisRecord, GateOutcome } from "../lib/analysis";

/** Plain names. The gate file's own metric id stays one hover away, on the Gates table. */
const GATE_NAMES: Record<string, string> = {
  vehicle_recall: "Share of vehicle-frames found",
  precision_proxy: "Track rows matched to NGSIM",
  across_heading_p90_m: "Sideways position error",
  lane_agreement: "Same lane as published",
  entering_count_error_share: "Entering count error",
  turning_matrix_agreement: "Turning count agreement",
  speed_abs_error_median_m_s: "Speed error",
};

/** What each check measures, from the definitions in cairoflow_evaluation/cv_agreement.py. */
const GATE_GLOSS: Record<string, string> = {
  vehicle_recall: "Share of published vehicle positions in view that the camera run also found.",
  precision_proxy:
    "Share of camera positions that match a published vehicle. A stand-in for precision: the published file doesn't cover every approach in view.",
  across_heading_p90_m: "9 in 10 points sit within this distance of the published track, measured across its direction of travel.",
  lane_agreement: "Share of paired points in the same lane as the published vehicle.",
  entering_count_error_share: "Difference from the published entering counts, by arm and block, as a share of those counts.",
  turning_matrix_agreement: "How far camera and published counts overlap, movement by movement, as a share of the larger total.",
  speed_abs_error_median_m_s: "Median difference from the published speed, on points moving at 2 m/s or more.",
};

/** What a reading of this check is, after its number: "20.33% count error". */
const GATE_NOUN: Record<string, string> = {
  entering_count_error_share: "count error",
};

/** Gates record to four places, so the measured value is shown whole: 0.2033, never 0.203. */
const MEASURED_PLACES = 4;
const LIMIT_PLACES = 2;

export function gateName(metric: string): string {
  const name = GATE_NAMES[metric];
  if (name) return name;
  const words = metric.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function gateGloss(metric: string): string | null {
  return GATE_GLOSS[metric] ?? null;
}

export const measuredWords = (value: number): string => value.toFixed(MEASURED_PLACES);
export const limitWords = (value: number): string => value.toFixed(LIMIT_PLACES);

type GateUnit = "share" | "m" | "m/s";

function gateUnit(metric: string): GateUnit {
  if (metric.endsWith("_m_s")) return "m/s";
  return metric.endsWith("_m") ? "m" : "share";
}

/** A measured value with its unit. A share keeps all four recorded places, so a near miss stays one. */
export function gateReading(metric: string, value: number): string {
  const unit = gateUnit(metric);
  return unit === "share" ? `${(value * 100).toFixed(2)}%` : `${value.toFixed(2)} ${unit}`;
}

/** A limit with its unit: limits are round numbers, and are written as round numbers. */
export function gateLimit(metric: string, threshold: number): string {
  const unit = gateUnit(metric);
  return unit === "share" ? `${Number((threshold * 100).toFixed(2))}%` : `${Number(threshold.toFixed(2))} ${unit}`;
}

/** `<=` as a person reads a limit. */
export function comparatorWord(comparator: string): string {
  return comparator === "<=" ? "at most" : comparator === ">=" ? "at least" : comparator;
}

export interface Withheld {
  /** The check that failed, when the record names it. */
  check: GateOutcome | null;
  gateId: string | null;
  /** "20.33% count error, limit 20%", or null when the record holds no measured value. */
  versus: string | null;
  /** What the record says about the limit. */
  limitNote: string;
  /** The record's own reason for this figure. */
  reason: string;
}

const LIMIT_FIXED = "The limit was fixed before the read.";

/** Anything the record can withhold: a metric or a series. */
interface Gated {
  status?: string | undefined;
  gate_id?: string | null | undefined;
  reason?: string | null | undefined;
}

function failedCheck(record: AnalysisRecord, item: Gated): GateOutcome | null {
  const failed = (record.gates ?? []).filter((gate) => !gate.passed && gate.gate_id === item.gate_id);
  return failed.find((gate) => item.reason?.includes(gate.metric)) ?? failed[0] ?? null;
}

function limitNote(record: AnalysisRecord, check: GateOutcome | null): string {
  const line = check ? record.disclosures.find((text) => text.startsWith(`${check.metric} failed`)) : undefined;
  return line?.split(/(?<=\.)\s/).find((sentence) => sentence.includes("before the read")) ?? LIMIT_FIXED;
}

function versusWords(check: GateOutcome, measured: number): string {
  const noun = GATE_NOUN[check.metric] ?? gateName(check.metric).toLowerCase();
  return `${gateReading(check.metric, measured)} ${noun}, limit ${gateLimit(check.metric, check.threshold)}`;
}

/** Null unless this figure failed its gate. */
export function withheldBy(record: AnalysisRecord, item: Gated): Withheld | null {
  if (item.status !== "gate_fail") return null;
  const check = failedCheck(record, item);
  const measured = check?.measured ?? null;
  return {
    check,
    gateId: check?.gate_id ?? item.gate_id ?? null,
    versus: check && measured !== null ? versusWords(check, measured) : null,
    limitNote: limitNote(record, check),
    reason: item.reason ?? "This figure failed its gate.",
  };
}

/** The labels of every figure one gate holds back, as the record lists them. */
export function figuresWithheld(record: AnalysisRecord): string[] {
  return record.metrics.filter((metric) => metric.status === "gate_fail").map((metric) => metric.label);
}

export function gatesPassed(record: AnalysisRecord): { passed: number; total: number } {
  const gates = record.gates ?? [];
  return { passed: gates.filter((gate) => gate.passed).length, total: gates.length };
}

/** How far a placement gate puts a point from the published track, in a sentence. */
export function placementWords(gate: GateOutcome): string {
  const measured = (gate.measured as number).toFixed(2);
  const reading =
    gate.metric === "across_heading_p90_m"
      ? `Sideways position error: 9 in 10 points within ${measured} m of the published track`
      : `${gateName(gate.metric)}: ${measured} m`;
  return gate.passed ? reading : `${reading}, check failed`;
}

const MID_VIEW = /(\d[\d,]*) other counted tracks began mid-view/;

/**
 * Why a turning count can pass while an entering count fails, once the same run shows both. Null
 * unless the record has that split. These are two definitions, so one is no route to the other.
 */
export function countsNote(record: AnalysisRecord): string | null {
  const gates = record.gates ?? [];
  const entering = gates.find((gate) => gate.metric === "entering_count_error_share");
  const turning = gates.find((gate) => gate.metric === "turning_matrix_agreement");
  if (!entering || entering.passed || !turning?.passed || turning.measured == null) return null;
  const midView = record.disclosures.join(" ").match(MID_VIEW)?.[1];
  const dropped = midView ? `, so ${midView} tracks that began mid-view drop out` : "";
  return (
    `Turning counts passed and entering counts failed because they are different checks. Entering counts keep only ` +
    `tracks first seen at the edge of the picture${dropped}. Turning counts use every counted track's first and last ` +
    `point, compared movement by movement: ${gateReading(turning.metric, turning.measured)} agreement, limit ` +
    `${gateLimit(turning.metric, turning.threshold)}. So the rows of Movement counts are not entering counts, and no ` +
    `withheld figure can be read from them.`
  );
}

/** Raw field names as a reader sees them. Record text keeps its meaning; only the names change. */
const FIELD_WORDS: readonly (readonly [RegExp, string])[] = [
  [/\blocal_enu_junction_m\b/g, "local junction grid, in metres"],
  [/\bacross_heading_p90_m\b/g, "sideways position error"],
  [/\bentering_count_error_share\b/g, "Entering count error"],
  [/\bturning_matrix_agreement\b/g, "turning count agreement"],
];

const RAW_FAILURE = /entering_count_error_share failed: ([\d.]+) against a limit of <= ([\d.]+)\./;

/** A record's sentence with its raw field names written out. `hideIds` also drops ADR references. */
export function recordWords(line: string, { hideIds = false }: { hideIds?: boolean } = {}): string {
  const metric = "entering_count_error_share";
  const failure = line.replace(RAW_FAILURE, (_all, measured: string, limit: string) => {
    const reading = gateReading(metric, Number(measured));
    return `Entering count error failed: ${reading} (${measured}) against a limit of ${gateLimit(metric, Number(limit))}.`;
  });
  const worded = FIELD_WORDS.reduce((text, [pattern, words]) => text.replace(pattern, words), failure);
  return hideIds ? worded.replace(/\s*\(ADR-\d+\)/g, "") : worded;
}
