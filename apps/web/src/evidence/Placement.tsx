import { motion, useReducedMotion } from "motion/react";
import { EvidenceBadge } from "../design";
import { EASE } from "../design/motion";
import { analysisSpan, measuredCoverage } from "../domain/footage";
import type { AnalysisRecord } from "../lib/analysis";
import { Kicker, SectionHead } from "./parts";

/**
 * Where the run's camera placement was checked, drawn on the source timeline: the analysed span as a
 * track, the measured windows lit on it. The record's own sentence stays one hover away.
 */
export function PlacementBar({ record }: { record: AnalysisRecord }) {
  const still = useReducedMotion();
  const coverage = measuredCoverage(record);
  const [from, to] = analysisSpan(record);
  const total = to - from;
  const windows = (record.calibration.valid_windows_s ?? []).map(([low, high]) => [Number(low), Number(high)] as const);
  const calibrated = record.calibration.mode === "calibrated";
  const at = (value: number) => Math.max(0, Math.min(100, ((value - from) / total) * 100));
  return (
    <section id="placement" aria-label="Placement" className="min-w-0 scroll-mt-24">
      <SectionHead
        title="Placement"
        meta={coverage ? <span className="mono text-micro text-amber" title={coverage.note}>{coverage.label}</span> : null}
      />
      <div className="mt-4 flex items-baseline justify-between gap-3">
        <Kicker>{calibrated ? "Calibrated, approximate" : "Uncalibrated"}</Kicker>
        <span className="mono text-micro text-paint-3">{`source s ${from} to ${to}`}</span>
      </div>
      {calibrated && windows.length > 0 ? (
        <div className="mt-3" title={coverage?.note}>
          <div className="relative h-7 border border-rule-1 bg-[repeating-linear-gradient(135deg,var(--rule-1)_0_1px,transparent_1px_6px)]">
            {windows.map(([low, high], index) => (
              <motion.span
                key={`${low}-${high}`}
                aria-hidden="true"
                className="absolute inset-y-0 origin-left"
                style={{
                  left: `${at(low)}%`,
                  width: `${at(high) - at(low)}%`,
                  background: "color-mix(in srgb, var(--ev-approximated) 55%, transparent)",
                  borderLeft: "1px solid var(--ev-approximated)",
                  borderRight: "1px solid var(--ev-approximated)",
                }}
                initial={still ? false : { scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: true }}
                transition={{ duration: 0.5, ease: EASE, delay: 0.1 + index * 0.06 }}
              />
            ))}
          </div>
          <div className="mono mt-1.5 flex justify-between text-micro text-paint-4" aria-hidden="true">
            <span>{from}</span>
            <span>{to}</span>
          </div>
          <div className="mono mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-paint-3">
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block size-2.5" style={{ background: "color-mix(in srgb, var(--ev-approximated) 55%, transparent)" }} />
              Placement measured
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className="inline-block size-2.5 border border-rule-2 bg-[repeating-linear-gradient(135deg,var(--rule-2)_0_1px,transparent_1px_3px)]" />
              Not checked
            </span>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-label leading-snug text-paint-2">
          {record.calibration.notes?.[0] ?? "No camera calibration: nothing here has a ground position."}
        </p>
      )}
      {coverage ? (
        <div className="mt-4">
          <EvidenceBadge kind="approximated" note="Arms, turns and entering flow rest on this placement" />
        </div>
      ) : null}
    </section>
  );
}
