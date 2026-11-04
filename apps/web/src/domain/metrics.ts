/**
 * Reading figures out of a record. Nothing here computes a measurement: it finds what the record
 * holds and changes only how it is written, such as a share of 0.46 as 46 %.
 */
import type { AnalysisRecord, Metric, MetricSeries, MovementCell } from "../lib/analysis";

export function findMetric(record: AnalysisRecord, metricId: string): Metric | undefined {
  return record.metrics.find((metric) => metric.metric_id === metricId);
}

export interface Reading {
  value: number | null;
  unit: string;
  format: (value: number) => string;
}

const whole = (value: number) => Math.round(value).toLocaleString("en-US");
const twoPlaces = (value: number) => value.toFixed(2);

/** How a metric is written on screen. A value of null stays null: it is never shown as zero. */
export function readMetric(metric: Metric): Reading {
  const value = metric.value ?? null;
  if (metric.unit === "share") {
    return { value: value === null ? null : value * 100, unit: "%", format: whole };
  }
  if (metric.unit === "m/s") return { value, unit: metric.unit, format: twoPlaces };
  return { value, unit: metric.unit, format: whole };
}

/** Metrics of one family, such as `vehicles.class.` */
export function metricsUnder(record: AnalysisRecord, prefix: string): Metric[] {
  return record.metrics.filter((metric) => metric.metric_id.startsWith(prefix));
}

export interface Bin {
  startS: number;
  /** Sum over the arms shown. Null when any of them has no value for this bin. */
  total: number | null;
  /** The one arm picked out, when a filter is on. */
  selected: number | null;
}

/** The arm a series belongs to, read from its scope: `arm:N`. */
export function seriesArm(series: MetricSeries): string | null {
  return series.scope.startsWith("arm:") ? series.scope.slice(4) : null;
}

/** Bins of the entering-flow series, summed across arms. A missing value never counts as zero. */
export function entryBins(record: AnalysisRecord, arm: string | null): Bin[] {
  const series = (record.series ?? []).filter((item) => seriesArm(item) !== null);
  const first = series[0];
  if (!first) return [];
  return first.values.map((_, index) => {
    const values = series.map((item) => item.values[index] ?? null);
    const total = values.some((value) => value === null)
      ? null
      : values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
    const picked = series.find((item) => seriesArm(item) === arm);
    return {
      startS: first.start_s + index * first.bin_s,
      total,
      selected: picked ? (picked.values[index] ?? null) : null,
    };
  });
}

/**
 * How an entering count is made, per local/zones.py and scripts/build_reference_analysis.py. Both
 * classify a vehicle by where it first appeared; neither watches a line. Null with no arms to count.
 */
export function enteringRule(record: AnalysisRecord): string | null {
  if (record.calibration.mode !== "calibrated") return null;
  return record.data_origin === "camera_cv"
    ? "Counted by track origin, not at a stop line: a track adds one to the arm of its first usable point, in the bin of its first frame, if it was first seen near the image's edge or at the start of the window. So the first bin also holds vehicles already in view when the window began."
    : "Counted by track origin, not at a stop line: a published vehicle adds one to the arm of its first point, in the bin of its first frame.";
}

/** A record's reason for an absent value, as a sentence: "not available: it has no arms" reads "It has no arms." */
export function absentWords(reason: string | null | undefined): string {
  const text = (reason ?? "not recorded").replace(/^not available:\s*/i, "");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

const NO_ALONG_ROAD = "Withheld: this camera setup cannot measure distance along the road reliably.";

/** A reason in words a reader can use. The record's own text, with its ADR id, stays in Limits and Limitations. */
export function plainReason(reason: string | null | undefined): string {
  return reason && /along-road distance/i.test(reason) ? NO_ALONG_ROAD : absentWords(reason);
}

export interface WindowCount {
  count: number;
  seconds: number;
}

/**
 * Entering flow as what was counted. An hourly rate scaled up from an unchecked window claims more
 * than the run holds, so without a passed gate the raw count and its window stand in its place. The
 * count is the record's own arms added up: 83 vehicles in 60 s, not 4,980 veh/h.
 */
export function windowCount(record: AnalysisRecord, metric: Metric): WindowCount | null {
  if (metric.metric_id !== "flow.entering" || metric.value == null || metric.status === "gate_pass") return null;
  const arms = metricsUnder(record, "vehicles.arm.");
  if (arms.length === 0 || arms.some((arm) => arm.value == null)) return null;
  return {
    count: arms.reduce((sum, arm) => sum + (arm.value ?? 0), 0),
    seconds: Math.round(record.frame_count / record.fps),
  };
}

const FRAGMENTS = /(\d[\d,]*) tracks shorter than 1 s are fragments/;

/** How many tracks the record keeps in its files and leaves out of its counts. Null when it doesn't say. */
export function fragmentCount(record: AnalysisRecord): number | null {
  const found = record.disclosures.join(" ").match(FRAGMENTS)?.[1];
  return found ? Number(found.replace(/,/g, "")) : null;
}

/** A round axis top and step for a maximum, so ticks fall on numbers a person would write. */
export function niceAxis(max: number, ticks = 4): { top: number; step: number } {
  if (max <= 0) return { top: 1, step: 1 };
  const rough = max / ticks;
  const power = 10 ** Math.floor(Math.log10(rough));
  const unit = rough / power;
  const step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
  return { top: Math.ceil(max / step) * step, step };
}

/** The count in one cell of the turning-movement matrix, or the cell itself if it has none. */
export function movementCell(
  movements: readonly MovementCell[],
  approach: string,
  turn: string,
): MovementCell | undefined {
  return movements.find((cell) => cell.approach === approach && cell.turn === turn);
}
