import { describe, expect, it } from "vitest";
import type { AnalysisRecord, DecisionSummary, Metric } from "../lib/analysis";
import { DEFAULT_ANALYSIS, SAM_ANALYSIS, analysisName, offeredAnalyses } from "./analyses";
import { CHAIN_BRANCHES, decisionFor, readChain, whatWouldChange } from "./chain";
import { countsNote, gateLimit, gateReading, placementWords, recordWords } from "./gates";
import { fragmentCount, plainReason, windowCount } from "./metrics";
import { candidateFindings, comparedTimeline, measureName } from "./simulation";

const metric = (metric_id: string, value: number | null, status = "ungated"): Metric =>
  ({ metric_id, value, status, label: metric_id, unit: "veh", evidence_kind: "approximated" }) as unknown as Metric;

const armCounts = [metric("vehicles.arm.n", 49), metric("vehicles.arm.e", 2), metric("vehicles.arm.s", 32), metric("vehicles.arm.w", 0)];

const record = (metrics: Metric[]) => ({ metrics, frame_count: 600, fps: 10 }) as unknown as AnalysisRecord;

describe("the analyses on offer", () => {
  const listed = ["cv-ngsim-int2-15min", "cv-hero-dev", DEFAULT_ANALYSIS, SAM_ANALYSIS, "ref-ngsim-int2-full", "cv-job-1"];

  it("never offers the run written before its gate, which would un-withhold its figures", () => {
    const offered = offeredAnalyses(listed.map((id) => ({ id }))).map((row) => row.id);
    expect(offered).toEqual([DEFAULT_ANALYSIS, SAM_ANALYSIS, "ref-ngsim-int2-full", "cv-job-1"]);
  });

  it("names each run for a person, and keeps a job's window", () => {
    expect(analysisName({ id: DEFAULT_ANALYSIS, title: "CairoFlow CV, source 120-1020 s, checked", mode: "precomputed" })).toBe(
      "15-minute study (checked)",
    );
    expect(analysisName({ id: "cv-job-1", title: "CairoFlow CV, source 970-980 s", mode: "live" })).toBe("Live run, source 970-980 s");
  });
});

describe("entering flow on a run with no passed gate", () => {
  it("is what was counted in the window, never an hourly rate", () => {
    const flow = metric("flow.entering", 4980);
    expect(windowCount(record([flow, ...armCounts]), flow)).toEqual({ count: 83, seconds: 60 });
  });

  it("keeps the rate once a gate has passed it", () => {
    const flow = metric("flow.entering", 4980, "gate_pass");
    expect(windowCount(record([flow, ...armCounts]), flow)).toBeNull();
  });

  it("says nothing for a figure that is absent", () => {
    const flow = metric("flow.entering", null, "gate_fail");
    expect(windowCount(record([flow, ...armCounts]), flow)).toBeNull();
  });
});

describe("plain words over record fields", () => {
  it("states the withheld speed without an ADR id or a homography", () => {
    const words = plainReason("withheld: speed needs along-road distance, which is not accepted from this homography (ADR-018)");
    expect(words).toBe("Withheld: this camera setup cannot measure distance along the road reliably.");
  });

  it("keeps all four recorded places of a near miss", () => {
    expect(gateReading("entering_count_error_share", 0.2033)).toBe("20.33%");
    expect(gateLimit("entering_count_error_share", 0.2)).toBe("20%");
    expect(gateReading("across_heading_p90_m", 1.3891)).toBe("1.39 m");
    expect(gateLimit("speed_abs_error_median_m_s", 1)).toBe("1 m/s");
  });

  it("writes out a raw failure line and still shows the exact value", () => {
    const line = "entering_count_error_share failed: 0.2033 against a limit of <= 0.2. The limit was fixed before the read.";
    expect(recordWords(line)).toBe(
      "Entering count error failed: 20.33% (0.2033) against a limit of 20%. The limit was fixed before the read.",
    );
  });

  it("drops an ADR id only where it is asked to", () => {
    const line = "Along-road distances are not accepted (ADR-018).";
    expect(recordWords(line)).toContain("ADR-018");
    expect(recordWords(line, { hideIds: true })).toBe("Along-road distances are not accepted.");
  });

  it("describes the placement gate as a reader would", () => {
    const gate = { metric: "across_heading_p90_m", measured: 1.3891, passed: true };
    expect(placementWords(gate as never)).toBe("Sideways position error: 9 in 10 points within 1.39 m of the published track");
  });

  it("reads fragments from the record's own sentence", () => {
    const disclosures = ["8.0% of track points are interpolated; 5 tracks shorter than 1 s are fragments, kept in the files."];
    expect(fragmentCount({ disclosures } as unknown as AnalysisRecord)).toBe(5);
    expect(fragmentCount({ disclosures: [] } as unknown as AnalysisRecord)).toBeNull();
  });
});

describe("turning counts beside entering counts", () => {
  const gate = (name: string, measured: number, threshold: number, passed: boolean) => ({ metric: name, measured, threshold, passed });
  const split = {
    gates: [gate("entering_count_error_share", 0.2033, 0.2, false), gate("turning_matrix_agreement", 0.847, 0.8, true)],
    disclosures: ["159 other counted tracks began mid-view (E 5, N 97, S 52, W 5)."],
  } as unknown as AnalysisRecord;

  it("explains why one passed and the other failed, and that rows are not entering counts", () => {
    const note = countsNote(split);
    expect(note).toContain("different checks");
    expect(note).toContain("159 tracks that began mid-view");
    expect(note).toContain("84.70% agreement, limit 80%");
    expect(note).toContain("not entering counts");
  });

  it("says nothing when the two agree", () => {
    expect(countsNote({ gates: [], disclosures: [] } as unknown as AnalysisRecord)).toBeNull();
  });
});

const decision = {
  outcome: { recommended_intervention_id: null, recommendation: "Nothing is recommended." },
  baseline: { status: "partially_validated" },
  simulation: { run_id: "run-1", seeds: [1, 2] },
  hypotheses: [],
  candidates: [],
  metrics: [
    { candidate: "I1", metric_name: "mean_delay_s", unit: "s", baseline_mean: 25.76, candidate_mean: 25.0, mean_difference: -0.75 },
    { candidate: "I1", metric_name: "max_queue_m.E", unit: "m", baseline_mean: 13.03, candidate_mean: 24.74, mean_difference: 11.7 },
    { candidate: "I1", metric_name: "throughput_veh_per_h", unit: "veh/h", baseline_mean: 3787.96, candidate_mean: 3768.91, mean_difference: -19 },
  ],
  ensemble: {
    members: [
      { member: "T1", required: true, gate_passed: true },
      { member: "T2", required: true, gate_passed: true },
    ],
    candidates: [
      {
        candidate: "I1",
        verdict: "no_support",
        robust_harm: ["max_queue_m.E", "throughput_veh_per_h"],
        common_confirmed: ["mean_queue_m.S"],
        members: [
          { member: "T1", mean_delay_difference_s: 5.4 },
          { member: "T2", mean_delay_difference_s: -0.75 },
        ],
      },
    ],
  },
} as unknown as DecisionSummary;

describe("the provenance chain's two branches", () => {
  const stages = readChain({ record: null, decision, site: null });

  it("never runs one line from a camera stage to the decision", () => {
    expect(CHAIN_BRANCHES.map(({ label }) => label)).toEqual(["Camera analysis", "Published NGSIM trajectories"]);
    expect(stages.filter((stage) => stage.branch === "camera").map((stage) => stage.id)).toEqual([
      "source_record",
      "observation",
      "world_entity",
    ]);
    expect(stages.find((stage) => stage.id === "decision_report")?.branch).toBe("published");
  });

  it("gives every stage somewhere to read its evidence", () => {
    for (const stage of stages) expect(stage.to).toMatch(/^\/[a-z]+#[a-z-]+$/);
  });

  it("names what would let it recommend", () => {
    expect(whatWouldChange(decision)).toContain("assessed as validated");
    expect(whatWouldChange(decision)).toContain("on all 2 signal timelines");
  });
});

describe("the decision beside a run", () => {
  const camera = { source: { uploaded_by_user: false } } as unknown as AnalysisRecord;
  const upload = { source: { uploaded_by_user: true } } as unknown as AnalysisRecord;

  it("stands beside a run at this site that names none, and not beside an upload", () => {
    expect(decisionFor(camera, decision)).toBe(decision);
    expect(decisionFor(upload, decision)).toBeNull();
  });
});

describe("what the simulator found", () => {
  it("names each measure and gives the record's own figures", () => {
    expect(measureName("max_queue_m.E")).toBe("Longest queue from east");
    expect(measureName("mean_queue_m.W")).toBe("Mean queue from west");
    const [finding] = candidateFindings(decision);
    expect(finding?.worse[0]).toEqual({ metric: "max_queue_m.E", name: "Longest queue from east", change: "13.0 to 24.7 m" });
    expect(finding?.worse[1]?.change).toBe("3,788 to 3,769 veh/h");
    expect(finding?.better[0]?.change).toBeNull();
    expect(finding?.delays).toEqual([
      { member: "T1", differenceS: 5.4 },
      { member: "T2", differenceS: -0.75 },
    ]);
  });

  it("finds which timeline the per-metric figures came from", () => {
    expect(comparedTimeline(decision)).toBe("T2");
  });
});
