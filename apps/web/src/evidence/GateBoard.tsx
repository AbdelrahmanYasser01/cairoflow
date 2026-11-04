import { motion, useReducedMotion } from "motion/react";
import { EmptyState, StatusMark } from "../design";
import { EASE } from "../design/motion";
import {
  comparatorWord,
  figuresWithheld,
  gateGloss,
  gateLimit,
  gateName,
  gateReading,
  gatesPassed,
  limitWords,
  measuredWords,
  recordWords,
  withheldBy,
} from "../domain/gates";
import type { AnalysisRecord, GateOutcome } from "../lib/analysis";
import { Kicker, SectionHead } from "./parts";

type Unit = "share" | "m" | "m/s";

const unitOf = (metric: string): Unit => (metric.endsWith("_m_s") ? "m/s" : metric.endsWith("_m") ? "m" : "share");

/** The scale a check is drawn on: a share runs 0 to 100%, a distance or speed to twice its limit. */
function domainOf(gate: GateOutcome): [number, number] {
  return unitOf(gate.metric) === "share" ? [0, 1] : [0, gate.threshold * 2];
}

const pct = (value: number, [low, high]: [number, number]) =>
  Math.max(0, Math.min(100, ((value - low) / (high - low)) * 100));

/** The tick words on a scale, in the check's own unit. */
function tickWords(gate: GateOutcome, value: number): string {
  const unit = unitOf(gate.metric);
  return unit === "share" ? `${Number((value * 100).toFixed(1))}%` : `${Number(value.toFixed(2))}`;
}

function Needle({ at, failed, delay }: { at: number; failed: boolean; delay: number }) {
  const still = useReducedMotion();
  const colour = failed ? "var(--stop)" : "var(--paint-1)";
  return (
    <>
      <motion.span
        aria-hidden="true"
        className="absolute top-1/2 left-0 h-[3px] -translate-y-1/2"
        style={{ background: failed ? "var(--stop)" : "var(--paint-3)" }}
        initial={still ? false : { width: "0%" }}
        animate={{ width: `${at}%` }}
        transition={{ duration: 1.1, ease: EASE, delay }}
      />
      <motion.span
        aria-hidden="true"
        className="absolute top-[-5px] bottom-[-5px] w-[2px] -translate-x-1/2"
        style={{ background: colour, boxShadow: failed ? "0 0 10px var(--stop)" : "none" }}
        initial={still ? false : { left: "0%" }}
        animate={{ left: `${at}%` }}
        transition={{ duration: 1.1, ease: EASE, delay }}
      />
    </>
  );
}

/** One check as a scale: the passing side calm, the failing side hatched, the reading as a needle. */
function Scale({
  gate,
  domain,
  delay,
  ticks,
}: {
  gate: GateOutcome;
  domain: [number, number];
  delay: number;
  ticks: readonly number[];
}) {
  const limitAt = pct(gate.threshold, domain);
  const at = gate.measured == null ? null : pct(gate.measured, domain);
  const atLeast = gate.comparator === ">=";
  const failed = !gate.passed;
  return (
    <div className="relative h-11">
      <div className="absolute inset-x-0 top-3 h-3.5 border-y border-rule-1 bg-asphalt-1">
        {/* The side of the limit that fails, hatched. */}
        <span
          aria-hidden="true"
          className="absolute inset-y-0"
          style={{
            left: atLeast ? "0%" : `${limitAt}%`,
            width: atLeast ? `${limitAt}%` : `${100 - limitAt}%`,
            backgroundImage: `repeating-linear-gradient(135deg, ${failed ? "rgba(240,82,79,0.28)" : "var(--rule-1)"} 0 1px, transparent 1px 5px)`,
          }}
        />
        {/* The side that passes, calm. */}
        <span
          aria-hidden="true"
          className="absolute inset-y-0"
          style={{
            left: atLeast ? `${limitAt}%` : "0%",
            width: atLeast ? `${100 - limitAt}%` : `${limitAt}%`,
            background: "rgba(51,195,123,0.07)",
          }}
        />
        {at === null ? null : <Needle at={at} failed={failed} delay={delay} />}
        <span
          aria-hidden="true"
          className="absolute top-[-7px] bottom-[-7px] w-px bg-amber"
          style={{ left: `${limitAt}%` }}
        />
      </div>
      <div aria-hidden="true" className="absolute inset-x-0 top-[34px]">
        {ticks.map((tick, index) => (
          <span
            key={tick}
            className={`mono absolute text-micro leading-none text-paint-4 ${index === 0 ? "" : index === ticks.length - 1 ? "-translate-x-full" : "-translate-x-1/2"}`}
            style={{ left: `${pct(tick, domain)}%` }}
          >
            {tickWords(gate, tick)}
          </span>
        ))}
      </div>
    </div>
  );
}

/** A half-width for the magnified scale: six misses wide, rounded up to one significant figure. */
function loupeHalf(miss: number, unit: Unit): number {
  const raw = Math.max(miss * 6, unit === "share" ? 0.01 : 0.05);
  const step = 10 ** Math.floor(Math.log10(raw));
  return Math.ceil(raw / step) * step;
}

/** The failed check magnified around its limit, so a near miss reads as one. */
function Loupe({ gate }: { gate: GateOutcome }) {
  if (gate.measured == null) return null;
  const unit = unitOf(gate.metric);
  const miss = Math.abs(gate.measured - gate.threshold);
  const half = loupeHalf(miss, unit);
  const domain: [number, number] = [gate.threshold - half, gate.threshold + half];
  const limitAt = pct(gate.threshold, domain);
  const at = pct(gate.measured, domain);
  const left = Math.min(limitAt, at);
  const width = Math.abs(at - limitAt);
  const zoom = Math.round((domainOf(gate)[1] - domainOf(gate)[0]) / (2 * half));
  return (
    <div className="relative mt-2 border border-stop/40 bg-[rgba(240,82,79,0.05)] px-4 pt-2.5 pb-6">
      <div className="flex items-baseline justify-between gap-3">
        <span className="mono text-micro tracking-[0.1em] whitespace-nowrap text-stop uppercase">{`Magnified ×${zoom}`}</span>
        <span className="mono text-micro whitespace-nowrap text-paint-3">
          <span className="text-stop">{`+${measuredWords(miss)}`}</span> over the limit
        </span>
      </div>
      <div className="relative mt-4 h-4">
        <div className="absolute inset-x-0 top-1/2 h-px bg-rule-2" />
        {[-1, -0.5, 0.5, 1].map((step) => (
          <span key={step} aria-hidden="true" className="absolute top-[5px] h-[6px] w-px bg-paint-4" style={{ left: `${50 + step * 50}%` }} />
        ))}
        <span aria-hidden="true" className="absolute top-[-6px] bottom-[-6px] w-px bg-amber" style={{ left: `${limitAt}%` }} />
        <motion.span
          aria-hidden="true"
          className="absolute top-1/2 h-[6px] -translate-y-1/2"
          style={{ left: `${left}%`, background: "var(--stop)", boxShadow: "0 0 14px rgba(240,82,79,0.75)" }}
          initial={{ width: 0 }}
          whileInView={{ width: `${width}%` }}
          viewport={{ once: true }}
          transition={{ duration: 0.9, ease: EASE, delay: 0.6 }}
        />
        <span aria-hidden="true" className="absolute top-[-8px] bottom-[-8px] w-[2px] -translate-x-1/2 bg-stop" style={{ left: `${at}%` }} />
      </div>
      <div aria-hidden="true" className="relative mt-1.5">
        {[domain[0], gate.threshold, domain[1]].map((tick, index) => (
          <span
            key={tick}
            className={`mono absolute text-micro leading-none whitespace-nowrap ${index === 1 ? "-translate-x-1/2 text-amber" : "text-paint-4"} ${index === 2 ? "-translate-x-full" : ""}`}
            style={{ left: `${pct(tick, domain)}%` }}
          >
            {index === 1 ? `limit ${tickWords(gate, tick)}` : tickWords(gate, tick)}
          </span>
        ))}
      </div>
    </div>
  );
}

function GateRow({ gate, index }: { gate: GateOutcome; index: number }) {
  const domain = domainOf(gate);
  const failed = !gate.passed;
  const ticks = unitOf(gate.metric) === "share" ? [0, 0.5, 1] : [0, gate.threshold, gate.threshold * 2];
  const hint = `${gate.metric} ${gate.comparator} ${limitWords(gate.threshold)}, measured ${gate.measured == null ? "nothing" : measuredWords(gate.measured)}. ${gateGloss(gate.metric) ?? ""}`;
  return (
    <li
      className={`relative grid grid-cols-1 items-center gap-x-6 gap-y-2 border-b border-rule-1 py-3.5 pr-2 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_8rem_7.5rem] ${
        failed ? "bg-[linear-gradient(90deg,rgba(240,82,79,0.10),rgba(240,82,79,0)_70%)] pl-4" : "pl-4"
      }`}
    >
      {failed ? <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[3px] bg-stop" /> : null}
      <div className="min-w-0" title={hint}>
        <span className="block font-display text-head leading-tight text-paint-1">
          {gateName(gate.metric)}
        </span>
        <span className="mono mt-0.5 block text-micro text-paint-3">{`n ${gate.n.toLocaleString("en-US")}${gate.split === "held_out" ? "" : " · dev"}`}</span>
      </div>
      <div className="min-w-0">
        <Scale gate={gate} domain={domain} delay={0.15 + index * 0.07} ticks={ticks} />
        {failed ? <Loupe gate={gate} /> : null}
      </div>
      <div className="text-right">
        <span className={`mono block text-num-s leading-none ${failed ? "text-stop" : "text-paint-1"}`}>
          {gate.measured == null ? <span className="text-paint-3">None</span> : gateReading(gate.metric, gate.measured)}
        </span>
        <span className="mono mt-1.5 block text-micro text-paint-3">
          {comparatorWord(gate.comparator)} <span className="text-paint-2">{gateLimit(gate.metric, gate.threshold)}</span>
        </span>
      </div>
      <div className="flex justify-end whitespace-nowrap">
        <StatusMark status={failed ? "gate_fail" : "gate_pass"} />
      </div>
    </li>
  );
}

/** The registered checks read for this analysis, drawn as instruments. */
export function GateBoard({ record, index }: { record: AnalysisRecord; index?: string }) {
  const gates = record.gates ?? [];
  const { passed, total } = gatesPassed(record);
  const withheld = figuresWithheld(record);
  const failed = gates.find((gate) => !gate.passed);
  const held = failed ? withheldBy(record, { status: "gate_fail", gate_id: failed.gate_id }) : null;
  const standing = record.disclosures.find((line) => line.startsWith(`Gate ${gates[0]?.gate_id}`));
  const allHeldOut = gates.every((gate) => gate.split === "held_out");
  return (
    <section id="gates" className="scroll-mt-24">
      <SectionHead index={index} title="Gates" meta={gates[0] ? <span className="mono text-label text-paint-3">{gates[0].gate_id}</span> : null} />
      {gates.length === 0 ? (
        <EmptyState
          className="mt-3"
          title="No gate read for this analysis"
          reason="Its figures stay ungated until a registered gate compares them with a reference."
        />
      ) : (
        <div className="mt-4 grid grid-cols-1 gap-x-10 gap-y-6 xl:grid-cols-[17rem_minmax(0,1fr)]">
          <div className="flex flex-col gap-5">
            <div>
              <Kicker>Checks passed</Kicker>
              <p className="mt-2 font-display leading-[0.85] font-medium tracking-[-0.02em] text-paint-1">
                <span className="text-[112px]">{passed}</span>
                <span className="text-[56px] text-paint-4">{` / ${total}`}</span>
              </p>
              <div className="mt-3 flex gap-1" aria-hidden="true">
                {gates.map((gate, index) => (
                  <motion.span
                    key={gate.metric}
                    className={`h-1.5 flex-1 origin-left ${gate.passed ? "bg-go/70" : "bg-stop shadow-[0_0_10px_rgba(240,82,79,0.8)]"}`}
                    initial={{ scaleX: 0 }}
                    animate={{ scaleX: 1 }}
                    transition={{ duration: 0.4, ease: EASE, delay: 0.15 + index * 0.07 }}
                  />
                ))}
              </div>
              <p className="mono mt-3 text-label text-paint-3">
                {allHeldOut ? "Held-out blocks · limits fixed first" : "Limits fixed before the read"}
              </p>
            </div>
            {failed && held ? (
              <div className="border-t border-rule-2 pt-4">
                <Kicker tone="stop">Withheld</Kicker>
                <p className="mt-2 font-display text-head leading-tight text-paint-1">{withheld.join(" · ")}</p>
                <p className="mt-1.5 text-label leading-snug text-paint-2">
                  {`${gateName(failed.metric)} missed its limit. No value shown.`}
                </p>
              </div>
            ) : null}
          </div>
          <div className="min-w-0">
            <div className="mono hidden grid-cols-[minmax(0,14rem)_minmax(0,1fr)_8rem_7.5rem] gap-x-6 border-b border-rule-2 pr-2 pb-2 pl-4 text-micro tracking-[0.08em] text-paint-3 uppercase md:grid">
              <span>Check</span>
              <span>Scale · limit in amber</span>
              <span className="text-right">Measured · limit</span>
              <span className="text-right">Outcome</span>
            </div>
            <ul className="m-0 list-none p-0" aria-label="Registered gates read for this analysis">
              {gates.map((gate, index) => (
                <GateRow key={`${gate.gate_id}-${gate.metric}-${gate.scope}`} gate={gate} index={index} />
              ))}
            </ul>
            {standing ? (
              <details className="group mt-3">
                <summary className="mono cursor-pointer list-none text-label text-paint-3 hover:text-paint-1">
                  <span className="mr-2 inline-block transition-transform group-open:rotate-90">›</span>
                  Agreement with NGSIM, not accuracy
                </summary>
                <p className="mt-2 max-w-[60rem] text-label leading-relaxed text-paint-2">{recordWords(standing)}</p>
                {held ? <p className="mt-1 max-w-[60rem] text-label leading-relaxed text-paint-3">{held.limitNote}</p> : null}
              </details>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
