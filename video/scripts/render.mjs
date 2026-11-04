#!/usr/bin/env node
// Renders the edit to video/out/cairoflow-demo.mp4 (1920x1080, 30 fps, H.264, yuv420p).
//
//   npm --prefix video run render            full quality
//   npm --prefix video run render:draft      faster: half-size frames, same length (check only)
//   node video/scripts/render.mjs --frames=0-299         one range
//   node video/scripts/render.mjs --stills=150,2400      PNG stills into out/stills/
//   node video/scripts/render.mjs --bundle               bundle once into out/bundle (public/ included)
//   node video/scripts/render.mjs --entry=out/bundle --frames=720-1061 --out=out/p1.mp4
//                                                        render from that bundle, no re-bundling
//
// It reads public/captures/manifest.json (written by `npm run capture`) and any Higgsfield shots
// dropped in public/slots/h1.mp4 and public/slots/h2.mp4, and passes them in as props. It uses the
// preinstalled headless Chromium when one is found (CHROME_PATH, or PLAYWRIGHT_BROWSERS_PATH) so
// Remotion never downloads a browser; otherwise Remotion uses its own.

import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const OUT = join(ROOT, "out");
const args = process.argv.slice(2);
const flag = (name) => args.find((a) => a === `--${name}` || a.startsWith(`--${name}=`));
const value = (name) => flag(name)?.split("=")[1];
mkdirSync(OUT, { recursive: true });

function headlessShell() {
  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || "/opt/pw-browsers";
  if (!existsSync(root)) return null;
  for (const dir of readdirSync(root).filter((d) => d.startsWith("chromium_headless_shell-")).sort().reverse()) {
    for (const sub of readdirSync(join(root, dir))) {
      const exe = join(root, dir, sub, "chrome-headless-shell");
      if (existsSync(exe)) return exe;
    }
  }
  return null;
}

const captures = {};
const manifestPath = join(ROOT, "public", "captures", "manifest.json");
if (existsSync(manifestPath)) {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  for (const [name, clip] of Object.entries(manifest.clips || {})) {
    if (existsSync(join(ROOT, "public", clip.src))) captures[name] = { src: clip.src, durationInFrames: clip.durationInFrames };
  }
}
const slots = {};
for (const id of ["h1", "h2"]) if (existsSync(join(ROOT, "public", "slots", `${id}.mp4`))) slots[id] = `slots/${id}.mp4`;
// The annotated NGSIM footage, rendered locally (see docs/VIDEO.md), replaces the Vision Lab clip.
const footage = existsSync(join(ROOT, "public", "footage", "annotated.mp4")) ? "footage/annotated.mp4" : undefined;
const props = {
  captures,
  slots,
  ...(footage ? { footage } : {}),
  showSlotLabels: !flag("final"),
  captions: !flag("no-captions"),
};
const propsPath = join(OUT, "props.json");
writeFileSync(propsPath, JSON.stringify(props, null, 2));
console.log(
  `[render] captures: ${Object.keys(captures).join(", ") || "none"}; slots: ${Object.keys(slots).join(", ") || "none"}; footage: ${footage ? "yes" : "no"}`,
);

// --entry=<bundle dir> renders from a folder made by --bundle, which skips the bundling step; the
// bundle holds its own copy of public/, so bundle again after a re-capture.
const entry = value("entry") ? resolve(value("entry")) : "src/index.ts";
if (flag("bundle")) {
  const cliBundle = join(ROOT, "node_modules", ".bin", "remotion");
  const out = join(OUT, "bundle");
  console.log(`[render] remotion bundle src/index.ts --out-dir=${out}`);
  const r = spawnSync(cliBundle, ["bundle", "src/index.ts", `--out-dir=${out}`, "--log=warn"], { cwd: ROOT, stdio: "inherit" });
  process.exit(r.status ?? 1);
}

const browser = headlessShell();
const common = [`--props=${propsPath}`, ...(browser ? [`--browser-executable=${browser}`] : []), "--log=warn"];
const cli = join(ROOT, "node_modules", ".bin", "remotion");
const run = (cmd) => {
  console.log(`[render] remotion ${cmd.join(" ")}`);
  const r = spawnSync(cli, cmd, { cwd: ROOT, stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status ?? 1);
};

if (flag("stills")) {
  mkdirSync(join(OUT, "stills"), { recursive: true });
  for (const f of value("stills").split(",")) {
    run(["still", entry, "CairoFlowDemo", join(OUT, "stills", `f${f.padStart(5, "0")}.png`), `--frame=${f}`, ...common]);
  }
  process.exit(0);
}

const target = value("out") ? resolve(value("out")) : join(OUT, "cairoflow-demo.mp4");
const extra = [];
if (flag("draft")) extra.push("--scale=0.5", "--crf=26");
if (flag("frames")) extra.push(`--frames=${value("frames")}`);
const concurrency = value("concurrency") || process.env.REMOTION_CONCURRENCY || "50%";
run([
  "render", entry, "CairoFlowDemo", target,
  "--codec=h264", "--pixel-format=yuv420p", "--crf=18", "--image-format=jpeg", "--jpeg-quality=92",
  `--concurrency=${concurrency}`, ...extra, ...common,
]);

if (!value("out") && !flag("frames")) {
  const review = process.env.REVIEW_DIR || "/tmp/claude-0/review/video";
  try {
    mkdirSync(review, { recursive: true });
    copyFileSync(target, join(review, "cairoflow-demo.mp4"));
    console.log(`[render] copied to ${join(review, "cairoflow-demo.mp4")}`);
  } catch {
    /* the review folder is optional */
  }
}
console.log(`[render] wrote ${target}`);
