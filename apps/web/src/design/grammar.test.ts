import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { EVIDENCE, EVIDENCE_KINDS, STATUS, STATUSES } from "./grammar";

const root = resolve(__dirname, "../..");

describe("evidence grammar", () => {
  it("matches the EvidenceKind literal in the contracts", () => {
    const source = readFileSync(
      resolve(root, "../../packages/contracts/cairoflow_contracts/evidence.py"),
      "utf8",
    );
    const literal = /EvidenceKind = Literal\[([^\]]+)\]/.exec(source)?.[1] ?? "";
    const kinds = [...literal.matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
    expect(kinds).toEqual([...EVIDENCE_KINDS]);
  });

  it("gives every kind its own shape, colour and label", () => {
    const specs = EVIDENCE_KINDS.map((kind) => EVIDENCE[kind]);
    for (const field of ["shape", "colour", "label"] as const) {
      expect(new Set(specs.map((spec) => spec[field])).size).toBe(EVIDENCE_KINDS.length);
    }
  });
});

describe("status marks", () => {
  it("leaves the signal head dark when nothing is available", () => {
    expect(STATUS.not_available.lamp).toBeNull();
  });

  it("lights a different lamp for every status that has one", () => {
    const lamps = STATUSES.map((status) => STATUS[status].lamp).filter(Boolean);
    expect(new Set(lamps).size).toBe(lamps.length);
  });
});

describe("build constraints", () => {
  it("keeps index.html free of inline scripts and remote URLs, so script-src 'self' holds", () => {
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
    expect(html).not.toMatch(/https?:\/\//);
  });
});
