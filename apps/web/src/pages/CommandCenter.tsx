import { motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import {
  ButtonLink,
  DUR,
  EASE,
  EVIDENCE,
  EVIDENCE_KINDS,
  ErrorState,
  EvidenceBadge,
  EvidenceGlyph,
  Label,
  Numeral,
  PlanDrawing,
  Skeleton,
  SkeletonRows,
  useElementSize,
} from "../design";
import { DataSlot } from "../design/DataSlot";
import { Instrument } from "../design/Instrument";
import { baselineLabel, decisionFor, whatWouldChange } from "../domain/chain";
import { isCalibrated } from "../domain/footage";
import { gateName, gatesPassed, withheldBy } from "../domain/gates";
import { findMetric, readMetric } from "../domain/metrics";
import { ARM_ORDER, fitAcross, fitToBand, onArm, projector, sitePlan, type PlanFrame, type SitePlanArms } from "../domain/plan";
import { ACTIVE_SITE, SITE_NAME_NOTE } from "../domain/site";
import { ChainRail, useChain } from "../instruments/Chain";
import { MetaModels } from "../instruments/MetaModels";
import { Readouts } from "../instruments/Readouts";
import { SitePlan } from "../instruments/SitePlan";
import { NO_GROUND, clock, type LoadedAnalysis, type Metric, type TrackEntry } from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { useDecision, useSite } from "../state/resources";
import type { SiteGeometry } from "../twin/site";

/*
 * The opening composition. The junction's own paint is the layout: its two roads cut the screen
 * into four corners, and each corner holds one instrument. The tracks of the grid and the fit of
 * the plan come from the same numbers, so they cannot drift apart.
 */
const EAST_PX = 320;
const SOUTH_PX = 188;
const ROAD_NS_PX = 148;
const ROAD_EW_PX = 134;
/** Keeps the street on one line at any width: the column left of the road, over the name's width. */
const HERO_FIT = `clamp(60px, calc((100vw - var(--rail) - ${ROAD_NS_PX + EAST_PX}px - 96px) / 6.45), 148px)`;
/** Junction metres across the shorter side of the plan on a narrow screen. */
const NARROW_PLAN_M = 92;
/** Frames of history behind each replayed vehicle on the plan. */
const TAIL_FRAMES = 14;
const REPLAY_CLOCK_ID = "hero-replay-clock";

const HEADLINE_METRICS = ["flow.entering", "speed.mean_moving", "stopped.share"];

/** One lamp per committed check, in the record's order: the run's scorecard at a glance. */
function GateStrip() {
  const { state } = useAnalysis();
  if (state.status !== "ready") return null;
  const { record } = state.loaded;
  const gates = record.gates ?? [];
  if (gates.length === 0) return null;
  const { passed, total } = gatesPassed(record);
  const blind = gates.every((gate) => gate.split === "held_out");
  return (
    <Link
      to="/evidence#gates"
      className="group inline-flex items-center gap-3 self-start"
      aria-label={`${passed} of ${total} checks passed${blind ? ", held-out read" : ""}`}
    >
      <span aria-hidden="true" className="flex gap-1">
        {gates.map((gate, index) => (
          <motion.span
            key={`${gate.metric}-${index}`}
            title={`${gateName(gate.metric)}: ${gate.passed ? "passed" : "failed"}`}
            initial={{ opacity: 0, scaleY: 0.2 }}
            animate={{ opacity: 1, scaleY: 1 }}
            transition={{ duration: DUR.base, ease: EASE, delay: 0.15 + index * 0.05 }}
            className={`block h-3 w-5 ${gate.passed ? "bg-go" : "bg-stop shadow-[0_0_10px_var(--stop)]"}`}
          />
        ))}
      </span>
      <span className="mono text-paint-1 group-hover:text-amber">{`${passed} of ${total} checks passed`}</span>
      {blind ? <span className="mono text-paint-3">held-out read</span> : null}
    </Link>
  );
}

function Headline() {
  const { state } = useAnalysis();
  const uploaded = state.status === "ready" && state.loaded.record.source.uploaded_by_user ? state.loaded.record : null;
  if (uploaded) {
    return (
      <>
        <h1 className="mt-auto font-display text-display leading-none font-medium tracking-[-0.015em] break-all">
          {uploaded.source.title}
        </h1>
        <p className="mono mt-4 text-paint-3">Uploaded video, no registered site</p>
        <p className="mono mt-1 text-paint-3">{uploaded.source.licence_name}</p>
      </>
    );
  }
  return (
    <>
      <GateStrip />
      <p className="eyebrow eyebrow-dot mt-auto pt-4">NGSIM intersection 2</p>
      <h1
        title={SITE_NAME_NOTE}
        className="mt-3 -ml-[0.035em] text-hero leading-[0.9] font-medium tracking-[-0.022em] xl:whitespace-nowrap"
        style={{ fontSize: "var(--hero-fit, var(--text-hero))" }}
      >
        {ACTIVE_SITE.street}
      </h1>
      <p
        title={SITE_NAME_NOTE}
        className="mt-2 font-display text-display leading-none tracking-[-0.012em] text-paint-2"
      >
        at {ACTIVE_SITE.crossStreet}
      </p>
      <p className="mt-4 font-display text-head leading-6 text-paint-2">Traffic intelligence that shows its working.</p>
    </>
  );
}

function headlineMetrics(loaded: LoadedAnalysis): Metric[] {
  return HEADLINE_METRICS.flatMap((id) => {
    const metric = findMetric(loaded.record, id);
    return metric ? [metric] : [];
  });
}

/** With data on screen the opening states what was measured. Without it, what the product is. */
function Opening() {
  return (
    <div className="mt-6 min-h-[122px] border-t border-rule-1 pt-4">
      <DataSlot
        failed="Couldn't load this analysis."
        retry={false}
        skeleton={
          <div className="flex items-end gap-10">
            <Skeleton width={120} height={50} />
            <Skeleton width={120} height={50} />
            <Skeleton width={120} height={50} />
          </div>
        }
        empty={
          <div className="flex flex-wrap items-center gap-x-8 gap-y-4 xl:flex-nowrap">
            <p className="max-w-[30rem] text-head leading-snug text-paint-1">Camera footage in. Checked evidence out.</p>
            <ButtonLink to="/intersections" className="shrink-0">
              Choose an analysis
            </ButtonLink>
          </div>
        }
      >
        {(loaded) => (
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
            <div className="min-w-0">
              <Readouts
                metrics={headlineMetrics(loaded)}
                record={loaded.record}
                className="flex flex-wrap items-start gap-x-10 gap-y-4 [&>div]:max-w-[16rem]"
              />
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <ButtonLink to="/twin" tone="text" className="font-normal">
                Open twin
              </ButtonLink>
              <ButtonLink to="/vision">Vision Lab</ButtonLink>
            </div>
          </div>
        )}
      </DataSlot>
    </div>
  );
}

function EvidenceKey() {
  return (
    <>
      <h2 className="eyebrow">Evidence key</h2>
      <ul className="m-0 mt-2 flex list-none flex-wrap gap-x-5 gap-y-1.5 p-0">
        {EVIDENCE_KINDS.map((kind) => (
          <li key={kind} className="flex items-center gap-2">
            <EvidenceGlyph kind={kind} />
            <span className="mono" style={{ color: EVIDENCE[kind].colour }}>
              {EVIDENCE[kind].label}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

function Chain() {
  const { state } = useAnalysis();
  const stages = useChain(state.status === "ready" ? state.loaded : null);
  return (
    <>
      <div className="flex items-baseline justify-between gap-6">
        <h2 className="eyebrow">Provenance</h2>
        <ButtonLink to="/evidence" tone="text" className="font-normal">
          Evidence
        </ButtonLink>
      </div>
      <ChainRail stages={stages} />
    </>
  );
}

const DECISION_HEAD = "mt-3 font-display text-display leading-[0.98] font-medium tracking-[-0.015em] text-paint-1";

/** The product's whole point, so it gets the largest corner and the largest words in it. */
function Decision() {
  const { state } = useAnalysis();
  const decision = useDecision();
  const heading = <h2 className="eyebrow eyebrow-dot">Decision</h2>;

  if (state.status === "loading" || (state.status === "ready" && decision.status === "loading")) {
    return (
      <>
        {heading}
        <div className="mt-3">
          <SkeletonRows rows={3} />
        </div>
      </>
    );
  }
  if (state.status === "error") {
    return (
      <>
        {heading}
        <ErrorState what="Couldn't read a decision." className="mt-3" lastGood={state.lastGood} />
      </>
    );
  }
  if (state.status === "none") {
    return (
      <>
        {heading}
        <p className={DECISION_HEAD}>Nothing is recommended</p>
        <p className="mono mt-3 text-paint-2">Needs a validated baseline.</p>
      </>
    );
  }
  if (decision.status === "error") {
    return (
      <>
        {heading}
        <ErrorState what="Couldn't load the decision record." className="mt-3" onRetry={decision.retry} />
      </>
    );
  }

  const record = state.status === "ready" ? state.loaded.record : null;
  const paired = record && decision.status === "ready" ? decisionFor(record, decision.data) : null;
  if (!record || !paired) {
    return (
      <>
        {heading}
        <p className={DECISION_HEAD}>Nothing is recommended</p>
        <p className="mono mt-3 text-paint-2">No decision record names this analysis.</p>
      </>
    );
  }

  const recommended = paired.outcome.recommended_intervention_id;
  const change = whatWouldChange(paired);
  const timelines = paired.ensemble.members.filter((member) => member.required).length;
  const facts: [string, string][] = [
    ["Candidates", String(paired.candidates.length)],
    ["Seeds", String(paired.simulation.seeds.length)],
    ["Timelines", String(timelines)],
  ];
  return (
    <>
      {heading}
      <p className={DECISION_HEAD}>{recommended ? `Recommend ${recommended}` : "Nothing is recommended"}</p>
      {record.decision_ref?.summary ? (
        <p className="mt-3 max-w-[17rem] text-label leading-snug text-paint-2">{record.decision_ref.summary}</p>
      ) : null}
      <dl className="m-0 mt-4 grid grid-cols-3 border-y border-rule-1">
        {facts.map(([name, value]) => (
          <div key={name} className="border-r border-rule-1 py-2 pl-2 first:pl-0 last:border-r-0">
            <dt className="eyebrow">{name}</dt>
            <dd className="m-0 mt-1 font-display text-num-s leading-none font-medium" style={{ color: EVIDENCE.simulated.colour }}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="mono mt-2 text-paint-3">{`Baseline ${baselineLabel(paired.baseline.status).toLowerCase()}`}</p>
      {change ? (
        <details className="mt-2">
          <summary className="mono cursor-pointer text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1">
            What would change it
          </summary>
          <p className="mt-2 text-label leading-snug text-paint-2">{change}</p>
        </details>
      ) : null}
      <div className="mt-auto flex flex-col items-start gap-1 pt-3">
        <EvidenceBadge kind={paired.outcome.evidence_kind} note="SUMO" />
        {record.decision_ref?.linked_to_cv ? null : <Label>NGSIM inputs, not camera output</Label>}
      </div>
    </>
  );
}

/** Each arm's entering count, set on the arm itself at the far end of its lanes. */
function ArmCounts({ loaded, arms, frame }: { loaded: LoadedAnalysis; arms: SitePlanArms; frame: PlanFrame }) {
  const project = projector(frame);
  return (
    <>
      {ARM_ORDER.map((arm) => {
        const plan = arms[arm];
        const metric = findMetric(loaded.record, `vehicles.arm.${arm.toLowerCase()}`);
        if (!plan || !metric) return null;
        const lanes = plan.approach;
        const cross = lanes.length > 0
          ? (Math.min(...lanes.map((lane) => lane.lo)) + Math.max(...lanes.map((lane) => lane.hi))) / 2
          : (plan.lo + plan.hi) / 2;
        const [x, y] = project(...onArm(arm, cross, plan.far));
        const { value, unit, format } = readMetric(metric);
        const withheld = withheldBy(loaded.record, metric);
        // One tile and one caption already say these are withheld, so the arm carries what is known.
        const place = {
          left: Math.min(Math.max(x, 72), frame.width - 72),
          top: Math.min(Math.max(y, 18), frame.height - 18),
        };
        if (withheld) {
          return (
            <span
              key={arm}
              className="pointer-events-none absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-baseline gap-1.5 border border-rule-1 bg-asphalt-0/90 px-1.5 py-0.5"
              style={place}
            >
              <Label tone="paint">{arm}</Label>
              <Label>{`${lanes.length} lanes`}</Label>
            </span>
          );
        }
        return (
          <span
            key={arm}
            className="pointer-events-none absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-baseline gap-1.5 border border-rule-1 bg-asphalt-0/90 px-1.5 py-0.5"
            style={place}
          >
            <Label>{arm}</Label>
            <Numeral value={value} size="s" format={format} unit={unit} />
          </span>
        );
      })}
    </>
  );
}

const mmss = (seconds: number) => {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
};

/**
 * The stored camera tracks replayed on the plan at their ground positions, at real speed. A local
 * cursor drives it, so the shared clock and every other page stay where they are.
 */
function LiveTraffic({ loaded, frame }: { loaded: LoadedAnalysis; frame: PlanFrame }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    const surface = canvas.current;
    const context = surface?.getContext("2d");
    if (!surface || !context || frame.width === 0 || frame.height === 0) return;
    const ratio = window.devicePixelRatio || 1;
    surface.width = Math.round(frame.width * ratio);
    surface.height = Math.round(frame.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const style = getComputedStyle(document.documentElement);
    const tone = style.getPropertyValue("--ev-approximated").trim() || "#7fd0da";
    const project = projector(frame);
    const { trajectories: t, tracks, record } = loaded;
    const scale = t.header.groundScale;
    const last = Math.max(0, record.frame_count - 1);
    let cursor = Math.min(Math.max(0, Math.round(clock.get())), last);
    let before = performance.now();
    let stamped = 0;
    let handle = 0;

    const draw = (at: number) => {
      context.clearRect(0, 0, frame.width, frame.height);
      context.lineCap = "round";
      context.lineJoin = "round";
      loaded.active.forEach(at, (index, row) => {
        const entry = tracks[index] as TrackEntry;
        if (t.gx[row] === NO_GROUND) return;
        const first = Math.max(entry.first_row, row - TAIL_FRAMES);
        context.beginPath();
        let started = false;
        for (let at2 = first; at2 <= row; at2 += 1) {
          if (t.gx[at2] === NO_GROUND) continue;
          const [px, py] = project((t.gx[at2] as number) * scale, (t.gy[at2] as number) * scale);
          if (started) context.lineTo(px, py);
          else {
            context.moveTo(px, py);
            started = true;
          }
        }
        context.globalAlpha = 0.32;
        context.strokeStyle = tone;
        context.lineWidth = 2;
        context.stroke();
        const [x, y] = project((t.gx[row] as number) * scale, (t.gy[row] as number) * scale);
        context.globalAlpha = 1;
        context.shadowColor = tone;
        context.shadowBlur = 10;
        context.fillStyle = tone;
        context.beginPath();
        context.arc(x, y, 2.6, 0, Math.PI * 2);
        context.fill();
        context.shadowBlur = 0;
      });
      context.globalAlpha = 1;
    };

    const stamp = (at: number, now: number) => {
      if (now - stamped < 250) return;
      stamped = now;
      const target = document.getElementById(REPLAY_CLOCK_ID);
      if (target) target.textContent = mmss(at / record.fps);
    };

    if (reduced) {
      draw(cursor);
      stamp(cursor, Infinity);
      return;
    }
    const step = (now: number) => {
      cursor += ((now - before) / 1000) * record.fps;
      before = now;
      if (cursor > last) cursor = 0;
      const at = Math.floor(cursor);
      draw(at);
      stamp(at, now);
      handle = requestAnimationFrame(step);
    };
    handle = requestAnimationFrame(step);
    return () => cancelAnimationFrame(handle);
  }, [loaded, frame, reduced]);

  return <canvas ref={canvas} aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" />;
}

/** Replay on: what the dots are, and the source second they show. */
function ReplayChip() {
  return (
    <span className="mono inline-flex items-center gap-2 border border-rule-2 bg-asphalt-1/80 px-2 py-0.5 text-paint-2">
      <span aria-hidden="true" className="lamp-live inline-block size-1.5 bg-ev-approximated" />
      <span className="text-paint-1">Replay</span>
      <span id={REPLAY_CLOCK_ID} className="text-paint-1">
        --:--
      </span>
      <span className="text-paint-3">camera tracks</span>
    </span>
  );
}

function PlanNotes({ hasSite, loaded }: { hasSite: boolean; loaded: LoadedAnalysis | null }) {
  if (!hasSite) return <EvidenceBadge kind="assumed" note="Schematic" />;
  const calibrated = loaded ? isCalibrated(loaded.record) : false;
  const counted = loaded && calibrated ? findMetric(loaded.record, "vehicles.arm.n") : null;
  return (
    <>
      {calibrated ? <ReplayChip /> : null}
      <EvidenceBadge kind="approximated" note="Lanes from tracks" />
      <EvidenceBadge kind="assumed" note="Road beyond" />
      {counted && counted.status !== "gate_fail" ? (
        <EvidenceBadge kind={counted.evidence_kind} note="Arm counts" />
      ) : null}
      {counted?.status === "gate_fail" ? (
        <Link
          to="/evidence#gates"
          className="mono inline-flex items-center gap-1.5 text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1 hover:decoration-amber"
        >
          <span aria-hidden="true" className="inline-block size-1.5 bg-stop" />
          Arm counts withheld
        </Link>
      ) : null}
      {loaded && !calibrated ? <Label>Registered junction, not this video</Label> : null}
    </>
  );
}

function Plan({ site, width, height }: { site: SiteGeometry | null; width: number; height: number }) {
  const { state } = useAnalysis();
  const arms = useMemo(() => (site ? sitePlan(site) : null), [site]);
  const left = width - EAST_PX - ROAD_NS_PX;
  const top = height - SOUTH_PX - ROAD_EW_PX;
  const frame = useMemo(
    () =>
      arms
        ? fitToBand(arms, { left, right: left + ROAD_NS_PX, top, bottom: top + ROAD_EW_PX }, width, height)
        : null,
    [arms, left, top, width, height],
  );

  if (!site || !frame || !arms) {
    return (
      <PlanDrawing
        className="absolute inset-0"
        geometry={{
          width,
          height,
          cx: left + ROAD_NS_PX / 2,
          cy: top + ROAD_EW_PX / 2,
          halfNs: ROAD_NS_PX / 2,
          halfEw: ROAD_EW_PX / 2,
        }}
      />
    );
  }

  const calibrated = state.status === "ready" && isCalibrated(state.loaded.record) ? state.loaded : null;
  return (
    <>
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0"
        style={{ left: Math.max(0, left - 220) }}
      >
        <div className="survey-grid absolute inset-0" />
        <div className="scan-sweep absolute inset-0" />
      </div>
      <motion.div layoutId="site-plan" transition={{ duration: DUR.slow, ease: EASE }} className="absolute inset-0">
        <SitePlan className="absolute inset-0" site={site} frame={frame} continuation />
      </motion.div>
      {calibrated ? <LiveTraffic loaded={calibrated} frame={frame} /> : null}
      {calibrated ? <ArmCounts loaded={calibrated} arms={arms} frame={frame} /> : null}
    </>
  );
}

export function CommandCenter() {
  const [frame, size] = useElementSize<HTMLDivElement>();
  const [narrow, narrowSize] = useElementSize<HTMLDivElement>();
  const { state } = useAnalysis();
  const site = useSite();
  const siteData = site.status === "ready" ? site.data : null;
  const loaded = state.status === "ready" ? state.loaded : null;
  const narrowFrame = useMemo(
    () => fitAcross(narrowSize.width, narrowSize.height, NARROW_PLAN_M, 0.5, 0.5),
    [narrowSize.width, narrowSize.height],
  );

  return (
    <>
      <div
        ref={frame}
        className="relative hidden h-full min-h-[640px] xl:grid"
        style={{
          gridTemplateColumns: `minmax(0, 1fr) ${ROAD_NS_PX}px ${EAST_PX}px`,
          gridTemplateRows: `minmax(0, 1fr) ${ROAD_EW_PX}px ${SOUTH_PX}px`,
          ["--hero-fit" as string]: HERO_FIT,
        }}
      >
        {size.width > 0 ? <Plan site={siteData} width={size.width} height={size.height} /> : null}
        <Instrument className="relative col-start-1 row-start-1 flex min-h-0 flex-col pt-6 pr-10 pb-5 pl-margin">
          <Headline />
          <Opening />
        </Instrument>
        <Instrument className="relative col-start-3 row-start-1 flex min-h-0 flex-col pt-6 pr-margin pb-5 pl-6">
          <Decision />
        </Instrument>
        <Instrument className="relative col-start-1 row-start-2 flex flex-wrap items-end gap-x-5 gap-y-1 self-end pr-24 pb-7 pl-margin">
          <PlanNotes hasSite={siteData !== null} loaded={loaded} />
        </Instrument>
        <Instrument className="relative col-start-1 row-start-3 pt-5 pr-10 pb-5 pl-margin">
          <Chain />
        </Instrument>
        <Instrument className="relative col-start-3 row-start-3 pt-4 pr-margin pb-3 pl-6">
          <MetaModels />
        </Instrument>
      </div>

      <div className="xl:hidden">
        <Instrument className="flex flex-col px-margin pt-5">
          <Headline />
        </Instrument>
        <Instrument className="mt-6">
          <div ref={narrow} className="relative h-[240px] overflow-hidden">
            {siteData ? (
              <>
                <SitePlan className="absolute inset-0" site={siteData} frame={narrowFrame} continuation />
                {loaded && isCalibrated(loaded.record) && narrowSize.width > 0 ? (
                  <LiveTraffic loaded={loaded} frame={narrowFrame} />
                ) : null}
              </>
            ) : null}
          </div>
          {siteData ? (
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 px-margin">
              <PlanNotes hasSite loaded={null} />
            </div>
          ) : null}
        </Instrument>
        <Instrument className="px-margin">
          <Opening />
        </Instrument>
        <Instrument className="mt-10 flex flex-col px-margin">
          <Decision />
        </Instrument>
        <Instrument className="mt-10 px-margin">
          <Chain />
        </Instrument>
        <Instrument className="mt-10 px-margin">
          <MetaModels />
        </Instrument>
        <Instrument className="mt-10 px-margin pb-10">
          <EvidenceKey />
        </Instrument>
      </div>
    </>
  );
}
