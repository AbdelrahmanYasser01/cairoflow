#!/usr/bin/env node
// Replaces frames of the full render with re-rendered ranges, so a scene can be fixed without
// rendering the whole cut again:
//
//   node video/scripts/render.mjs --frames=4200-4559 --out=video/out/patch.mp4
//   node video/scripts/splice.mjs video/out/patch.mp4 4200 [another.mp4 720 ...]
//
// Each patch covers frames [start, start + its length) of out/cairoflow-demo.mp4 (or BASE=...).
// The result is re-encoded once with the render's settings (H.264, yuv420p, CRF 18, 30 fps) and
// replaces the original.
import { spawnSync } from "node:child_process";
import { renameSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
if (args.length < 2 || args.length % 2 !== 0) {
  console.error("usage: splice.mjs <patch.mp4> <start frame> [<patch.mp4> <start frame> ...]");
  process.exit(2);
}
const base = process.env.BASE ? resolve(process.env.BASE) : join(ROOT, "out", "cairoflow-demo.mp4");
// ffprobe can print "600," (a trailing empty field) for some encodes, so parse the leading integer.
const frames = (file) =>
  Number.parseInt(
    spawnSync("ffprobe", ["-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", file], {
      encoding: "utf8",
    }).stdout.trim(),
    10,
  );
const patches = [];
for (let i = 0; i < args.length; i += 2) {
  const file = resolve(args[i]);
  const start = Number(args[i + 1]);
  patches.push({ file, start, end: start + frames(file) });
}
patches.sort((a, b) => a.start - b.start);
const total = frames(base);

// Segments in order: base gaps and patches.
const inputs = ["-i", base];
const parts = [];
let cursor = 0;
patches.forEach((p, k) => {
  parts.push({ kind: "base", from: cursor, to: p.start });
  parts.push({ kind: "patch", index: k + 1 });
  inputs.push("-i", p.file);
  cursor = p.end;
});
parts.push({ kind: "base", from: cursor, to: total });
const baseParts = parts.filter((p) => p.kind === "base" && p.to > p.from);
let filter = `[0:v]split=${baseParts.length}${baseParts.map((_, i) => `[s${i}]`).join("")};`;
const labels = [];
let b = 0;
parts.forEach((p, i) => {
  if (p.kind === "base") {
    if (p.to <= p.from) return;
    filter += `[s${b}]trim=start_frame=${p.from}:end_frame=${p.to},setpts=PTS-STARTPTS[p${i}];`;
    b += 1;
  } else {
    filter += `[${p.index}:v]setpts=PTS-STARTPTS[p${i}];`;
  }
  labels.push(`[p${i}]`);
});
filter += `${labels.join("")}concat=n=${labels.length}:v=1:a=0,fps=30,format=yuv420p[v]`;
const tmp = base.replace(/\.mp4$/, ".spliced.mp4");
const r = spawnSync(
  "ffmpeg",
  ["-y", "-loglevel", "error", ...inputs, "-filter_complex", filter, "-map", "[v]", "-c:v", "libx264", "-preset", process.env.SPLICE_PRESET || "medium", "-crf", "18", "-movflags", "+faststart", tmp],
  { stdio: "inherit" },
);
if (r.status !== 0) process.exit(r.status ?? 1);
renameSync(tmp, base);
console.log(`spliced ${patches.map((p) => `${p.start}-${p.end - 1}`).join(", ")} into ${base} (${total} frames)`);
