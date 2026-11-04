#!/usr/bin/env node
// Replaces frames of the full render with a re-rendered range, so one scene can be fixed without
// rendering the whole cut again:
//
//   node video/scripts/render.mjs --frames=4140-4559 --out=video/out/patch.mp4
//   node video/scripts/splice.mjs video/out/patch.mp4 4140
//
// The patch covers frames [start, start + its length) of out/cairoflow-demo.mp4. The result is
// re-encoded with the same settings (H.264, yuv420p, CRF 18, 30 fps) and replaces the original.
import { spawnSync } from "node:child_process";
import { renameSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const [patch, startArg, baseArg] = process.argv.slice(2);
if (!patch || startArg === undefined) {
  console.error("usage: splice.mjs <patch.mp4> <start frame> [base.mp4]");
  process.exit(2);
}
const base = baseArg ? resolve(baseArg) : join(ROOT, "out", "cairoflow-demo.mp4");
const start = Number(startArg);
const count = Number(
  spawnSync("ffprobe", ["-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", patch], {
    encoding: "utf8",
  }).stdout.trim(),
);
const end = start + count;
const tmp = base.replace(/\.mp4$/, ".spliced.mp4");
const filter =
  `[0:v]trim=start_frame=0:end_frame=${start},setpts=PTS-STARTPTS[a];` +
  `[1:v]setpts=PTS-STARTPTS[b];` +
  `[0:v]trim=start_frame=${end},setpts=PTS-STARTPTS[c];` +
  `[a][b][c]concat=n=3:v=1:a=0,fps=30,format=yuv420p[v]`;
const r = spawnSync(
  "ffmpeg",
  ["-y", "-loglevel", "error", "-i", base, "-i", resolve(patch), "-filter_complex", filter, "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-movflags", "+faststart", tmp],
  { stdio: "inherit" },
);
if (r.status !== 0) process.exit(r.status ?? 1);
renameSync(tmp, base);
console.log(`spliced ${count} frames at ${start} into ${base}`);
