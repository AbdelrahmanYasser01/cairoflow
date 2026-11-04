#!/usr/bin/env node
// Copies the real data layer the edit draws from the committed sample analyses into
// video/src/generated/, reshaped for drawing (grouped by frame, pixels rounded to whole numbers):
//   sam-masks.json    SAM 3.1 mask outlines, 60 s SAM window (data/sample/.../sam_outlines.json)
// Run from anywhere: node video/scripts/prepare-data.mjs

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const OUT = resolve(HERE, "..", "src", "generated");
mkdirSync(OUT, { recursive: true });

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
console.log(`sam: ${frames.length} frames, ${sam.entries.length} outlines, bounds ${[minX, minY, maxX, maxY]}`);
