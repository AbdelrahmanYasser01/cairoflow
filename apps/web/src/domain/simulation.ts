/**
 * What the simulator found, read out of the decision record. Every figure here is the record's own:
 * this module only names the measures and lines each candidate's figures up.
 */
import type { DecisionSummary, EnsembleVerdict, SimulatedComparison } from "../lib/analysis";

/** The simulator behind every simulated figure: packages/simulation runs SUMO. */
export const SIMULATOR = "SUMO";

const ARM_WORDS: Readonly<Record<string, string>> = { N: "north", E: "east", S: "south", W: "west" };

/** `max_queue_m.E` as a reader sees it. The record's own name stays one hover away. */
export function measureName(metric: string): string {
  if (metric === "mean_delay_s") return "Mean delay";
  if (metric === "throughput_veh_per_h") return "Throughput";
  const queue = /^(max|mean)_queue_m\.([NESW])$/.exec(metric);
  if (queue) return `${queue[1] === "max" ? "Longest" : "Mean"} queue from ${ARM_WORDS[queue[2] as string]}`;
  return metric;
}

export interface Measure {
  /** The record's own name for the measure. */
  metric: string;
  name: string;
  /** "13.0 to 24.7 m", baseline then candidate, or null when the run holds no comparison for it. */
  change: string | null;
}

export interface TimelineDelay {
  member: string;
  /** Candidate minus baseline: above zero is worse. */
  differenceS: number;
}

export interface CandidateFinding {
  candidate: string;
  /** Measures that worsen on every required timeline. */
  worse: Measure[];
  /** Measures that improve on every required timeline. */
  better: Measure[];
  delays: TimelineDelay[];
}

function figure(value: number, unit: string): string {
  return unit === "veh/h" ? Math.round(value).toLocaleString("en-US") : value.toFixed(1);
}

function measure(rows: readonly SimulatedComparison[], metric: string): Measure {
  const row = rows.find((item) => item.metric_name === metric);
  return {
    metric,
    name: measureName(metric),
    change: row ? `${figure(row.baseline_mean, row.unit)} to ${figure(row.candidate_mean, row.unit)} ${row.unit}` : null,
  };
}

function finding(decision: DecisionSummary, verdict: EnsembleVerdict): CandidateFinding {
  const rows = decision.metrics.filter((row) => row.candidate === verdict.candidate);
  return {
    candidate: verdict.candidate,
    worse: verdict.robust_harm.map((metric) => measure(rows, metric)),
    better: verdict.common_confirmed.map((metric) => measure(rows, metric)),
    delays: verdict.members.map(({ member, mean_delay_difference_s: differenceS }) => ({ member, differenceS })),
  };
}

export function candidateFindings(decision: DecisionSummary): CandidateFinding[] {
  return decision.ensemble.candidates.map((verdict) => finding(decision, verdict));
}

/** Which timeline `metrics` was run on: the member whose delay difference is the one it holds. */
export function comparedTimeline(decision: DecisionSummary): string | null {
  const first = decision.ensemble.candidates[0];
  const row = decision.metrics.find((item) => item.candidate === first?.candidate && item.metric_name === "mean_delay_s");
  if (!first || !row) return null;
  return first.members.find((member) => Math.abs(member.mean_delay_difference_s - row.mean_difference) < 1e-6)?.member ?? null;
}

export function signedSeconds(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}
