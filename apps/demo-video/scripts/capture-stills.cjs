// Stills of the real app at 2x (3840x2160) for camera moves in the edit.
//   node capture-stills.cjs [base URL] [out dir]
// The demo server must be running (bash scripts/demo_run.sh). Every still is the product on its
// committed records; nothing is retouched.
const { chromium } = require("playwright");
const path = require("path");

const BASE = process.argv[2] || "http://127.0.0.1:8000";
const OUT = process.argv[3] || path.join(__dirname, "..", "public", "stills");
const GL = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];

async function settle(page, ms = 2500) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(ms);
}

(async () => {
  const browser = await chromium.launch({ args: GL });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 });
  await context.addInitScript(() => sessionStorage.setItem("cairoflow.analysis", "cv-ngsim-int2-15min-study"));
  const page = await context.newPage();
  const shot = async (name) => {
    await page.screenshot({ path: path.join(OUT, `${name}.png`) });
    console.log("still", name);
  };

  await page.goto(BASE + "/", { waitUntil: "networkidle" });
  await settle(page, 3500);
  await shot("command");

  await page.goto(BASE + "/analytics", { waitUntil: "networkidle" });
  await settle(page);
  await shot("analytics");
  const which = page.getByText("Which check stopped it");
  if (await which.count()) {
    await which.first().click();
    await page.waitForTimeout(800);
    await shot("analytics_check_open");
  }

  await page.goto(BASE + "/evidence", { waitUntil: "networkidle" });
  await settle(page);
  await shot("evidence_gates");
  for (const [label, name] of [["Decision", "evidence_decision"], ["Candidates", "evidence_candidates"], ["Hypotheses", "evidence_hypotheses"], ["Muse's reading", "evidence_muse"], ["Limitations", "evidence_limits"], ["Provenance", "evidence_provenance"]]) {
    const link = page.getByRole("link", { name: label, exact: true }).or(page.getByRole("button", { name: label, exact: true }));
    if (await link.count()) {
      await link.first().click();
      await page.waitForTimeout(1200);
      await shot(name);
    } else console.log("no tab", label);
  }

  await page.goto(BASE + "/intersections", { waitUntil: "networkidle" });
  await settle(page);
  await shot("intersections");

  await browser.close();
})();
