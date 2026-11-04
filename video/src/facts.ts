// Every figure the edit shows, copied from README.md ("What it found", "How the Meta models are
// used") and data/sample. Nothing here is computed or rounded for the video. If a source changes,
// change it here and nowhere else.

export type Status = "checked" | "approximated" | "assumed" | "ungated" | "simulated" | "model" | "withheld";

export const SITE = {
  street: "Lankershim Blvd at Universal Hollywood Dr",
  city: "Los Angeles",
  date: "16 June 2005",
  study: "15-minute study",
  source: "NGSIM", // README: "filmed for the NGSIM programme on 16 June 2005"
};

// README: "The 15-minute study counted 967 tracks. That total has no gate behind it"
// data/sample/diagnoses/...: metric:vehicles.total evidence_kind approximated, status ungated.
export const TRACKS = { value: 967, statuses: ["approximated", "ungated"] as Status[] };

export type Check = {
  label: string;
  value: number;
  shown: string;
  limit: number;
  limitShown: string;
  sense: "atLeast" | "atMost";
  pass: boolean;
};

// README table "Held-out result / Limit".
export const CHECKS: Check[] = [
  { label: "Recall", value: 0.761, shown: "0.761", limit: 0.7, limitShown: "≥ 0.70", sense: "atLeast", pass: true },
  { label: "Matched to NGSIM", value: 0.863, shown: "0.863", limit: 0.8, limitShown: "≥ 0.80", sense: "atLeast", pass: true },
  { label: "Position error p90", value: 1.39, shown: "1.39 m", limit: 1.5, limitShown: "≤ 1.5 m", sense: "atMost", pass: true },
  { label: "Lane agreement", value: 0.881, shown: "0.881", limit: 0.85, limitShown: "≥ 0.85", sense: "atLeast", pass: true },
  { label: "Turning agreement", value: 0.847, shown: "0.847", limit: 0.8, limitShown: "≥ 0.80", sense: "atLeast", pass: true },
  { label: "Median speed error", value: 0.48, shown: "0.48 m/s", limit: 1.0, limitShown: "≤ 1.0 m/s", sense: "atMost", pass: true },
  { label: "Entering count error", value: 0.2033, shown: "0.2033", limit: 0.2, limitShown: "≤ 0.20", sense: "atMost", pass: false },
];
export const MISS = "0.0033"; // README: "Entering counts missed by 0.0033."

// README "How the Meta models are used": one 60-second window, $0.32.
export const SAM = {
  replaced: 586,
  interpolatedBefore: "7.7%",
  interpolatedAfter: "2.7%",
  acrossBefore: "0.37 m",
  acrossAfter: "0.29 m",
  alongBefore: "0.55 m",
  alongAfter: "0.58 m",
  agreed: "9,900 of 10,858",
  cost: "$0.32",
  window: "60 s",
};

// data/sample/diagnoses/cv-ngsim-int2-15min-study.json, replicate 1, first hypothesis.
export const MUSE = {
  model: "muse-spark-1.3",
  label: "model-generated hypotheses; not a measurement",
  hypothesisId: "h-ns-through",
  statement:
    "Operation is limited by heavy north-south through demand, with the N-S and S-N through movements dominating the validated turn counts and producing high stopped time.",
  confidence: "low",
  supporting: ["movement:N-S", "movement:S-N", "metric:vehicles.total", "metric:stopped.share"],
  contradicting: ["metric:speed.mean_moving"],
  // All 24 evidence ids the request carried, in record order.
  evidence: [
    "metric:vehicles.total", "metric:flow.entering", "metric:speed.mean_moving", "metric:stopped.share",
    "metric:vehicles.arm.n", "metric:vehicles.arm.e", "metric:vehicles.arm.s", "metric:vehicles.arm.w",
    "metric:vehicles.class.bus", "metric:vehicles.class.car", "metric:vehicles.class.motorcycle",
    "metric:vehicles.class.truck", "movement:E-N", "movement:E-S", "movement:E-W", "movement:N-E",
    "movement:N-S", "movement:N-W", "movement:S-E", "movement:S-N", "movement:S-W", "movement:W-E",
    "movement:W-N", "movement:W-S",
  ],
  replicates: 3,
  citationProblems: 0,
  unsupportedNumbers: 0,
  hypotheses: 3,
};

// data/sample/decision_summary.json outcome.selection_reason, and README "The decision".
export const SUMO = {
  candidates: [
    { id: "I1", worsens: ["throughput_veh_per_h", "max_queue_m.E", "mean_queue_m.E", "mean_queue_m.W"] },
    {
      id: "I2",
      worsens: ["mean_delay_s", "throughput_veh_per_h", "max_queue_m.E", "max_queue_m.S", "mean_queue_m.E", "mean_queue_m.S", "mean_queue_m.W"],
    },
    { id: "I3", worsens: ["mean_delay_s", "throughput_veh_per_h", "max_queue_m.E", "mean_queue_m.E", "mean_queue_m.W"] },
  ],
  seeds: 20,
  timelines: 4,
  baseline: "2 timelines partly validated, 2 not assessed",
  inputs: "published NGSIM trajectories, not camera output",
  recommendation: "Nothing is recommended: no candidate was selected.",
};

export const CREDITS = {
  repo: "github.com/AbdelrahmanYasser01/cairoflow",
  data: "NGSIM data: U.S. DOT FHWA, CC BY-SA",
  models: "Built with SAM 3.1 and Muse Spark",
  scope: "Los Angeles, 2005 · decision support only",
};
