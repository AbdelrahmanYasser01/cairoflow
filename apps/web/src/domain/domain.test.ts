import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { AnalysisRecord, DecisionSummary, FootageSource, Metric } from "../lib/analysis";
import { parseDecision } from "../lib/analysis/records";
import type { SiteGeometry } from "../twin/site";
import { decisionFor, readChain } from "./chain";
import { footageFor, frameOnScreen, measuredCoverage } from "./footage";
import {
  BLOCK_S,
  COVERED_END_S,
  COVERED_START_S,
  blockOf,
  devWindows,
  heldOutReached,
  offeredWindows,
  plannedFrames,
  readWindow,
} from "./heldOut";
import { enteringRule, entryBins, niceAxis, readMetric } from "./metrics";
import { armPlan, fitToBand, onArm, projector, sitePlan, turnCross } from "./plan";

const source = (over: Partial<FootageSource>): FootageSource =>
  ({ file_name: "clip.mp4", title: "Clip", duration_s: 300, source_start_s: 0, ...over }) as FootageSource;

describe("the held-out rule", () => {
  it("keeps the constants of the rule the worker enforces", () => {
    const python = readFileSync(
      resolve(__dirname, "../../../../packages/perception/cairoflow_perception/local/scoring.py"),
      "utf8",
    );
    expect(python).toContain(`COVERED_START_S, COVERED_END_S = ${COVERED_START_S}.0, ${COVERED_END_S}.0`);
    expect(python).toContain(`BLOCK_S = ${BLOCK_S}.0`);
  });

  it("numbers blocks as the worker does, and gives seconds outside the range no block", () => {
    expect(blockOf(120)).toBe(0);
    expect(blockOf(960)).toBe(14);
    expect(blockOf(1019.9)).toBe(14);
    expect(blockOf(119.9)).toBeNull();
    expect(blockOf(1020)).toBeNull();
  });

  it("finds every odd block a window reaches, its end not included", () => {
    expect(heldOutReached(960, 1020)).toEqual([]);
    expect(heldOutReached(900, 960)).toEqual([[900, 960]]);
    expect(heldOutReached(170, 250)).toEqual([[180, 240]]);
    expect(heldOutReached(120, 180)).toEqual([]);
    expect(heldOutReached(0, 100)).toEqual([]);
  });

  it("offers development blocks in seconds of the file, clipped to it", () => {
    expect(devWindows(960, 1121)).toEqual([[960, 1020]]);
    expect(offeredWindows(source({ source_start_s: 970, duration_s: 30 }))).toEqual([[0, 30]]);
    expect(offeredWindows(source({ source_start_s: null }))).toEqual([]);
  });

  it("counts frames as the worker plans them, with no phantom frame from the AVI's rate", () => {
    expect(plannedFrames(10.00001, 10, 1)).toBe(100);
    expect(plannedFrames(10, 10, 3)).toBe(34);
  });

  it("explains a refused window, and has nothing to say about an upload", () => {
    const camera = source({ source_start_s: 0, duration_s: 1122, file_name: "camera.avi" });
    expect(readWindow(camera, 970, 10).problem).toBeNull();
    expect(readWindow(camera, 900, 10).problem).toContain("held out");
    expect(readWindow(camera, 1100, 60).problem).toContain("lasts");
    const upload = source({ source_start_s: null, duration_s: 1200 });
    expect(readWindow(upload, 900, 10)).toMatchObject({ problem: null, sourceSpan: null });
  });
});

describe("footage", () => {
  const record = { source_start_s: 960, fps: 10, frame_count: 600, source: {} } as unknown as AnalysisRecord;
  const clip = source({ source_start_s: 970 });

  it("keeps the frame on screen for its whole interval, so the overlay never leads the picture", () => {
    expect(frameOnScreen(record, clip, 0)).toBe(100);
    expect(frameOnScreen(record, clip, 0.06)).toBe(100);
    expect(frameOnScreen(record, clip, 0.099)).toBe(100);
    expect(frameOnScreen(record, clip, 0.1)).toBe(101);
    expect(frameOnScreen(record, clip, 0.1 * 3)).toBe(103);
  });

  it("lays an upload only under the analysis made from that same file", () => {
    const upload = source({ source_start_s: null, file_name: "street.mp4" });
    const other = source({ source_start_s: null, file_name: "other.mp4" });
    const fromUpload = { source: { uploaded_by_user: true, source_file: "street.mp4" } } as unknown as AnalysisRecord;
    const fromCamera = { source: { uploaded_by_user: false } } as unknown as AnalysisRecord;
    expect(footageFor(fromUpload, [upload, other, clip])).toEqual([upload]);
    expect(footageFor(fromCamera, [upload, clip])).toEqual([clip]);
  });
});

describe("placement outside the measured window", () => {
  const camera = (start: number, frames: number, disclosures: string[] = []) =>
    ({
      data_origin: "camera_cv",
      source_start_s: start,
      fps: 10,
      frame_count: frames,
      disclosures,
      calibration: { mode: "calibrated", valid_windows_s: [[970, 1000]] },
    }) as unknown as AnalysisRecord;

  it("says nothing for a run inside the window", () => {
    expect(measuredCoverage(camera(975, 100))).toBeNull();
  });

  it("uses the record's own disclosure for a run partly outside", () => {
    const line = "Part of this run is outside the validated window: it covers 30 s.";
    const coverage = measuredCoverage(camera(960, 600, ["Other.", line]));
    expect(coverage).toMatchObject({ label: "Placement checked on 30 of 60 s", note: line, coveredS: 30 });
  });

  it("names a run wholly outside, and still says why with no disclosure", () => {
    const coverage = measuredCoverage(camera(240, 100));
    expect(coverage?.label).toBe("Placement not checked on this run");
    expect(coverage?.note).toContain("970 to 1000");
  });

  it("does not apply to reference data or to a run with no placement", () => {
    expect(measuredCoverage({ ...camera(960, 600), data_origin: "reference_dataset" } as AnalysisRecord)).toBeNull();
    expect(
      measuredCoverage({ ...camera(960, 600), calibration: { mode: "uncalibrated" } } as unknown as AnalysisRecord),
    ).toBeNull();
  });
});

describe("enteringRule", () => {
  it("never says a count is made at a line, and has no rule without arms", () => {
    const calibrated = { calibration: { mode: "calibrated" } };
    const camera = enteringRule({ ...calibrated, data_origin: "camera_cv" } as AnalysisRecord);
    const reference = enteringRule({ ...calibrated, data_origin: "reference_dataset" } as AnalysisRecord);
    expect(camera).toContain("track origin");
    expect(reference).toContain("track origin");
    expect(enteringRule({ calibration: { mode: "uncalibrated" } } as AnalysisRecord)).toBeNull();
  });
});

const metric = (over: Partial<Metric>): Metric =>
  ({ metric_id: "m", label: "M", unit: "veh", scope: "site", evidence_kind: "observed", status: "ungated", ...over }) as Metric;

describe("readMetric", () => {
  it("writes a share as a percentage", () => {
    const reading = readMetric(metric({ value: 0.46, unit: "share" }));
    expect(reading.unit).toBe("%");
    expect(reading.format(reading.value as number)).toBe("46");
  });

  it("keeps a missing value missing, never zero", () => {
    expect(readMetric(metric({ value: null, unit: "share" })).value).toBeNull();
    expect(readMetric(metric({ unit: "veh" })).value).toBeNull();
  });
});

describe("entryBins", () => {
  const series = (arm: string, values: (number | null)[]) => ({
    series_id: arm,
    label: arm,
    unit: "veh",
    scope: `arm:${arm}`,
    bin_s: 60,
    start_s: 0,
    values,
    evidence_kind: "observed",
    status: "ungated",
  });
  const record = {
    series: [series("N", [2, null]), series("S", [3, 4])],
  } as unknown as AnalysisRecord;

  it("sums arms, and a bin missing an arm has no total", () => {
    expect(entryBins(record, null).map((bin) => bin.total)).toEqual([5, null]);
  });

  it("picks out the filtered arm", () => {
    expect(entryBins(record, "S").map((bin) => bin.selected)).toEqual([3, 4]);
  });
});

describe("niceAxis", () => {
  it("rounds up to a step a person would write", () => {
    expect(niceAxis(87)).toEqual({ top: 100, step: 50 });
    expect(niceAxis(0)).toEqual({ top: 1, step: 1 });
  });
});

const lane = (id: string, arm: string, direction: string, u0: number, u1: number, v0: number, v1: number, turns: string[] = []) => ({
  lane_id: id,
  arm,
  direction,
  polygon_m: [[u0, v0], [u0, v1], [u1, v1], [u1, v0]],
  allowed_turns: turns,
  provenance: "approximated",
  source: "test",
});

const site = {
  site_id: "s",
  site_name: null,
  site_name_status: null,
  ground_frame: "local_enu_junction_m",
  drives_on: "right",
  calibration: null,
  lanes: [
    lane("N1", "N", "approach", -6, -3, 20, 100, ["through"]),
    lane("N2", "N", "approach", -3, 0, 20, 100, ["left"]),
    lane("Nx", "N", "exit", 1, 4, 25, 50),
    lane("W1", "W", "approach", -30, -15, -6, 0, ["through"]),
  ],
  gates: [{ arm: "W", role: "exit_line", segment_m: [[-20, 2], [-20, 9]], provenance: "approximated", source: "test" }],
} as unknown as SiteGeometry;

describe("armPlan", () => {
  it("reads spans, lanes and the median from the data", () => {
    const north = armPlan(site, "N");
    expect(north).toMatchObject({ lo: -6, hi: 4, near: 20, far: 100, median: 0.5 });
    expect(north?.approach.map((item) => item.lane.lane_id)).toEqual(["N1", "N2"]);
  });

  it("uses the exit line for the median of an arm with no exit lanes", () => {
    expect(armPlan(site, "W")?.median).toBe(1);
  });

  it("is null for an arm with no lanes", () => {
    expect(armPlan(site, "E")).toBeNull();
  });

  it("finds the lanes carrying a turn", () => {
    const north = armPlan(site, "N");
    expect(north && turnCross(north, "left")).toBe(-1.5);
  });
});

describe("projection", () => {
  it("flips north to the top of the screen", () => {
    const project = projector({ width: 100, height: 100, cx: 50, cy: 50, scale: 2 });
    expect(project(10, 10)).toEqual([70, 30]);
  });

  it("places arm positions in the right quadrant", () => {
    expect(onArm("S", 2, 30)).toEqual([2, -30]);
    expect(onArm("W", 2, 30)).toEqual([-30, 2]);
  });

  it("fits the roads into the band", () => {
    const frame = fitToBand(sitePlan(site), { left: 100, right: 200, top: 0, bottom: 300 }, 400, 400);
    const project = projector(frame);
    expect(project(-6, 0)[0]).toBeCloseTo(100);
    expect(project(4, 0)[0]).toBeCloseTo(200);
  });
});

const decision = {
  outcome: { outcome: "none_selected", recommendation: "Nothing is recommended.", selection_reason: "" },
  baseline: { status: "partially_validated" },
  simulation: { run_id: "run-1", seeds: [1, 2] },
  ensemble: { members: [{ required: true, gate_passed: true }, { required: true, gate_passed: false }] },
  hypotheses: [{ grounding: { status: "supported" } }],
  candidates: [{ validator: { status: "accepted" } }, { validator: { status: "rejected" } }],
  metrics: [],
  assumptions: [],
  limitations: [],
  sources: [],
} as unknown as DecisionSummary;

const record = {
  metrics: [{ metric_id: "vehicles.total", value: 12 }],
  gates: [],
  data_origin: "reference_dataset",
  provenance: { kind: "observed" },
  source: { captured: "16 June 2005", title: "Source" },
  decision_ref: { run_id: "run-1" },
} as unknown as AnalysisRecord;

describe("readChain", () => {
  const byId = (inputs: Parameters<typeof readChain>[0]) =>
    Object.fromEntries(readChain(inputs).map((stage) => [stage.id, stage]));

  it("shows no stage as checked when nothing is loaded", () => {
    expect(readChain({ record: null, decision: null, site: null }).every((stage) => stage.status === "not_available")).toBe(true);
  });

  it("leaves a stage with no gate ungated, whatever its record says", () => {
    expect(byId({ record, decision, site }).observation?.status).toBe("ungated");
  });

  it("passes a gate only when every check on record passed", () => {
    const chain = byId({ record, decision, site });
    expect(chain.problem_statement?.status).toBe("gate_pass");
    expect(chain.intervention?.status).toBe("gate_fail");
    expect(chain.experiment?.status).toBe("gate_fail");
  });

  it("abstains unless the baseline is validated, and never calls that a failed check", () => {
    expect(byId({ record, decision, site }).decision_report?.status).toBe("abstained");
    const validated = { ...decision, baseline: { status: "validated" } } as DecisionSummary;
    expect(byId({ record, decision: validated, site }).decision_report?.status).toBe("gate_pass");
  });
});

describe("decision records", () => {
  it("pairs a decision only with the analysis naming its run", () => {
    expect(decisionFor(record, decision)).toBe(decision);
    const other = { ...record, decision_ref: { run_id: "run-2" } } as unknown as AnalysisRecord;
    expect(decisionFor(other, decision)).toBeNull();
  });

  it("rejects a record with no outcome, by name", () => {
    expect(() => parseDecision({})).toThrow("no outcome");
    expect(() => parseDecision(decision)).not.toThrow();
  });
});
