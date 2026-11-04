import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Two standing rules, checked on the source of every page and instrument: a metric on screen comes
 * from a record and never from a literal, and sentences found false in the truth audit stay gone.
 * A static check, so it reads the code a person would read. Styling and paths are removed first,
 * because a class such as `w-[30%]` or an SVG path is not a claim.
 */
const src = resolve(__dirname, "..");
const SCANNED = ["pages", "instruments"];

const UNIT = String.raw`(?:veh\/h|veh\b|vehicles?\b|tracks?\b|detections?\b|fps\b|m\/s\b|km\/h\b|mph\b|seeds?\b|%|percent\b)`;
const NUMBER = String.raw`\d[\d,]*(?:\.\d+)?`;

function claims(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/^import .*$/gm, "")
    .replace(/className=(?:"[^"]*"|\{`[^`]*`\})/g, "")
    .replace(/style=\{\{[^}]*\}\}/g, "")
    .replace(/\bd="[^"]*"/g, "");
}

/** Each way a typed-in number has looked in this code base, or could. */
function hardCodedMetrics(source: string): string[] {
  const text = claims(source);
  const found: string[] = [];
  const add = (pattern: RegExp) => {
    for (const match of text.matchAll(pattern)) found.push(match[0].trim());
  };
  add(new RegExp(String.raw`<Numeral\b[^>]*\bvalue=\{\s*${NUMBER}\s*\}`, "g"));
  add(new RegExp(String.raw`<Field\b[^>]*\bvalue=(?:"[^"]*\d[^"]*"|\{\s*${NUMBER}\s*\})`, "g"));
  add(new RegExp(String.raw`\bvalue:\s*${NUMBER}\b`, "g"));
  add(new RegExp(String.raw`["'\`>][^"'\`<>{}]*\b${NUMBER}\s*${UNIT}`, "g"));
  return found;
}

function scannedFiles(): string[] {
  return SCANNED.flatMap((folder) =>
    readdirSync(join(src, folder))
      .filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.tsx"))
      .map((name) => join(src, folder, name)),
  );
}

describe("no decorative metrics", () => {
  it("catches a number typed into each place a metric appears", () => {
    expect(hardCodedMetrics(`<Numeral value={4980} unit="veh/h" />`)).toHaveLength(1);
    expect(hardCodedMetrics(`<Field label="Flow" value="4,980 veh/h" />`)).not.toHaveLength(0);
    expect(hardCodedMetrics(`<Field label="Flow" value={12} />`)).toHaveLength(1);
    expect(hardCodedMetrics(`const row = { label: "Flow", value: 4980 };`)).toHaveLength(1);
    expect(hardCodedMetrics(`<p>102 vehicles entered</p>`)).toHaveLength(1);
    expect(hardCodedMetrics(`<Label>Mean speed 10.25 m/s</Label>`)).toHaveLength(1);
    expect(hardCodedMetrics(`const note = "12 tracks, 30.5 fps";`)).not.toHaveLength(0);
  });

  it("leaves alone what is not a claim", () => {
    expect(hardCodedMetrics(`<Numeral value={value} unit={unit} />`)).toEqual([]);
    expect(hardCodedMetrics(`<Field label="Flow" value={metric.value} />`)).toEqual([]);
    expect(hardCodedMetrics(`<div className="w-[30%] h-6 grid-cols-7" />`)).toEqual([]);
    expect(hardCodedMetrics(`<path d="M3 2h1.5v10H3z" />`)).toEqual([]);
    expect(hardCodedMetrics(`/** Shown at 25 vehicles. */\nconst a = 1;`)).toEqual([]);
    expect(hardCodedMetrics("const label = `${tracks.length} tracks`;")).toEqual([]);
  });

  it.each(scannedFiles())("holds in %s", (path) => {
    expect(hardCodedMetrics(readFileSync(path, "utf8"))).toEqual([]);
  });

  it("takes a count's evidence kind from the record, never from a literal", () => {
    for (const path of scannedFiles()) {
      expect(claims(readFileSync(path, "utf8"))).not.toMatch(/kind="observed"/);
    }
  });
});

describe("the truth audit", () => {
  const copy = scannedFiles()
    .concat(join(src, "domain/chain.ts"), join(src, "twin/panels.tsx"))
    .map((path) => claims(readFileSync(path, "utf8")))
    .join("\n");

  it.each([
    ["a recommendation is not a proof", /cannot prove/],
    ["code holds the rule, but a model does propose candidates", /model cannot move a decision/],
    ["counts are by track origin, so no line counts anything", /[Cc]ounting lines?/],
    ["uploads are accepted with their licence unverified", /needs a recorded license/],
    ["a placement check measured a window, it did not gate this frame", /Outside the window a gate measured/],
  ])("does not say what it cannot back: %s", (_why, pattern) => {
    expect(copy).not.toMatch(pattern);
  });

  it("puts no street name on the assumed road", () => {
    const plan = readFileSync(join(src, "pages/CommandCenter.tsx"), "utf8");
    expect(plan).not.toMatch(/labels=\{/);
  });

  it("never offers a reference count in the place of a withheld value", () => {
    for (const name of ["pages/CommandCenter.tsx", "instruments/Readouts.tsx", "instruments/Withheld.tsx"]) {
      expect(readFileSync(join(src, name), "utf8")).not.toMatch(/reference_count/);
    }
  });
});
