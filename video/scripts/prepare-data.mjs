#!/usr/bin/env node
// Copies the two real data layers the edit draws from the committed sample analyses into
// video/src/generated/, reshaped for drawing but with every value unchanged:
//   density.json      vehicle-frames per 2 m cell, 15-minute study (data/sample/.../density.json)
//   sam-masks.json    SAM 3.1 mask outlines, 60 s SAM window (data/sample/.../sam_outlines.json)
// Run from anywhere: node video/scripts/prepare-data.mjs

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const OUT = resolve(HERE, "..", "src", "generated");
mkdirSync(OUT, { recursive: true });

const densitySrc = join(REPO, "data/sample/analyses/cv-ngsim-int2-15min-study/density.json");
const density = JSON.parse(readFileSync(densitySrc, "utf8"));
const cells = [];
let max = 0;
density.counts.forEach((row, i) =>
  row.forEach((v, j) => {
    if (v > 0) {
      cells.push([i, j, v]);
      max = Math.max(max, v);
    }
  }),
);
writeFileSync(
  join(OUT, "density.json"),
  JSON.stringify({
    source: "data/sample/analyses/cv-ngsim-int2-15min-study/density.json",
    note: "counts[i][j] as stored; cells listed only where the count is above zero",
    evidence_kind: density.evidence_kind,
    value: density.value,
    cell_size: density.cell_size,
    nx: density.nx,
    ny: density.ny,
    max,
    cells,
  }),
);

const samSrc = join(REPO, "data/sample/analyses/cv-hero-sam/sam_outlines.json");
const sam = JSON.parse(readFileSync(samSrc, "utf8"));
const byFrame = new Map();
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
for (const entry of sam.entries) {
  if (!byFrame.has(entry.frame)) byFrame.set(entry.frame, []);
  byFrame.get(entry.frame).push([entry.track_id, entry.polygon.map(([x, y]) => [Math.round(x), Math.round(y)])]);
  for (const [x, y] of entry.polygon) {
    minX = Math.min(minX, x); minY = Math.min(minY, y);
    maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
  }
}
const frames = [...byFrame.keys()].sort((a, b) => a - b).map((f) => ({ frame: f, masks: byFrame.get(f) }));
writeFileSync(
  join(OUT, "sam-masks.json"),
  JSON.stringify({
    source: "data/sample/analyses/cv-hero-sam/sam_outlines.json",
    label: sam.label,
    coordinates: sam.coordinates,
    every_frames: sam.every_frames,
    bounds: [minX, minY, maxX, maxY],
    tracks: new Set(sam.entries.map((e) => e.track_id)).size,
    frames,
  }),
);
console.log(`density: ${cells.length} cells, max ${max}; sam: ${frames.length} frames, ${sam.entries.length} outlines, bounds ${[minX, minY, maxX, maxY]}`);
