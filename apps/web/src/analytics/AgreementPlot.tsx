import { motion } from "motion/react";
import { DUR, EASE, EVIDENCE, Label, useElementSize } from "../design";
import { gateLimit, gateName, gateReading } from "../domain/gates";
import type { AnalysisRecord, MovementCell } from "../lib/analysis";
import { movementTitle, type Pairing } from "./JunctionFlows";
import { movementKey } from "./junction";

const HEIGHT_PX = 236;
const PAD = { top: 12, right: 12, bottom: 30, left: 40 };
const TICKS = [1, 10, 100, 1000];

/**
 * Camera count against the published count, one point per movement, on log axes. The diagonal is
 * where the two agree. Both figures are the record's; nothing here is a computed difference.
 */
export function AgreementPlot({
  record,
  movements,
  pairing,
  active,
  onActive,
}: {
  record: AnalysisRecord;
  movements: readonly MovementCell[];
  pairing: Pairing;
  active: string | null;
  onActive: (key: string | null) => void;
}) {
  const [ref, { width }] = useElementSize<HTMLDivElement>();
  const points = movements.filter(
    (cell) => (cell.count ?? 0) > 0 && (cell.reference_count ?? 0) > 0,
  ) as (MovementCell & { count: number; reference_count: number })[];
  if (!pairing.paired || points.length === 0) return null;

  const largest = Math.max(...points.map((cell) => Math.max(cell.count, cell.reference_count)));
  const top = 10 ** Math.ceil(Math.log10(largest * 1.15));
  const ticks = TICKS.filter((tick) => tick <= top);
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = HEIGHT_PX - PAD.top - PAD.bottom;
  const scale = (value: number) => Math.log10(Math.max(1, value)) / Math.log10(top);
  const x = (value: number) => PAD.left + scale(value) * plotW;
  const y = (value: number) => PAD.top + plotH - scale(value) * plotH;
  const colour = EVIDENCE[pairing.kind].colour;
  const gate = (record.gates ?? []).find((item) => item.metric === "turning_matrix_agreement");

  return (
    <div className="mt-6">
      <div className="flex items-baseline justify-between gap-3 border-b border-rule-1 pb-2">
        <Label tone="body">Camera vs NGSIM, per movement</Label>
        {gate && gate.measured != null ? (
          <span className="mono text-micro text-paint-3">
            <span className={gate.passed ? "text-go" : "text-stop"}>{gate.passed ? "Checked" : "Failed"}</span>
            {` · ${gateReading(gate.metric, gate.measured)}, limit ${gateLimit(gate.metric, gate.threshold)}`}
          </span>
        ) : null}
      </div>
      <div ref={ref} className="relative mt-2" onMouseLeave={() => onActive(null)}>
        {width > 0 ? (
          <svg
            width={width}
            height={HEIGHT_PX}
            role="img"
            aria-label={`${pairing.main} count against the published NGSIM count for each movement, on log axes.${gate ? ` ${gateName(gate.metric)} is checked pooled, not per movement.` : ""}`}
          >
            {ticks.map((tick) => (
              <g key={tick} fontFamily="var(--font-mono)" fontSize="11" fill="var(--paint-3)">
                <line x1={x(tick)} x2={x(tick)} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--rule-1)" />
                <line x1={PAD.left} x2={PAD.left + plotW} y1={y(tick)} y2={y(tick)} stroke="var(--rule-1)" />
                <text x={x(tick)} y={HEIGHT_PX - 14} textAnchor="middle">
                  {tick}
                </text>
                <text x={PAD.left - 8} y={y(tick) + 4} textAnchor="end">
                  {tick}
                </text>
              </g>
            ))}
            <line
              x1={x(1)}
              y1={y(1)}
              x2={x(top)}
              y2={y(top)}
              stroke="var(--paint-4)"
              strokeDasharray="4 4"
            />
            <text
              x={x(top) - 4}
              y={y(top) + 14}
              textAnchor="end"
              fontFamily="var(--font-mono)"
              fontSize="11"
              fill="var(--paint-4)"
            >
              equal
            </text>
            {points.map((cell, index) => {
              const key = movementKey(cell);
              const on = active === key;
              const dim = active !== null && !on;
              return (
                <g key={key} onMouseEnter={() => onActive(key)}>
                  <circle cx={x(cell.reference_count)} cy={y(cell.count)} r={12} fill="transparent" />
                  <motion.circle
                    cx={x(cell.reference_count)}
                    cy={y(cell.count)}
                    r={on ? 6.5 : 4.5}
                    fill={colour}
                    stroke={on ? "var(--amber)" : "var(--asphalt-0)"}
                    strokeWidth={on ? 2 : 1}
                    initial={{ opacity: 0, scale: 0 }}
                    animate={{ opacity: dim ? 0.25 : 0.95, scale: 1 }}
                    transition={{ duration: DUR.slow, ease: EASE, delay: 0.3 + index * 0.04 }}
                  >
                    <title>{`${movementTitle(cell)}: camera ${cell.count}, NGSIM ${cell.reference_count}`}</title>
                  </motion.circle>
                </g>
              );
            })}
          </svg>
        ) : (
          <div style={{ height: HEIGHT_PX }} />
        )}
        <div className="flex items-baseline justify-between">
          <Label>↑ Camera</Label>
          <Label>NGSIM published →</Label>
        </div>
      </div>
    </div>
  );
}
