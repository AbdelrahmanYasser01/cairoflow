import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The analytics instruments keep the same rules as pages and instruments: no metric typed in as a
 * literal, no evidence kind for a count written as a literal, and no reference count standing in
 * for a withheld value.
 */
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

function hardCodedMetrics(source: string): string[] {
  const text = claims(source);
  const found: string[] = [];
  const add = (pattern: RegExp) => {
    for (const match of text.matchAll(pattern)) found.push(match[0].trim());
  };
  add(new RegExp(String.raw`<Numeral\b[^>]*\bvalue=\{\s*${NUMBER}\s*\}`, "g"));
  add(new RegExp(String.raw`\bvalue:\s*${NUMBER}\b`, "g"));
  add(new RegExp(String.raw`["'\`>][^"'\`<>{}]*\b${NUMBER}\s*${UNIT}`, "g"));
  return found;
}

const files = readdirSync(__dirname)
  .filter((name) => name.endsWith(".tsx"))
  .map((name) => join(__dirname, name));

describe("analytics instruments", () => {
  it.each(files)("type no metric into %s", (path) => {
    expect(hardCodedMetrics(readFileSync(path, "utf8"))).toEqual([]);
  });

  it("take a count's evidence kind from the record", () => {
    for (const path of files) expect(claims(readFileSync(path, "utf8"))).not.toMatch(/kind="observed"/);
  });

  it("never show a reference count in a withheld value's place", () => {
    for (const name of ["Kpis.tsx", "WithheldBand.tsx"]) {
      expect(readFileSync(join(__dirname, name), "utf8")).not.toMatch(/reference_count/);
    }
  });
});
