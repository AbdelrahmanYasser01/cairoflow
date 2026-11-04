import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { AnalysisRecord, AnalysisSummary } from "../lib/analysis";
import { DEFAULT_ANALYSIS, orderAnalyses } from "../state/analysis";
import { measuredCoverage } from "./footage";
import { comparatorWord, figuresWithheld, gateName, gatesPassed, limitWords, measuredWords, withheldBy } from "./gates";
import { findMetric } from "./metrics";

/** The record the product ships with, so these hold against what is really on disk. */
const shipped = JSON.parse(
  readFileSync(
    resolve(__dirname, `../../../../data/sample/analyses/${DEFAULT_ANALYSIS}/record.json`),
    "utf8",
  ),
) as AnalysisRecord;

describe("the shipped study", () => {
  const flow = findMetric(shipped, "flow.entering");

  it("withholds entering flow, with the check that stopped it", () => {
    const withheld = withheldBy(shipped, flow ?? {});
    expect(flow?.value).toBeNull();
    expect(withheld?.check?.metric).toBe("entering_count_error_share");
    expect(withheld?.versus).toBe("20.33% count error, limit 20%");
    expect(withheld?.gateId).toBe("cv-int2-v2");
  });

  it("says the limit came first, in the record's own words", () => {
    expect(withheldBy(shipped, flow ?? {})?.limitNote).toContain("fixed before the read");
  });

  it("holds back every series the check covers", () => {
    for (const series of shipped.series ?? []) {
      expect(series.values.every((value) => value === null)).toBe(true);
      expect(withheldBy(shipped, series)?.versus).toBe("20.33% count error, limit 20%");
    }
  });

  it("leaves a figure alone when it passed, or when no gate covers it", () => {
    expect(withheldBy(shipped, findMetric(shipped, "speed.mean_moving") ?? {})).toBeNull();
    expect(withheldBy(shipped, findMetric(shipped, "vehicles.total") ?? {})).toBeNull();
  });

  it("counts its checks and names what the failed one holds back", () => {
    expect(gatesPassed(shipped)).toEqual({ passed: 6, total: 7 });
    expect(figuresWithheld(shipped)).toEqual([
      "Entering flow",
      "Entering from N",
      "Entering from E",
      "Entering from S",
      "Entering from W",
    ]);
  });

  it("is only partly inside the windows a gate measured", () => {
    expect(measuredCoverage(shipped)?.label).toBe("Placement checked on 450 of 900 s");
  });
});

describe("gate words", () => {
  it("shows a measured value whole, because the fourth place is what failed", () => {
    expect(measuredWords(0.2033)).toBe("0.2033");
    expect(limitWords(0.2)).toBe("0.20");
  });

  it("reads a comparator and names a check nobody has listed yet", () => {
    expect(comparatorWord("<=")).toBe("at most");
    expect(comparatorWord(">=")).toBe("at least");
    expect(gateName("entering_count_error_share")).toBe("Entering count error");
    expect(gateName("new_check")).toBe("New check");
  });

  it("has nothing to say about a figure that did not fail", () => {
    expect(withheldBy(shipped, { status: "gate_pass", gate_id: "cv-int2-v2" })).toBeNull();
    expect(withheldBy(shipped, { status: "ungated" })).toBeNull();
  });
});

describe("analysis order", () => {
  const row = (id: string, origin: AnalysisSummary["data_origin"]) => ({ id, data_origin: origin }) as AnalysisSummary;

  it("opens on the shipped study, puts camera runs next and reference data last", () => {
    const listed = [row("ref-a", "reference_dataset"), row("cv-live", "camera_cv"), row(DEFAULT_ANALYSIS, "camera_cv")];
    expect(orderAnalyses(listed).map((item) => item.id)).toEqual([DEFAULT_ANALYSIS, "cv-live", "ref-a"]);
  });
});
