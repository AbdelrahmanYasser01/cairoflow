import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import block from "./samBlock.json";

const repo = resolve(__dirname, "../../../..");
const measuredPath = resolve(repo, block.measured_record.path);

describe.skipIf(!existsSync(measuredPath))("the block extract is the measured record", () => {
  const text = existsSync(measuredPath) ? readFileSync(measuredPath, "utf8") : "{}";
  const measured = JSON.parse(text);
  const path = measured.block_14_scoring_path;

  it("names the file it was taken from, as it stands", () => {
    expect(createHash("sha256").update(text).digest("hex")).toBe(block.measured_record.sha256);
    expect(measured.refined).toBe(block.analysis_id);
    expect(measured.parent).toBe(block.parent_analysis);
    expect(measured.label).toBe(block.label);
  });

  it("copies each figure unchanged", () => {
    const interpolated = path.replaced_rows_interpolated_position;
    const sam = path.replaced_rows_sam_position;
    expect(interpolated.rows).toBe(block.replaced_rows);
    expect(sam.rows).toBe(block.replaced_rows);
    expect(interpolated.matched).toBe(block.replaced_rows_matched);
    expect(sam.matched).toBe(block.replaced_rows_matched);
    expect(interpolated.median_abs_across_m).toBe(block.across_road_error_m.interpolated_position);
    expect(sam.median_abs_across_m).toBe(block.across_road_error_m.sam_position);
    expect(interpolated.median_abs_along_m).toBe(block.along_road_error_m.interpolated_position);
    expect(sam.median_abs_along_m).toBe(block.along_road_error_m.sam_position);
    expect(path.before.recall.value).toBe(block.recall.before);
    expect(path.after.recall.value).toBe(block.recall.with_sam_boxes);
    expect(path.control_same_rows_interpolated.recall.value).toBe(block.recall.same_rows_interpolated);
  });
});
