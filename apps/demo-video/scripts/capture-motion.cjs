// Smooth motion captures of the real app on software GL. The page runs in slow motion (its
// performance.now and animation-frame timestamps are scaled), frames are grabbed over CDP with the
// page time they show, then retimed to an exact 30 fps sequence and encoded.
//   node capture-motion.cjs [shot names...]     (default: every shot)
// Env: CF_BASE (default http://127.0.0.1:8000), CF_FRAMES_DIR (scratch for frames).
const { chromium } = require("playwright");
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const BASE = process.env.CF_BASE || "http://127.0.0.1:8000";
const OUT = path.join(__dirname, "..", "public", "clips");
const FRAMES = process.env.CF_FRAMES_DIR || path.join(__dirname, "..", ".frames");
const GL = []; // the headless shell's default software GL presents twin frames fastest
const STUDY = "cv-ngsim-int2-15min-study";
const SAM = "cv-hero-sam";
const FPS = 30;

const click = (label) => async (page) => {
  const radio = page.getByRole("radio", { name: label, exact: true });
  if (await radio.count()) return radio.first().click({ timeout: 15000, force: true });
  return page.getByRole("button", { name: label, exact: true }).first().click({ timeout: 15000, force: true });
};
const play = async (page) => {
  const button = page.getByRole("button", { name: /^play$/i });
  if (await button.count()) await button.first().click({ force: true, timeout: 15000 });
  else console.log("  (no play button)");
};
const at = (x, y) => async (page) => page.mouse.click(x, y);
const pause = (ms) => async (page) => page.waitForTimeout(ms);

// slow: page seconds per real second while capturing. settle: real ms after loading.
const SHOTS = {
  vision_boxes: { url: "/vision", analysis: STUDY, before: [click("Boxes"), play], seconds: 8, slow: 0.25, settle: 3000 },
  vision_tracks: { url: "/vision", analysis: STUDY, before: [click("Tracks"), play], seconds: 6, slow: 0.25, settle: 3000 },
  vision_sam: { url: "/vision", analysis: SAM, before: [play], seconds: 7, slow: 0.25, settle: 4000 },
  vision_geo: { url: "/vision?layer=geo", analysis: STUDY, before: [play], seconds: 5, slow: 0.25, settle: 3000 },
  twin_along: { url: "/twin", analysis: STUDY, before: [click("2x"), play], seconds: 8, slow: 1 / 30, settle: 9000 },
  twin_select: { url: "/twin", analysis: STUDY, before: [at(1290, 690), pause(4000), click("Follow selected"), pause(6000), play], seconds: 6, slow: 1 / 30, settle: 9000 },
  twin_plan: { url: "/twin", analysis: STUDY, before: [click("Plan"), pause(8000), click("4x"), play], seconds: 5, slow: 1 / 30, settle: 9000 },
  twin_geo: { url: "/twin", analysis: STUDY, before: [click("Geo"), pause(8000), click("4x"), play], seconds: 5, slow: 1 / 30, settle: 9000 },
};

function timeScale() {
  const realNow = performance.now.bind(performance);
  const realRaf = window.requestAnimationFrame.bind(window);
  let factor = 1;
  let base = 0;
  let realBase = 0;
  const pageNow = () => base + (realNow() - realBase) * factor;
  window.__cfSlow = (next) => {
    base = pageNow();
    realBase = realNow();
    factor = next;
    return base;
  };
  window.__cfNow = pageNow;
  performance.now = pageNow;
  window.requestAnimationFrame = (callback) => realRaf(() => callback(pageNow()));
}

async function capture(browser, name, spec) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await context.addInitScript((id) => sessionStorage.setItem("cairoflow.analysis", id), spec.analysis);
  await context.addInitScript(timeScale);
  const page = await context.newPage();
  await page.goto(BASE + spec.url, { waitUntil: "networkidle" });
  await page.waitForTimeout(spec.settle);
  for (const step of spec.before) {
    await step(page);
    await page.waitForTimeout(400);
  }
  const cdp = await context.newCDPSession(page);
  const dir = path.join(FRAMES, name);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  // Screencast pushes every frame the compositor makes; each is stamped with wall time, which maps
  // to page time through the slow-motion factor set at realStart.
  const grabbed = [];
  let realStart = 0;
  cdp.on("Page.screencastFrame", (frame) => {
    const file = path.join(dir, `g${String(grabbed.length).padStart(5, "0")}.jpg`);
    fs.writeFileSync(file, Buffer.from(frame.data, "base64"));
    grabbed.push({ t: (frame.metadata.timestamp - realStart) * spec.slow * 1000, file });
    cdp.send("Page.screencastFrameAck", { sessionId: frame.sessionId }).catch(() => {});
  });
  const t0 = Date.now();
  realStart = Date.now() / 1000;
  await page.evaluate((slow) => window.__cfSlow(slow), spec.slow);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  await page.waitForTimeout((spec.seconds / spec.slow) * 1000 + 1500);
  await cdp.send("Page.stopScreencast");
  await page.waitForTimeout(300);
  await context.close();
  // Retime: for each output frame, the last grab made at or before its page time.
  const seq = path.join(dir, "seq");
  fs.mkdirSync(seq);
  const total = Math.round(spec.seconds * FPS);
  let j = 0;
  for (let k = 0; k < total; k += 1) {
    const target = (k * 1000) / FPS;
    while (j + 1 < grabbed.length && grabbed[j + 1].t <= target) j += 1;
    fs.copyFileSync(grabbed[j].file, path.join(seq, `f${String(k).padStart(4, "0")}.jpg`));
  }
  const file = path.join(OUT, `${name}.mp4`);
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(FPS), "-i", path.join(seq, "f%04d.jpg"),
    "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-movflags", "+faststart", file]);
  const unique = new Set();
  j = 0;
  for (let k = 0; k < total; k += 1) {
    const target = (k * 1000) / FPS;
    while (j + 1 < grabbed.length && grabbed[j + 1].t <= target) j += 1;
    unique.add(j);
  }
  console.log(`${name}: ${grabbed.length} grabs, ${unique.size}/${total} distinct frames, ${((Date.now() - t0) / 1000).toFixed(0)} s -> ${file}`);
}

(async () => {
  const wanted = process.argv.slice(2);
  const names = wanted.length ? wanted : Object.keys(SHOTS);
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ args: GL });
  for (const name of names) {
    try {
      await capture(browser, name, SHOTS[name]);
    } catch (error) {
      console.log(`${name}: FAILED ${error.message.split("\n")[0]}`);
    }
  }
  await browser.close();
})();
