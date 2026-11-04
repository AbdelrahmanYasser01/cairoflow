/**
 * Words the backend already fixes, copied from packages/contracts so that page structure is
 * built on the real vocabulary. These are names only; no value here is a measurement.
 */

/** `STAGES` in cairoflow_contracts/provenance.py, in chain order. */
export const CHAIN_STAGES = [
  { id: "source_record", label: "Source", holds: "Source file, by hash" },
  { id: "observation", label: "Observation", holds: "Tracks, samples, model replies" },
  { id: "world_entity", label: "World entity", holds: "World-model entities" },
  { id: "problem_statement", label: "Problem", holds: "Stated problem, with evidence" },
  { id: "intervention", label: "Intervention", holds: "Proposal, checked by code" },
  { id: "experiment", label: "Experiment", holds: "Simulator runs vs baseline" },
  { id: "decision_report", label: "Decision", holds: "Report and recommendation" },
] as const;

export type ChainStageId = (typeof CHAIN_STAGES)[number]["id"];

/** `AnalysisMode` in cairoflow_contracts/analysis_record.py, as a screen names it. */
export const MODE_LABEL = { precomputed: "Precomputed", live: "Live analysis" } as const;

/** `Arm` and `Turn` in cairoflow_contracts/evidence.py. */
export const ARMS = ["N", "E", "S", "W"] as const;

/**
 * An arm is named for where traffic comes from, and a US engineer reads "N" as northbound, so a
 * screen spells it out. Arm N is vehicles arriving from the north, travelling southbound.
 */
export const ARM_NAMES = {
  N: { from: "From north", heading: "Southbound" },
  E: { from: "From east", heading: "Westbound" },
  S: { from: "From south", heading: "Northbound" },
  W: { from: "From west", heading: "Eastbound" },
} as const;

export const TURNS = [
  { id: "left", label: "Left" },
  { id: "through", label: "Through" },
  { id: "right", label: "Right" },
  { id: "u_turn", label: "U-turn" },
] as const;

/** `VehicleClass` in cairoflow_contracts/analysis_record.py. */
export const VEHICLE_CLASSES = [
  { id: "car", label: "Car" },
  { id: "truck", label: "Truck" },
  { id: "bus", label: "Bus" },
  { id: "motorcycle", label: "Motorcycle" },
  { id: "vehicle", label: "Unclassified" },
  { id: "unknown", label: "Unknown" },
] as const;

/** `BaselineStatus` in cairoflow_contracts/report.py. Only `validated` allows a recommendation. */
export const BASELINE_STATUSES = [
  { id: "validated", label: "Validated", recommends: true },
  { id: "partially_validated", label: "Partially validated", recommends: false },
  { id: "not_validated", label: "Not validated", recommends: false },
  { id: "not_assessed", label: "Not assessed", recommends: false },
] as const;
