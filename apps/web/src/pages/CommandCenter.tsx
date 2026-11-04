import { motion } from "motion/react";
import { useMemo } from "react";
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
import { decisionFor, whatWouldChange } from "../domain/chain";
import { isCalibrated } from "../domain/footage";
import { withheldBy } from "../domain/gates";
import { findMetric, readMetric } from "../domain/metrics";
import { ARM_ORDER, fitAcross, fitToBand, onArm, projector, sitePlan, type PlanFrame, type SitePlanArms } from "../domain/plan";
import { ACTIVE_SITE, SITE_NAME_NOTE, productLine } from "../domain/site";
import { ChainRail, useChain } from "../instruments/Chain";
import { MetaModels } from "../instruments/MetaModels";
import { Readouts } from "../instruments/Readouts";
import { SitePlan } from "../instruments/SitePlan";
import type { LoadedAnalysis, Metric } from "../lib/analysis";
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

const HEADLINE_METRICS = ["flow.entering", "speed.mean_moving", "stopped.share"];

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
      <h1
        className="mt-auto -ml-[0.035em] text-hero leading-[0.9] font-medium tracking-[-0.022em] xl:whitespace-nowrap"
        style={{ fontSize: "var(--hero-fit, var(--text-hero))" }}
      >
        {ACTIVE_SITE.street}
      </h1>
      <p className="mt-2 font-display text-display leading-none tracking-[-0.012em] text-paint-2">
        at {ACTIVE_SITE.crossStreet}
      </p>
      <p className="mt-3 max-w-[43rem] leading-snug text-paint-1">{productLine(ACTIVE_SITE)}</p>
      <p className="mono mt-2 text-paint-3">
        {`${ACTIVE_SITE.city}, ${ACTIVE_SITE.dataset} ${ACTIVE_SITE.year}. ${SITE_NAME_NOTE}`}
      </p>
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
    <div className="mt-4 min-h-[122px]">
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
            <p className="max-w-[30rem] text-head leading-snug text-paint-1">
              Camera footage in, evidence out. CairoFlow labels what was seen, what was derived and
              what was assumed, and recommends nothing unless the simulation baseline is validated.
            </p>
            <ButtonLink to="/intersections" className="shrink-0">
              Choose an analysis
            </ButtonLink>
          </div>
        }
      >
        {(loaded) => (
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
            <div className="min-w-0">
              <Readouts
                metrics={headlineMetrics(loaded)}
                record={loaded.record}
                className="flex flex-wrap items-start gap-x-6 gap-y-4 [&>div]:max-w-[16rem]"
              />
            </div>
            <ButtonLink to="/vision" className="shrink-0">
              Open Vision Lab
            </ButtonLink>
          </div>
        )}
      </DataSlot>
    </div>
  );
}

function EvidenceKey() {
  return (
    <>
      <h2 className="text-head leading-6 font-medium">Evidence grammar</h2>
      <ul className="m-0 mt-2 list-none p-0">
        {EVIDENCE_KINDS.map((kind) => (
          <li key={kind} className="flex items-center gap-2.5 border-t border-rule-1 py-[3px]">
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
        <h2 className="text-head leading-6 font-medium">Provenance chain</h2>
        <ButtonLink to="/evidence" tone="text" className="font-normal">
          Read the evidence
        </ButtonLink>
      </div>
      <ChainRail stages={stages} />
    </>
  );
}

/** The product's whole point, so it gets the largest corner and the largest words in it. */
function Decision() {
  const { state } = useAnalysis();
  const decision = useDecision();
  const heading = <h2 className="text-head leading-6 font-medium">Decision</h2>;

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
        <p className="mt-3 font-display text-display leading-[0.98] font-medium tracking-[-0.015em] text-paint-1">
          Nothing is recommended
        </p>
        <p className="mt-3 leading-snug text-paint-2">A recommendation needs a validated baseline.</p>
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
        <p className="mt-3 font-display text-display leading-[0.98] font-medium tracking-[-0.015em] text-paint-1">
          Nothing is recommended
        </p>
        <p className="mt-3 leading-snug text-paint-2">No decision record names this analysis.</p>
      </>
    );
  }

  const recommended = paired.outcome.recommended_intervention_id;
  const change = whatWouldChange(paired);
  return (
    <>
      {heading}
      <p className="mt-3 font-display text-display leading-[0.98] font-medium tracking-[-0.015em] text-paint-1">
        {recommended ? `Recommend ${recommended}` : "Nothing is recommended"}
      </p>
      {record.decision_ref?.summary ? <p className="mt-3 leading-snug text-paint-2">{record.decision_ref.summary}</p> : null}
      {change ? <p className="mt-3 leading-snug text-paint-2">{change}</p> : null}
      <div className="mt-auto flex flex-col items-start gap-1 pt-3">
        <EvidenceBadge
          kind={paired.outcome.evidence_kind}
          note={`SUMO, ${paired.candidates.length} candidates, ${paired.simulation.seeds.length} seeds`}
        />
        {record.decision_ref?.linked_to_cv ? null : <Label>From published NGSIM inputs, not this camera analysis</Label>}
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
              className="pointer-events-none absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-baseline gap-1.5 bg-asphalt-0 px-1.5 py-0.5"
              style={place}
            >
              <Label tone="paint">{arm}</Label>
              <Label>{`${lanes.length} lanes in`}</Label>
            </span>
          );
        }
        return (
          <span
            key={arm}
            className="pointer-events-none absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-baseline gap-1.5 bg-asphalt-0 px-1.5 py-0.5"
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

function PlanNotes({ hasSite, loaded }: { hasSite: boolean; loaded: LoadedAnalysis | null }) {
  if (!hasSite) return <EvidenceBadge kind="assumed" note="Schematic, not surveyed" />;
  const counted = loaded && isCalibrated(loaded.record) ? findMetric(loaded.record, "vehicles.arm.n") : null;
  return (
    <>
      <EvidenceBadge kind="approximated" note="Lanes, from trajectories" />
      <EvidenceBadge kind="assumed" note="Road past them" />
      {counted && counted.status !== "gate_fail" ? (
        <EvidenceBadge kind={counted.evidence_kind} note="Entering counts, by track origin" />
      ) : null}
      {counted?.status === "gate_fail" ? (
        <Link
          to="/evidence#gates"
          className="mono text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1 hover:decoration-amber"
        >
          Entering counts withheld by their gate
        </Link>
      ) : null}
      {loaded && !isCalibrated(loaded.record) ? <Label>The plan is the registered junction, not this video</Label> : null}
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

  return (
    <>
      <motion.div layoutId="site-plan" transition={{ duration: DUR.slow, ease: EASE }} className="absolute inset-0">
        <SitePlan
          className="absolute inset-0"
          site={site}
          frame={frame}
          continuation
        />
      </motion.div>
      {state.status === "ready" && isCalibrated(state.loaded.record) ? (
        <ArmCounts loaded={state.loaded} arms={arms} frame={frame} />
      ) : null}
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
        <Instrument className="relative col-start-1 row-start-2 flex flex-wrap items-end gap-x-6 gap-y-1 self-end pr-24 pb-7 pl-margin">
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
              <SitePlan
                className="absolute inset-0"
                site={siteData}
                frame={fitAcross(narrowSize.width, narrowSize.height, NARROW_PLAN_M, 0.5, 0.5)}
                continuation
              />
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
