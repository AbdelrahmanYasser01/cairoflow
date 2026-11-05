#!/usr/bin/env node
// Records 1920x1080 clips of the real CairoFlow app for the demo edit.
//
//   npm --prefix video run capture                 every clip, against http://127.0.0.1:8105
//   CAPTURE_URL=http://127.0.0.1:8000 npm --prefix video run capture
//   npm --prefix video run capture -- --only=twin,command
//
// It needs a server that is already running (bash scripts/demo_run.sh) on a fresh build of
// apps/web. Frames come from the Chrome DevTools screencast as high-quality JPEGs with their own
// timestamps, so text stays crisp and motion keeps real time; ffmpeg turns each clip into H.264,
// yuv420p, 30 fps CFR in video/public/captures/<name>.mp4, plus manifest.json for the renderer.
// Every step looks controls up by their accessible name and carries on when one is missing, so a
// redesigned UI still yields a clip (the log says which steps were skipped).

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const VIDEO_ROOT = resolve(HERE, "..");
// CAPTURE_OUT and CAPTURE_WORK redirect the clips and their frames, e.g. for a trial run while a
// render is reading public/captures.
const OUT_DIR = process.env.CAPTURE_OUT ? resolve(process.env.CAPTURE_OUT) : join(VIDEO_ROOT, "public", "captures");
const WORK_DIR = process.env.CAPTURE_WORK ? resolve(process.env.CAPTURE_WORK) : join(VIDEO_ROOT, "out", "capture-frames");
const BASE = (process.env.CAPTURE_URL || "http://127.0.0.1:8105").replace(/\/$/, "");
const WIDTH = 1920;
const HEIGHT = 1080;
const FPS = 30;

const args = process.argv.slice(2);
const onlyArg = args.find((a) => a.startsWith("--only="));
const ONLY = onlyArg ? new Set(onlyArg.slice(7).split(",").filter(Boolean)) : null;
// --screencast records the 2D pages in real time instead of frame-stepping them (faster, but its
// frame rate drops when the machine is busy).
const SCREENCAST = args.includes("--screencast");

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    // The sandbox has a global copy on NODE_PATH; ESM import ignores NODE_PATH, require does not.
    const require = createRequire(import.meta.url);
    return require("playwright");
  }
}

function findHeadlessShell() {
  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || "/opt/pw-browsers";
  if (!existsSync(root)) return undefined;
  for (const dir of readdirSync(root).filter((d) => d.startsWith("chromium_headless_shell-")).sort().reverse()) {
    const base = join(root, dir);
    for (const sub of readdirSync(base)) {
      const exe = join(base, sub, "chrome-headless-shell");
      if (existsSync(exe)) return exe;
    }
  }
  return undefined;
}

const log = (...m) => console.log("[capture]", ...m);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Installed in every page: finds the element that actually scrolls. */
function installScrollerHelper() {
  window.__cfScroller = () => {
    const doc = document.scrollingElement || document.documentElement;
    let best = doc;
    let room = doc.scrollHeight - doc.clientHeight;
    for (const el of document.querySelectorAll("*")) {
      const style = getComputedStyle(el);
      if (!/(auto|scroll)/.test(style.overflowY)) continue;
      const r = el.scrollHeight - el.clientHeight;
      if (r > room + 40 && el.clientHeight > 300) {
        best = el;
        room = r;
      }
    }
    return best;
  };
}

/** Click the first control whose accessible name matches, trying radios, buttons and links. */
async function press(page, name, { timeout = 2500 } = {}) {
  for (const role of ["radio", "button", "tab", "link", "checkbox", "switch"]) {
    const loc = page.getByRole(role, { name, exact: false }).first();
    if ((await loc.count().catch(() => 0)) === 0) continue;
    if ((await loc.getAttribute("aria-disabled").catch(() => null)) === "true") continue;
    // The 3D canvas and animated overlays can keep Playwright's actionability checks waiting, so
    // fall back to a forced click and then to a DOM click.
    try {
      await loc.click({ timeout });
      return true;
    } catch {
      /* fall through */
    }
    try {
      await loc.click({ timeout: 1000, force: true });
      return true;
    } catch {
      /* fall through */
    }
    try {
      await loc.evaluate((el) => el.click());
      return true;
    } catch {
      /* try the next role */
    }
  }
  log(`  skipped: no control named ${name}`);
  return false;
}

/** Pick the analysis whose option label or value matches, in the shell's analysis selector. */
async function chooseAnalysis(page, pattern) {
  const select = page.getByRole("combobox", { name: /analysis/i }).first();
  if ((await select.count().catch(() => 0)) === 0) {
    log("  skipped: no analysis selector");
    return false;
  }
  const options = await select.locator("option").evaluateAll((els) =>
    els.map((el) => ({ value: el.value, label: el.textContent || "" })),
  );
  const hit = options.find((o) => pattern.test(o.label) || pattern.test(o.value));
  if (!hit) {
    log(`  skipped: no analysis matching ${pattern}`);
    return false;
  }
  await select.selectOption(hit.value).catch(() => {});
  return true;
}

/** Eased scroll of the page's scroller to an absolute offset (or a fraction of its range). */
async function glide(page, { to, fraction, ms }) {
  await page.evaluate(
    ({ to, fraction, ms }) =>
      new Promise((done) => {
        const el = window.__cfScroller();
        const max = el.scrollHeight - el.clientHeight;
        const from = el.scrollTop;
        const target = Math.max(0, Math.min(max, fraction != null ? max * fraction : to));
        const t0 = performance.now();
        const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
        const step = (now) => {
          const k = Math.min(1, (now - t0) / ms);
          el.scrollTop = from + (target - from) * ease(k);
          if (k < 1) requestAnimationFrame(step);
          else done();
        };
        requestAnimationFrame(step);
      }),
    { to, fraction, ms },
  );
}

/** Glide so the first visible text matching `pattern` sits at `at` of the viewport height. */
async function glideToText(page, pattern, { ms = 1800, at = 0.38 } = {}) {
  const offset = await page.evaluate(
    ({ source, flags, at }) => {
      const re = new RegExp(source, flags);
      const el = window.__cfScroller();
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (!re.test(node.textContent || "")) continue;
        const host = node.parentElement;
        if (!host || host.closest("[aria-hidden='true']")) continue;
        const rect = host.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        const isDoc = el === document.scrollingElement || el === document.documentElement;
        const top = isDoc ? 0 : el.getBoundingClientRect().top;
        return el.scrollTop + (rect.top - top) - el.clientHeight * at;
      }
      return null;
    },
    { source: pattern.source, flags: pattern.flags.replace("g", ""), at },
  );
  if (offset === null) {
    log(`  skipped: no text matching ${pattern}`);
    return false;
  }
  await glide(page, { to: offset, ms });
  return true;
}

/**
 * For frame-stepped clips: starts a glide and returns at once. The glide runs on the page's virtual
 * clock, so it advances only as the recorder steps it and comes out smooth at any machine load.
 */
async function glideLater(page, { to, fraction, pattern, at = 0.38, ms }) {
  let target = to;
  if (pattern) {
    target = await page.evaluate(
      ({ source, flags, at }) => {
        const re = new RegExp(source, flags);
        const el = window.__cfScroller();
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
          if (!re.test(node.textContent || "")) continue;
          const host = node.parentElement;
          if (!host || host.closest("[aria-hidden='true']")) continue;
          const rect = host.getBoundingClientRect();
          if (rect.width === 0 || rect.height === 0) continue;
          const isDoc = el === document.scrollingElement || el === document.documentElement;
          const top = isDoc ? 0 : el.getBoundingClientRect().top;
          return el.scrollTop + (rect.top - top) - el.clientHeight * at;
        }
        return null;
      },
      { source: pattern.source, flags: pattern.flags.replace("g", ""), at },
    );
    if (target === null) {
      log(`  skipped: no text matching ${pattern}`);
      return false;
    }
  }
  glide(page, { to: target, fraction, ms }).catch(() => {});
  return true;
}

/** Records the page through the DevTools screencast while `script` runs, then encodes it. */
async function record(context, name, url, script) {
  const page = await context.newPage();
  const frames = [];
  const dir = join(WORK_DIR, name);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });

  log(`${name}: ${url}`);
  await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
  await page.addStyleTag({ content: "*{scrollbar-width:none!important}::-webkit-scrollbar{display:none!important}" }).catch(() => {});
  await sleep(1500);

  const cdp = await context.newCDPSession(page);
  let writing = Promise.resolve();
  cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
    const file = join(dir, `f${String(frames.length).padStart(5, "0")}.jpg`);
    frames.push({ file, t: metadata.timestamp });
    writing = writing.then(() => writeFileSync(file, Buffer.from(data, "base64")));
    cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: WIDTH, maxHeight: HEIGHT, everyNthFrame: 1 });
  const startedWall = Date.now() / 1000;
  try {
    await script(page);
  } catch (error) {
    log(`  step failed, keeping what was recorded: ${String(error.message).split("\n")[0]}`);
  }
  const elapsed = Date.now() / 1000 - startedWall;
  await cdp.send("Page.stopScreencast").catch(() => {});
  await writing;
  await page.close();

  if (frames.length === 0) {
    log(`  no frames for ${name}`);
    return null;
  }
  // Concat list: each frame lasts until the next one arrived; the last one until the script ended.
  const lines = ["ffconcat version 1.0"];
  const t0 = frames[0].t;
  frames.forEach((f, i) => {
    const next = i + 1 < frames.length ? frames[i + 1].t : Math.max(f.t + 1 / FPS, t0 + elapsed);
    lines.push(`file '${f.file}'`, `duration ${Math.max(1 / FPS, next - f.t).toFixed(4)}`);
  });
  lines.push(`file '${frames[frames.length - 1].file}'`);
  const list = join(dir, "list.ffconcat");
  writeFileSync(list, lines.join("\n") + "\n");

  const out = join(OUT_DIR, `${name}.mp4`);
  const ff = spawnSync(
    "ffmpeg",
    ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list,
      "-vf", `scale=${WIDTH}:${HEIGHT}:flags=lanczos,fps=${FPS},format=yuv420p`,
      "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-movflags", "+faststart", out],
    { stdio: "inherit" },
  );
  if (ff.status !== 0) {
    log(`  ffmpeg failed for ${name}`);
    return null;
  }
  const probe = spawnSync("ffprobe", ["-v", "error", "-count_packets", "-select_streams", "v:0",
    "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", out], { encoding: "utf8" });
  const durationInFrames = Number.parseInt(String(probe.stdout).trim(), 10) || 0;
  log(`  ${name}.mp4: ${frames.length} source frames over ${elapsed.toFixed(1)} s -> ${durationInFrames} frames`);
  return { src: `captures/${name}.mp4`, durationInFrames, url, sourceFrames: frames.length };
}

/**
 * Installed before the page loads, for frame-stepped clips: the page's clock (performance.now,
 * Date.now, requestAnimationFrame) runs on virtual time. It free-runs on real time while the page
 * loads; window.__cfStep(ms) then freezes it and advances exactly ms per call, so a WebGL scene that
 * renders at 2 fps in software still yields smooth 30 fps footage with true playback speed.
 */
function installVirtualTime() {
  const realNow = performance.now.bind(performance);
  const realRaf = window.requestAnimationFrame.bind(window);
  const realDateNow = Date.now.bind(Date);
  const dateOrigin = realDateNow() - realNow();
  let stepped = false;
  let now = realNow();
  let queue = [];
  let nextId = 1;
  const flush = () => {
    const due = queue;
    queue = [];
    for (const entry of due) {
      try {
        entry.cb(now);
      } catch (error) {
        console.error(error);
      }
    }
  };
  performance.now = () => (stepped ? now : realNow());
  Date.now = () => Math.round(dateOrigin + (stepped ? now : realNow()));
  window.requestAnimationFrame = (cb) => {
    const id = nextId++;
    queue.push({ id, cb });
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    queue = queue.filter((entry) => entry.id !== id);
  };
  const pump = () => {
    if (stepped) return;
    now = realNow();
    flush();
    realRaf(pump);
  };
  realRaf(pump);
  window.__cfStep = (ms) => {
    if (!stepped) {
      stepped = true;
      now = realNow();
    }
    now += ms;
    flush();
    return new Promise((done) => realRaf(() => realRaf(() => done(true))));
  };
}

/**
 * Frame-stepped recording: advances the page's virtual clock 1/FPS s per output frame and takes a
 * screenshot each time. `timeline` actions run when the output reaches their second.
 */
async function recordStepped(context, name, url, { prepare, seconds, timeline }) {
  const page = await context.newPage();
  await page.addInitScript(installVirtualTime);
  const dir = join(WORK_DIR, name);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  log(`${name}: ${url} (frame-stepped, ${seconds} s)`);
  await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 60000 }).catch(() => {});
  await page.addStyleTag({ content: "*{scrollbar-width:none!important}::-webkit-scrollbar{display:none!important}" }).catch(() => {});
  await sleep(1500);
  try {
    await prepare(page);
  } catch (error) {
    log(`  prepare failed: ${String(error.message).split("\n")[0]}`);
  }
  const cdp = await context.newCDPSession(page);
  const total = Math.round(seconds * FPS);
  const pending = [...timeline].sort((a, b) => a.at - b.at);
  const t0 = Date.now();
  for (let i = 0; i < total; i += 1) {
    while (pending.length && pending[0].at * FPS <= i) {
      const step = pending.shift();
      try {
        await step.run(page);
      } catch (error) {
        log(`  action at ${step.at}s failed: ${String(error.message).split("\n")[0]}`);
      }
    }
    await page.evaluate((ms) => window.__cfStep(ms), 1000 / FPS);
    const shot = await cdp.send("Page.captureScreenshot", { format: "jpeg", quality: 92 });
    writeFileSync(join(dir, `f${String(i).padStart(5, "0")}.jpg`), Buffer.from(shot.data, "base64"));
    if (i % 60 === 0) log(`  frame ${i}/${total} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  }
  await page.close();
  const out = join(OUT_DIR, `${name}.mp4`);
  const ff = spawnSync(
    "ffmpeg",
    ["-y", "-loglevel", "error", "-framerate", String(FPS), "-i", join(dir, "f%05d.jpg"),
      "-vf", `scale=${WIDTH}:${HEIGHT}:flags=lanczos,format=yuv420p`,
      "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-r", String(FPS), "-movflags", "+faststart", out],
    { stdio: "inherit" },
  );
  if (ff.status !== 0) return null;
  log(`  ${name}.mp4: ${total} frames`);
  return { src: `captures/${name}.mp4`, durationInFrames: total, url, sourceFrames: total, stepped: true };
}

// The five clips the edit uses. Each script is time-boxed so clip lengths stay predictable.
const CLIPS = {
  // (a) Digital Twin at 4x, the camera easing between its presets.
  twin: {
    url: "/twin",
    stepped: {
      // The twin scene shows 12.4 s, so 12.6 s is enough: Oblique, Along Lankershim, Plan.
      seconds: Number(process.env.CAPTURE_TWIN_SECONDS || 12.6),
      async prepare(page) {
        await page.waitForSelector("canvas", { timeout: 20000 }).catch(() => log("  no canvas yet"));
        await sleep(3500);
        await press(page, /^4x$/);
        await press(page, /^oblique$/i);
        await sleep(2500);
      },
      timeline: [
        { at: 0, run: (page) => press(page, /^play$/i) },
        { at: 4, run: (page) => press(page, /along lankershim/i) },
        { at: 8.5, run: (page) => press(page, /^plan$/i) },
      ],
    },
  },
  // (b) Command center: the withheld figure, then the decision.
  command: {
    url: "/",
    // The page fits one screen; its camera-tracks replay runs on the stepped clock.
    stepped: { seconds: 10, async prepare() { await sleep(2500); }, timeline: [] },
    async run(page) {
      await sleep(2500);
      await glideToText(page, /withheld/i, { ms: 2200 });
      await sleep(3000);
      await glideToText(page, /nothing (is )?recommended/i, { ms: 2200 });
      await sleep(3500);
    },
  },
  // (c) Evidence: the gate panel, scrolled slowly.
  evidence: {
    url: "/evidence",
    // The evidence scene starts 4.5 s into the clip (gate table scrolled up) and runs 6.4 s.
    stepped: {
      seconds: 11,
      async prepare() { await sleep(2000); },
      timeline: [
        { at: 0.8, run: (page) => glideLater(page, { pattern: /entering count|agreement gate|held-out/i, at: 0.2, ms: 3200 }) },
        { at: 6.5, run: (page) => glideLater(page, { fraction: 0.75, ms: 6000 }) },
      ],
    },
    async run(page) {
      await sleep(2000);
      if (!(await glideToText(page, /entering count|agreement gate|held-out/i, { ms: 2500, at: 0.2 }))) {
        await glide(page, { fraction: 0.3, ms: 2500 });
      }
      await sleep(2500);
      await glide(page, { fraction: 0.75, ms: 6000 });
      await sleep(1500);
    },
  },
  // (d) Analytics: turning movements.
  analytics: {
    url: "/analytics",
    // The analytics scene starts 5 s into the clip (after the scroll) and runs 7.4 s, so 13 s covers it.
    stepped: {
      seconds: 13,
      async prepare() { await sleep(2000); },
      timeline: [{ at: 0.4, run: (page) => glideLater(page, { pattern: /turning/i, at: 0.25, ms: 2400 }) }],
    },
    async run(page) {
      await sleep(2000);
      if (!(await glideToText(page, /turning/i, { ms: 2500, at: 0.25 }))) await glide(page, { fraction: 0.4, ms: 2500 });
      await sleep(2000);
      await page.mouse.move(WIDTH * 0.45, HEIGHT * 0.55, { steps: 25 });
      await sleep(1500);
      await page.mouse.move(WIDTH * 0.6, HEIGHT * 0.5, { steps: 25 });
      await sleep(2500);
    },
  },
  // (e1) Vision Lab on the 15-minute study: tracks over the camera view.
  vision: {
    url: "/vision",
    stepped: {
      seconds: 12,
      async prepare(page) {
        await sleep(1500);
        await press(page, /^tracks$/i);
        await sleep(800);
      },
      timeline: [{ at: 0, run: (page) => press(page, /^play$/i) }],
    },
    async run(page) {
      await sleep(1500);
      await press(page, /^tracks$/i);
      const play = page.getByRole("button", { name: /^play/i }).first();
      if ((await play.count()) > 0) await play.click({ timeout: 2000 }).catch(() => {});
      await sleep(6000);
      await press(page, /^boxes$/i);
      await sleep(5000);
    },
  },
  // (e2) Vision Lab on the SAM window: its masks.
  sam: {
    url: "/vision",
    async run(page) {
      await sleep(1500);
      await chooseAnalysis(page, /sam/i);
      await sleep(2500);
      const play = page.getByRole("button", { name: /^play/i }).first();
      if ((await play.count()) > 0) await play.click({ timeout: 2000 }).catch(() => {});
      await press(page, /mask/i);
      await sleep(3000);
      if (!(await glideToText(page, /mask|sam 3\.1/i, { ms: 2500, at: 0.3 }))) await glide(page, { fraction: 0.35, ms: 2500 });
      await sleep(3000);
    },
  },
};

async function main() {
  const health = await fetch(`${BASE}/api/v1/health`).then((r) => r.ok).catch(() => false);
  if (!health) {
    console.error(`[capture] no server at ${BASE}. Start one first: CF_DEMO_PORT=8105 bash scripts/demo_run.sh`);
    process.exit(1);
  }
  mkdirSync(OUT_DIR, { recursive: true });
  mkdirSync(WORK_DIR, { recursive: true });
  const { chromium } = await loadPlaywright();
  const executablePath = findHeadlessShell();
  const browser = await chromium.launch({
    ...(executablePath ? { executablePath } : {}),
    args: ["--enable-webgl", "--ignore-gpu-blocklist", "--hide-scrollbars"],
  });
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1 });
  await context.addInitScript(installScrollerHelper);
  const manifestPath = join(OUT_DIR, "manifest.json");
  let manifest = {};
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch {
    manifest = {};
  }
  manifest.clips = manifest.clips || {};
  for (const [name, clip] of Object.entries(CLIPS)) {
    if (ONLY && !ONLY.has(name)) continue;
    const useStepped = clip.stepped && !(SCREENCAST && clip.run);
    const result = useStepped
      ? await recordStepped(context, name, clip.url, clip.stepped)
      : await record(context, name, clip.url, clip.run);
    if (result) manifest.clips[name] = result;
  }
  manifest.capturedAt = new Date().toISOString();
  manifest.base = BASE;
  manifest.fps = FPS;
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
  await browser.close();
  log(`wrote ${manifestPath}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
