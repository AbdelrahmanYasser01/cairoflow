import { Absent, Label, Numeral, StatusMark } from "../design";
import { isPlacementMetric, measuredCoverage } from "../domain/footage";
import { withheldBy } from "../domain/gates";
import { plainReason, readMetric, windowCount } from "../domain/metrics";
import type { AnalysisRecord, Metric } from "../lib/analysis";
import { OutsideWindow } from "./Receipts";
import { WithheldFigure } from "./Withheld";

/**
 * One figure as the record gives it: its label, value, unit, evidence kind and gate status. A value
 * that failed its gate stands in the same place, at the same size, as the word that says so. An
 * hourly rate with no passed gate gives way to what was counted, and the window it was counted in.
 */
export function Readout({
  metric,
  record,
  size = "m",
  grouped = false,
}: {
  metric: Metric;
  record: AnalysisRecord;
  size?: "s" | "m";
  /** The reason for this absent figure is stated once for the row, under it. */
  grouped?: boolean;
}) {
  const { value, unit, format } = readMetric(metric);
  const withheld = withheldBy(record, metric);
  const counted = windowCount(record, metric);
  return (
    <div className="min-w-0">
      <dt className="flex items-center gap-2">
        <Label className="min-w-0">{counted ? "Entering vehicles" : metric.label}</Label>
        <StatusMark status={metric.status} bare note={metric.reason} />
      </dt>
      <dd className="m-0 mt-1.5">
        {withheld ? (
          <WithheldFigure what={metric.label} withheld={withheld} size={size} />
        ) : grouped ? (
          <Absent reason="Withheld" />
        ) : (
          <Numeral
            value={counted ? counted.count : value}
            unit={counted ? `in ${counted.seconds} s` : unit}
            size={size}
            format={format}
            evidence={metric.evidence_kind}
            evidenceNote={metric.reason}
            reason={plainReason(metric.reason)}
          />
        )}
      </dd>
    </div>
  );
}

/** The one reason more than one figure in a row shares, so it is said once. */
function sharedReason(metrics: readonly Metric[], record: AnalysisRecord): string | null {
  const absent = metrics.filter((metric) => metric.value == null && !withheldBy(record, metric));
  const reasons = new Set(absent.map((metric) => plainReason(metric.reason)));
  return absent.length > 1 && reasons.size === 1 ? ([...reasons][0] as string) : null;
}

/** Figures in a line on a hairline. Not tiles: there is no box and no accent. */
export function Readouts({
  metrics,
  record,
  className = "",
  size = "m",
}: {
  metrics: readonly Metric[];
  record: AnalysisRecord;
  className?: string;
  size?: "s" | "m";
}) {
  const shared = sharedReason(metrics, record);
  const placed = metrics.some((metric) => metric.value != null && isPlacementMetric(metric.metric_id));
  const coverage = placed ? measuredCoverage(record) : null;
  return (
    <>
      <dl className={`m-0 ${className}`}>
        {metrics.map((metric) => (
          <Readout
            key={metric.metric_id}
            metric={metric}
            record={record}
            size={size}
            grouped={shared !== null && metric.value == null}
          />
        ))}
      </dl>
      {shared ? <p className="mono mt-2 basis-full text-paint-3">{shared}</p> : null}
      {coverage ? (
        <p className="mt-2 basis-full">
          <OutsideWindow coverage={coverage} />
        </p>
      ) : null}
    </>
  );
}
