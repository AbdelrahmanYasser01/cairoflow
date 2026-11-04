import { motion } from "motion/react";
import { DUR, EASE, Label, useElementSize } from "../design";
import type { Withheld } from "../domain/gates";
import type { MetricSeries } from "../lib/analysis";
import { WithheldCheck } from "../instruments/Withheld";

const HEIGHT_PX = 188;
const PLOT = { top: 10, bottom: 26, left: 0, right: 0 };
const TICK_ROOM_PX = 64;

/**
 * Where the entering-flow chart would be. The time axis stays, so the reader sees the window the
 * series covers; the plot itself is struck through and holds no bar, no zero and no estimate.
 */
export function WithheldBand({
  series,
  withheld,
  note,
}: {
  series: MetricSeries;
  withheld: Withheld;
  note: string | null;
}) {
  const [ref, { width }] = useElementSize<HTMLDivElement>();
  const bins = series.values.length;
  const plotW = Math.max(0, width - PLOT.left - PLOT.right);
  const plotH = HEIGHT_PX - PLOT.top - PLOT.bottom;
  const band = bins > 0 ? plotW / bins : 0;
  const every = Math.max(1, Math.ceil(TICK_ROOM_PX / Math.max(band, 1)));
  return (
    <div className="mt-3">
      <div ref={ref} className="relative">
        {width > 0 ? (
          <svg width={width} height={HEIGHT_PX} role="img" aria-label="Entering flow withheld: no bar is drawn.">
            <defs>
              <pattern id="cf-withheld" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="9" stroke="var(--stop)" strokeOpacity="0.22" strokeWidth="1.2" />
              </pattern>
            </defs>
            <motion.rect
              x={PLOT.left}
              y={PLOT.top}
              height={plotH}
              fill="url(#cf-withheld)"
              initial={{ width: 0 }}
              animate={{ width: plotW }}
              transition={{ duration: DUR.draw, ease: EASE }}
            />
            <rect x={PLOT.left} y={PLOT.top} width={plotW} height={plotH} fill="none" stroke="var(--stop)" strokeOpacity="0.35" />
            {Array.from({ length: bins }, (_, index) => (
              <g key={index}>
                {index > 0 ? (
                  <line
                    x1={PLOT.left + index * band}
                    x2={PLOT.left + index * band}
                    y1={PLOT.top}
                    y2={PLOT.top + plotH}
                    stroke="var(--asphalt-0)"
                    strokeWidth={2}
                  />
                ) : null}
                {index % every === 0 ? (
                  <text
                    x={PLOT.left + index * band + 2}
                    y={HEIGHT_PX - 8}
                    fill="var(--paint-3)"
                    fontFamily="var(--font-mono)"
                    fontSize="11"
                  >
                    {series.start_s + index * series.bin_s}
                  </text>
                ) : null}
              </g>
            ))}
          </svg>
        ) : (
          <div style={{ height: HEIGHT_PX }} />
        )}
        <div className="pointer-events-none absolute inset-x-0 top-[10px] flex h-[152px] flex-col items-center justify-center text-center">
          <span className="bg-asphalt-0 px-3 font-display text-num-m leading-none font-medium text-paint-1">Withheld</span>
          {withheld.versus ? (
            <span className="mono mt-2 bg-asphalt-0 px-2 text-stop">{withheld.versus}</span>
          ) : null}
        </div>
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-4">
        <Label>{`${bins} bins of ${series.bin_s} s · none drawn`}</Label>
        <Label>Seconds from start</Label>
      </div>
      <details className="group mt-4 border-t border-rule-1 pt-3">
        <summary className="mono cursor-pointer list-none text-paint-2 hover:text-paint-1">
          <span className="inline-block w-4 transition-transform duration-200 group-open:rotate-90">›</span>
          Why entering flow is withheld
        </summary>
        <div className="mt-3 grid gap-6 pl-4 md:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
          {note ? <p className="m-0 max-w-[38rem] leading-snug text-paint-2">{note}</p> : null}
          <WithheldCheck withheld={withheld} />
        </div>
      </details>
    </div>
  );
}
