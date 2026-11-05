import { motion } from "motion/react";
import { Absent, DUR, EASE, Label, Numeral, Popover, StatusMark, Tooltip } from "../design";
import { gateLimit, gateName, gateReading, withheldBy, type Withheld } from "../domain/gates";
import { fragmentCount, metricsUnder, plainReason, readMetric, windowCount } from "../domain/metrics";
import type { AnalysisRecord, Metric } from "../lib/analysis";
import { WithheldCheck } from "../instruments/Withheld";

const CLASS_TONE = ["var(--paint-1)", "var(--paint-3)", "var(--paint-4)", "var(--rule-2)", "var(--asphalt-3)"];

/** Class mix as one proportional bar. Counted tracks only. */
function ClassMix({ record }: { record: AnalysisRecord }) {
  const rows = metricsUnder(record, "vehicles.class.")
    .filter((row) => row.value != null)
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const total = rows.reduce((sum, row) => sum + (row.value ?? 0), 0);
  if (rows.length === 0 || total === 0) return null;
  const fragments = fragmentCount(record);
  return (
    <div className="mt-4">
      <Tooltip
        block
        content={`Class mix of counted tracks.${fragments ? ` ${fragments} fragments under 1 s are left out.` : ""}`}
      >
        <span tabIndex={0} className="flex h-2 w-full gap-px" role="img" aria-label="Class mix of counted tracks">
          {rows.map((row, index) => (
            <motion.span
              key={row.metric_id}
              className="block h-full"
              style={{ background: CLASS_TONE[index] ?? "var(--rule-2)", minWidth: 3 }}
              initial={{ flexGrow: 0 }}
              animate={{ flexGrow: row.value ?? 0 }}
              transition={{ duration: DUR.draw, ease: EASE, delay: 0.2 }}
            />
          ))}
        </span>
      </Tooltip>
      <ul className="m-0 mt-2 flex list-none flex-wrap gap-x-3 gap-y-1 p-0">
        {rows.map((row, index) => (
          <li key={row.metric_id} className="mono flex items-center gap-1.5 text-micro text-paint-3">
            <span aria-hidden="true" className="inline-block size-1.5" style={{ background: CLASS_TONE[index] }} />
            <span className="text-paint-2">{row.label}</span>
            <span className="text-paint-1">{(row.value ?? 0).toLocaleString("en-US")}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A share as a bar of its own length, beside nothing it could be confused with. */
function ShareBar({ share }: { share: number }) {
  return (
    <span aria-hidden="true" className="mt-4 block h-2 w-full bg-rule-1">
      <motion.span
        className="block h-full bg-paint-2"
        initial={{ width: 0 }}
        animate={{ width: `${Math.min(1, Math.max(0, share)) * 100}%` }}
        transition={{ duration: DUR.draw, ease: EASE, delay: 0.2 }}
      />
    </span>
  );
}

/** A value and its unit stay on one line. */
const keep = (words: string) => words.replace(/ /g, "\u00a0");

/** The check that passed this figure, in one line: "Checked · median speed error …, limit …". */
function CheckLine({ record, metric, name }: { record: AnalysisRecord; metric: string; name?: string }) {
  const gate = (record.gates ?? []).find((item) => item.metric === metric && item.passed);
  if (!gate || gate.measured == null) return null;
  return (
    <span className="mono mt-4 block text-micro text-paint-3">
      <span className="text-go">Checked</span>
      {` · ${name ?? gateName(gate.metric).toLowerCase()} ${keep(gateReading(gate.metric, gate.measured))}, limit ${keep(gateLimit(gate.metric, gate.threshold))}`}
    </span>
  );
}

function WithheldValue({ what, withheld }: { what: string; withheld: Withheld }) {
  return (
    <>
      <Popover
        label={`Why ${what} is withheld`}
        trigger={
          <motion.span
            className="inline-block font-display text-num-l leading-none font-medium tracking-[-0.01em] text-paint-1 underline decoration-stop decoration-[3px] underline-offset-[8px]"
            initial={{ y: "0.4em", opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: DUR.slow, ease: EASE }}
          >
            Withheld
          </motion.span>
        }
      >
        <WithheldCheck withheld={withheld} />
      </Popover>
      {withheld.versus ? <span className="mono mt-4 block text-micro text-stop">{withheld.versus}</span> : null}
    </>
  );
}

function Kpi({ metric, record, quiet }: { metric: Metric; record: AnalysisRecord; quiet: boolean }) {
  const { value, unit, format } = readMetric(metric);
  const withheld = withheldBy(record, metric);
  const counted = windowCount(record, metric);
  const label = counted ? "Entering vehicles" : metric.label;
  return (
    <div
      className={`relative min-w-0 px-5 py-4 sm:max-lg:odd:pl-0 lg:first:pl-0 ${withheld ? "max-sm:pr-0 max-sm:pl-4 bg-[repeating-linear-gradient(135deg,transparent_0_7px,color-mix(in_oklab,var(--stop)_9%,transparent)_7px_8px)]" : "max-sm:px-0"}`}
    >
      {withheld ? <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[2px] bg-stop" /> : null}
      <dt className="flex items-center gap-2">
        <Label className="min-w-0 leading-tight whitespace-normal uppercase tracking-[0.08em]">{label}</Label>
        <StatusMark status={metric.status} bare note={metric.reason} />
      </dt>
      <dd className="m-0 mt-3">
        {withheld ? (
          <WithheldValue what={metric.label} withheld={withheld} />
        ) : value === null && !counted ? (
          <>
            <span className="block pt-3 font-display text-num-m leading-none font-medium tracking-[-0.01em] whitespace-nowrap text-paint-4">
              Not available
            </span>
            <span className="mt-4 block">
              <Absent reason={plainReason(metric.reason)} quiet={quiet} className="text-micro" />
            </span>
          </>
        ) : (
          <Numeral
            value={counted ? counted.count : value}
            unit={counted ? `in ${counted.seconds} s` : unit}
            size="l"
            format={format}
            evidence={metric.evidence_kind}
            evidenceNote={metric.reason}
            reason={plainReason(metric.reason)}
          />
        )}
        {metric.metric_id === "vehicles.total" && value !== null ? <ClassMix record={record} /> : null}
        {metric.metric_id === "speed.mean_moving" && metric.status === "gate_pass" ? (
          <CheckLine record={record} metric="speed_abs_error_median_m_s" name="median speed error" />
        ) : null}
        {metric.metric_id === "stopped.share" && metric.value != null ? <ShareBar share={metric.value} /> : null}
      </dd>
    </div>
  );
}

/** Headline figures: large numerals on hairlines, each with its gate status and evidence kind. */
export function Kpis({ metrics, record }: { metrics: readonly Metric[]; record: AnalysisRecord }) {
  // Figures absent for one shared reason say it once, under the row.
  const absent = metrics.filter((metric) => metric.value == null && !withheldBy(record, metric) && !windowCount(record, metric));
  const reasons = new Set(absent.map((metric) => plainReason(metric.reason)));
  const shared = absent.length > 1 && reasons.size === 1 ? ([...reasons][0] as string) : null;
  return (
    <>
      <dl className="m-0 grid grid-cols-1 divide-rule-1 border-y border-rule-2 max-sm:divide-y sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
        {metrics.map((metric) => (
          <Kpi
            key={metric.metric_id}
            metric={metric}
            record={record}
            quiet={shared !== null && absent.includes(metric)}
          />
        ))}
      </dl>
      {shared ? (
        <p className="mono m-0 mt-2 flex items-center justify-end gap-2 text-micro text-paint-3">
          <span aria-hidden="true" className="inline-block h-[2px] w-3 bg-paint-4" />
          {shared.replace(/^Withheld:\s*/, "Not measured: ")}
        </p>
      ) : null}
    </>
  );
}
