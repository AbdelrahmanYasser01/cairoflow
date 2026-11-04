/**
 * What SAM changed in an analysis, in figures. Most come from the record's own sam_refine stage and
 * the stages before it. Three come from the measured records beside the run, which neither the record
 * nor the API carries: samMeasured.json is an extract of them, and sam.test.ts holds it to the source
 * files, so nothing here is typed in by hand.
 */
import type { AnalysisRecord, StageReceipt, TrackEntry } from "../lib/analysis";
import extract from "./samMeasured.json";

export type SamMeasured = typeof extract;

export interface SamChange {
  requests: number | null;
  costUsd: number | null;
  rowsConfirmed: number | null;
  rowsObserved: number | null;
  rowsReplaced: number | null;
  rowsInterpolated: number | null;
  interpolatedShareBefore: number | null;
  interpolatedShareAfter: number | null;
  /** Tracks whose class comes from mask length, and how many of those became a bus. */
  classFromMask: number;
  busesFromMask: number;
  /** Null when this run is not the one the measured records describe. */
  measured: SamMeasured | null;
}

function stageOf(record: AnalysisRecord, name: string): StageReceipt | undefined {
  return record.stages.find((stage) => stage.stage === name);
}

function count(stage: StageReceipt | undefined, key: string): number | null {
  const value = stage?.parameters?.[key];
  return typeof value === "number" ? value : null;
}

/** The extract applies to the run it was taken from, not to any run with a SAM stage. */
export function samMeasured(record: AnalysisRecord): SamMeasured | null {
  const stage = stageOf(record, "sam_refine");
  const named = record.analysis_id === extract.analysis_id && stage?.parameters?.parent_analysis === extract.parent_analysis;
  return named ? extract : null;
}

export type SamFacts = Omit<SamChange, "classFromMask" | "busesFromMask" | "measured">;

/** What the record's own stages say SAM did. Null when no SAM stage ran. */
export function samFacts(record: AnalysisRecord): SamFacts | null {
  const sam = stageOf(record, "sam_refine");
  if (!sam) return null;
  const rows = count(stageOf(record, "place"), "rows");
  const interpolated = count(stageOf(record, "relink"), "interpolated_rows");
  const left = count(sam, "rows_interpolated_left");
  const extended = count(sam, "rows_extended");
  return {
    requests: count(sam, "requests"),
    costUsd: sam.cost_usd ?? null,
    rowsConfirmed: count(sam, "rows_confirmed"),
    rowsObserved: rows !== null && interpolated !== null ? rows - interpolated : null,
    rowsReplaced: count(sam, "rows_replaced"),
    rowsInterpolated: interpolated,
    interpolatedShareBefore: rows !== null && interpolated !== null ? interpolated / rows : null,
    interpolatedShareAfter: rows !== null && left !== null && extended !== null ? left / (rows + extended) : null,
  };
}

/** Null when no SAM stage ran. */
export function samChange(record: AnalysisRecord, tracks: readonly TrackEntry[]): SamChange | null {
  const facts = samFacts(record);
  if (!facts) return null;
  const fromMask = tracks.filter((track) => track.class_source === "sam");
  return {
    ...facts,
    classFromMask: fromMask.length,
    busesFromMask: fromMask.filter((track) => track.vehicle_class === "bus").length,
    measured: samMeasured(record),
  };
}

const recordName = (measured: SamMeasured) => measured.measured_record.path.split("/").at(-2) ?? measured.measured_record.path;

/**
 * Lines for the disclosures, in the record's voice, for figures only the measured records hold. Empty
 * for any other run.
 */
export function samMeasuredDisclosures(record: AnalysisRecord): string[] {
  const measured = samMeasured(record);
  if (!measured) return [];
  const [from, to] = measured.window_source_s;
  const { interpolated_position: before, sam_position: after } = measured.across_road_error_m;
  const { with_sam_boxes: boxes, same_rows_interpolated: control } = measured.recall;
  return [
    `On rows where SAM's box replaced an interpolated one, median across-road error fell from ${before} to ${after} m in source seconds ${from} to ${to}. This is ${measured.label}. Measured record ${recordName(measured)}.`,
    `SAM did not raise recall. With SAM's boxes recall reads ${boxes}; the same rows left at their interpolated positions read ${control}. Measured record ${recordName(measured)}.`,
    `Class by mask length rests on thin evidence: only ${measured.long_vehicles_in_window} long vehicles are in this window.`,
  ];
}
