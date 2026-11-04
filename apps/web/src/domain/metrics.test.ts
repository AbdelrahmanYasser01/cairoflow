import { describe, expect, it } from "vitest";
import type { AnalysisRecord } from "../lib/analysis";
import { drawnKinds } from "../twin/panels";
import { absentWords } from "./metrics";
import { siteNameStatusLabel } from "./site";

describe("a reason for an absent value", () => {
  it("reads as a sentence, without the record's prefix", () => {
    expect(absentWords("not available: this analysis is uncalibrated, so tracks have no arms")).toBe(
      "This analysis is uncalibrated, so tracks have no arms.",
    );
  });

  it("keeps a reason of another kind whole", () => {
    expect(absentWords("withheld: speed needs along-road distance")).toBe("Withheld: speed needs along-road distance.");
  });

  it("says so when the record gives none", () => {
    expect(absentWords(null)).toBe("Not recorded.");
    expect(absentWords(undefined)).toBe("Not recorded.");
  });
});

describe("the evidence a twin scene draws", () => {
  const origin = (data_origin: string) => ({ data_origin }) as AnalysisRecord;

  it("never offers a switch for a kind the scene has nothing of", () => {
    for (const data_origin of ["camera_cv", "reference_dataset"]) {
      expect(drawnKinds(origin(data_origin))).not.toContain("model_generated");
      expect(drawnKinds(origin(data_origin))).not.toContain("simulated");
    }
  });

  it("offers observed positions only for published ones", () => {
    expect(drawnKinds(origin("reference_dataset"))).toContain("observed");
    expect(drawnKinds(origin("camera_cv"))).not.toContain("observed");
  });
});

describe("the site name's status", () => {
  it("is the record's own word, as a reader sees it", () => {
    expect(siteNameStatusLabel("supported_hypothesis")).toBe("Inferred from a present-day map");
  });
});
