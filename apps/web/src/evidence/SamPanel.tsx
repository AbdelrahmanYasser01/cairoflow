import { motion } from "motion/react";
import type { ReactNode } from "react";
import { EvidenceBadge } from "../design";
import { samChange, samMeasured, type SamChange } from "../domain/sam";
import type { AnalysisRecord, LoadedAnalysis } from "../lib/analysis";
import { CheckLamp, Kicker, SectionHead } from "./parts";
import block from "./samBlock.json";

const EASE = [0.22, 1, 0.36, 1] as const;
const whole = (value: number) => value.toLocaleString("en-US");
const share = (value: number) => `${(value * 100).toFixed(1)}%`;
const pct = (value: number, [low, high]: readonly [number, number]) => ((value - low) / (high - low)) * 100;

/** The block figures apply to the run they were measured on, not to any run with a SAM stage. */
function blockFor(record: AnalysisRecord) {
  return samMeasured(record) !== null && record.analysis_id === block.analysis_id ? block : null;
}

/** One share of rows as a filled bar, with the figure at its end. */
function ShareBar({ label, value, tone }: { label: string; value: number; tone: "before" | "after" }) {
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)_3.5rem] items-center gap-3">
      <span className="mono text-micro tracking-[0.1em] text-paint-3 uppercase">{label}</span>
      <span className="relative h-2.5 bg-asphalt-2" aria-hidden="true">
        <motion.span
          className="absolute inset-y-0 left-0"
          style={{ background: tone === "after" ? "var(--ev-model-generated)" : "var(--paint-3)" }}
          initial={{ width: 0 }}
          whileInView={{ width: `${Math.min(100, value * 1000)}%` }}
          viewport={{ once: true }}
          transition={{ duration: 0.9, ease: EASE, delay: tone === "after" ? 0.35 : 0 }}
        />
      </span>
      <span className={`mono text-right text-label ${tone === "after" ? "text-paint-1" : "text-paint-2"}`}>{share(value)}</span>
    </div>
  );
}

/** The left column: what SAM did to the rows, from the record's own stages. */
function Rows({ change }: { change: SamChange }) {
  const { rowsReplaced, rowsInterpolated, interpolatedShareBefore: before, interpolatedShareAfter: after } = change;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div>
        <Kicker>Interpolated rows replaced</Kicker>
        <span className="mt-2 flex items-baseline gap-3">
          <span className="font-display text-num-l leading-none text-paint-1">{rowsReplaced === null ? "None" : whole(rowsReplaced)}</span>
          {rowsInterpolated !== null ? <span className="mono text-label text-paint-3">{`of ${whole(rowsInterpolated)}`}</span> : null}
        </span>
      </div>
      {before !== null && after !== null ? (
        <div className="flex flex-col gap-2" title="Share of all rows that are interpolated, before and after SAM. Bars drawn on a 0 to 10% scale.">
          <Kicker>Rows still interpolated</Kicker>
          <ShareBar label="Before" value={before} tone="before" />
          <ShareBar label="With SAM" value={after} tone="after" />
        </div>
      ) : null}
      <dl className="m-0 grid grid-cols-1 gap-px border border-rule-1 bg-rule-1 sm:grid-cols-3">
        <Readout label="Agreed rows" title="Observed detector rows SAM agreed with. SAM was seeded from these tracks: a consistency check, not confirmation.">
          {change.rowsConfirmed === null ? "None" : whole(change.rowsConfirmed)}
          {change.rowsObserved !== null ? <span className="text-paint-4">{` / ${whole(change.rowsObserved)}`}</span> : null}
        </Readout>
        <Readout label="Box prompts" title="Requests to Meta's Model API, seeded with the detector's boxes.">
          {change.requests === null ? "None" : `${change.requests}`}
          {change.costUsd !== null ? <span className="text-paint-4">{` · ${change.costUsd.toFixed(3)} USD`}</span> : null}
        </Readout>
        <Readout label="Class by mask" title="Tracks whose class comes from SAM's mask length.">
          {`${change.classFromMask}`}
          {change.busesFromMask > 0 ? <span className="text-paint-4">{` · ${change.busesFromMask} bus`}</span> : null}
        </Readout>
      </dl>
    </div>
  );
}

function Readout({ label, title, children }: { label: string; title: string; children: ReactNode }) {
  return (
    <div className="bg-asphalt-0 px-3 py-2.5" title={title}>
      <dt className="mono text-micro tracking-[0.1em] text-paint-3 uppercase">{label}</dt>
      <dd className="mono m-0 mt-1 text-label text-paint-1">{children}</dd>
    </div>
  );
}

/** A before and after pair on one scale: grey for the interpolated position, model colour for SAM's. */
function Shift({
  label,
  before,
  after,
  domain,
  unit,
  verdict,
  better,
  title,
  control,
}: {
  label: string;
  before: number;
  after: number;
  domain: readonly [number, number];
  unit: string;
  verdict: string;
  better: boolean;
  title: string;
  control?: number;
}) {
  const from = pct(before, domain);
  const to = pct(after, domain);
  const tone = better ? "text-go" : "text-stop";
  return (
    <li className="grid grid-cols-1 items-center gap-x-5 gap-y-2 border-b border-rule-1 py-3.5 md:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_7rem]" title={title}>
      <span className="font-display text-head leading-tight text-paint-1">{label}</span>
      <div className="relative">
        <div className="relative h-6">
          <div className="absolute inset-x-0 top-1/2 h-px bg-rule-2" />
          <motion.span
            aria-hidden="true"
            className="absolute top-1/2 h-[3px] -translate-y-1/2"
            style={{ left: `${Math.min(from, to)}%`, background: better ? "var(--go)" : "var(--stop)", opacity: 0.55 }}
            initial={{ width: 0 }}
            whileInView={{ width: `${Math.abs(to - from)}%` }}
            viewport={{ once: true }}
            transition={{ duration: 0.9, ease: EASE, delay: 0.2 }}
          />
          <span aria-hidden="true" className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 border border-paint-2 bg-asphalt-0" style={{ left: `${from}%` }} />
          {control !== undefined ? (
            <span aria-hidden="true" className="absolute top-[2px] bottom-[2px] w-px bg-amber" style={{ left: `${pct(control, domain)}%` }} />
          ) : null}
          <motion.span
            aria-hidden="true"
            className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2"
            style={{ background: "var(--ev-model-generated)", boxShadow: "0 0 10px color-mix(in srgb, var(--ev-model-generated) 70%, transparent)" }}
            initial={{ left: `${from}%` }}
            whileInView={{ left: `${to}%` }}
            viewport={{ once: true }}
            transition={{ duration: 0.9, ease: EASE, delay: 0.2 }}
          />
        </div>
        <div aria-hidden="true" className="relative h-4">
          <span className="mono absolute text-micro whitespace-nowrap text-paint-4" style={{ left: 0 }}>{`${domain[0]}${unit}`}</span>
          {control !== undefined ? (
            <span className="mono absolute -translate-x-1/2 text-micro whitespace-nowrap text-amber" style={{ left: `${pct(control, domain)}%`, top: 0 }}>
              {`control ${control}`}
            </span>
          ) : null}
          <span className="mono absolute -translate-x-full text-micro whitespace-nowrap text-paint-4" style={{ left: "100%" }}>{`${domain[1]}${unit}`}</span>
        </div>
      </div>
      <span className="flex flex-col items-start gap-0.5 md:items-end">
        <span className="mono text-label whitespace-nowrap text-paint-1">
          <span className="text-paint-3">{before}</span>
          <span className="text-paint-4">{" → "}</span>
          {`${after}${unit}`}
        </span>
        <span className={`mono text-micro whitespace-nowrap ${tone}`}>{verdict}</span>
      </span>
    </li>
  );
}

/** The right column: the replaced rows against the published reference, on the dev block. */
function Against() {
  const { across_road_error_m: across, along_road_error_m: along, recall } = block;
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Kicker>Against the reference · dev block</Kicker>
        <span className="mono text-micro text-paint-3" title={`Measured record ${block.measured_record.path}. ${block.label}.`}>
          agreement, not accuracy
        </span>
      </div>
      <ul className="m-0 mt-2 list-none border-t border-rule-1 p-0">
        <Shift
          label="Across-road error"
          before={across.interpolated_position}
          after={across.sam_position}
          domain={[0, 0.8]}
          unit=" m"
          verdict="tighter"
          better={across.sam_position < across.interpolated_position}
          title={`Median across-road error on the ${block.replaced_rows} replaced rows: interpolated position against SAM's box.`}
        />
        <Shift
          label="Along-road error"
          before={along.interpolated_position}
          after={along.sam_position}
          domain={[0, 0.8]}
          unit=" m"
          verdict="no gain"
          better={along.sam_position < along.interpolated_position}
          title={`Median along-road error on the same ${block.replaced_rows} rows.`}
        />
        <Shift
          label="Recall"
          before={recall.before}
          after={recall.with_sam_boxes}
          domain={[0.7, 0.9]}
          unit=""
          verdict="same as control"
          better={false}
          control={recall.same_rows_interpolated}
          title={`Scored recall rose from ${recall.before} to ${recall.with_sam_boxes}. A control that keeps the same rows at their interpolated positions scores ${recall.same_rows_interpolated}.`}
        />
      </ul>
      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
        <CheckLamp ok={false} title="The control scores almost the same: the gain comes from counting the rows, not from SAM's positions.">
          SAM did not raise recall
        </CheckLamp>
        <span className="mono text-label text-paint-3">{`Rows matched: ${block.replaced_rows_matched} of ${block.replaced_rows}, either way`}</span>
      </div>
    </div>
  );
}

/** What Meta's SAM 3.1 did on its one window, drawn as before and after. */
export function SamPanel({ loaded, index }: { loaded: LoadedAnalysis; index?: string | undefined }) {
  const change = samChange(loaded.record, loaded.tracks);
  if (!change) return null;
  const measured = blockFor(loaded.record);
  return (
    <section id="what-sam-changed" className="scroll-mt-24">
      <SectionHead
        index={index}
        title="What SAM changed"
        meta={
          <>
            <span className="mono hidden text-micro whitespace-nowrap text-paint-3 sm:inline">Seeded by detector boxes</span>
            <EvidenceBadge kind="model_generated" note="Not ground truth" />
          </>
        }
      />
      <div className={`mt-5 grid grid-cols-1 gap-x-10 gap-y-8 ${measured ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]" : ""}`}>
        <Rows change={change} />
        {measured ? <Against /> : null}
      </div>
      <details className="group mt-5 border-t border-rule-1 pt-3">
        <summary className="mono cursor-pointer text-label text-paint-3 hover:text-paint-1">Known limits of the refinement</summary>
        <ul className="m-0 mt-2 grid list-none gap-1.5 p-0 text-paint-2">
          <li>SAM follows only what the detector seeded. Known misses remain.</li>
          <li>Agreed rows are a consistency check, not confirmation.</li>
          {measured ? <li>{`Figures: ${measured.scope}; ${measured.label}.`}</li> : null}
        </ul>
      </details>
    </section>
  );
}
