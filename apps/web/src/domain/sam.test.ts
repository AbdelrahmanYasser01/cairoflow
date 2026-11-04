import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { AnalysisRecord, TrackEntry } from "../lib/analysis";
import { samChange, samMeasured, samMeasuredDisclosures } from "./sam";
import extract from "./samMeasured.json";

const repo = resolve(__dirname, "../../../..");
const sample = resolve(repo, "data/sample/analyses/cv-hero-sam");
const read = (path: string) => readFileSync(path, "utf8");

const measuredPath = resolve(repo, extract.measured_record.path);
const runPath = resolve(repo, extract.run_record.path);
const recordPath = resolve(sample, "record.json");

describe.skipIf(!existsSync(measuredPath) || !existsSync(runPath))("the extract is the measured records", () => {
  const measuredText = existsSync(measuredPath) ? read(measuredPath) : "{}";
  const runText = existsSync(runPath) ? read(runPath) : "{}";
  const measured = JSON.parse(measuredText);
  const run = JSON.parse(runText);

  it("names the files it was taken from, as they stand", () => {
    expect(createHash("sha256").update(measuredText).digest("hex")).toBe(extract.measured_record.sha256);
    expect(createHash("sha256").update(runText).digest("hex")).toBe(extract.run_record.sha256);
    expect(measured.refined).toBe(extract.analysis_id);
    expect(measured.parent).toBe(extract.parent_analysis);
    expect(measured.label).toBe(extract.label);
  });

  it("copies each figure unchanged", () => {
    const { w3 } = measured;
    expect(measured.w3_source_s).toEqual(extract.window_source_s);
    expect(w3.replaced_rows_interpolated_position.median_abs_across_m).toBe(extract.across_road_error_m.interpolated_position);
    expect(w3.replaced_rows_sam_position.median_abs_across_m).toBe(extract.across_road_error_m.sam_position);
    expect(w3.after.recall.value).toBe(extract.recall.with_sam_boxes);
    expect(w3.control_same_rows_interpolated.recall.value).toBe(extract.recall.same_rows_interpolated);
    const { true_long: found, missed_long: missed } = run.size_evidence.mask_length;
    expect(found + missed).toBe(extract.long_vehicles_in_window);
  });

  it("agrees with the rows the record's stages give", () => {
    expect(measured.rows.before.rows).toBe(11767);
    const record = JSON.parse(read(recordPath)) as AnalysisRecord;
    const change = samChange(record, []);
    expect(change?.interpolatedShareBefore?.toFixed(4)).toBe(measured.rows.before.interpolated_share.toFixed(4));
    expect(change?.interpolatedShareAfter?.toFixed(4)).toBe(measured.rows.after.interpolated_share.toFixed(4));
    expect(change?.rowsObserved).toBe(measured.block_14_scoring_path.before.precision_proxy.denominator);
  });
});

describe.skipIf(!existsSync(recordPath))("what SAM changed in the shipped run", () => {
  const record = JSON.parse(read(recordPath)) as AnalysisRecord;
  const tracks = (JSON.parse(read(resolve(sample, "tracks.json"))) as { tracks: TrackEntry[] }).tracks;

  it("reads the stage and the tracks", () => {
    expect(samChange(record, tracks)).toMatchObject({
      requests: 9,
      costUsd: 0.322,
      rowsConfirmed: 9900,
      rowsObserved: 10858,
      rowsReplaced: 586,
      rowsInterpolated: 909,
      classFromMask: 5,
      busesFromMask: 1,
    });
  });

  it("says SAM did not raise recall, and puts no before-and-after on it", () => {
    const lines = samMeasuredDisclosures(record).join("\n");
    expect(lines).toContain("SAM did not raise recall");
    expect(lines).not.toMatch(/0\.80/);
  });

  it("is silent on any other run", () => {
    const other = { ...record, analysis_id: "another-run" };
    expect(samMeasured(other)).toBeNull();
    expect(samMeasuredDisclosures(other)).toEqual([]);
    expect(samChange(other, tracks)?.measured).toBeNull();
  });

  it("has nothing to say with no SAM stage", () => {
    const plain = { ...record, stages: record.stages.filter((stage) => stage.stage !== "sam_refine") };
    expect(samChange(plain, tracks)).toBeNull();
    expect(samMeasuredDisclosures(plain)).toEqual([]);
  });
});
