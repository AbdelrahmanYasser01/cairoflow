#!/usr/bin/env node
// One frame every 6 s of out/cairoflow-demo.mp4, tiled 4 across, into out/contact-sheet.png.
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const input = process.argv[2] || join(ROOT, "out", "cairoflow-demo.mp4");
const output = process.argv[3] || join(ROOT, "out", "contact-sheet.png");
const every = process.env.SHEET_EVERY || "6";
const r = spawnSync(
  "ffmpeg",
  ["-y", "-loglevel", "error", "-i", input, "-vf", `fps=1/${every},scale=480:-1,tile=4x7:padding=4:color=0x0a0c0e`, "-frames:v", "1", output],
  { stdio: "inherit" },
);
if (r.status === 0) console.log(`wrote ${output}`);
process.exit(r.status ?? 1);
