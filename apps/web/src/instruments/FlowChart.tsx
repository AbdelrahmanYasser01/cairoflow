import { useState } from "react";
import { Label, useElementSize } from "../design";
import { niceAxis, type Bin } from "../domain/metrics";

const HEIGHT_PX = 232;
const PLOT = { top: 22, right: 0, bottom: 26, left: 34 };
/** Minimum room for one x-axis label, in pixels. */
const TICK_ROOM_PX = 56;

/**
 * Entering vehicles per bin. Columns are the whole junction; picking an arm draws that arm in amber
 * against the whole. A bin with no value is left empty, never drawn as zero.
 */
export function FlowChart({
  bins,
  binS,
  picked,
}: {
  bins: readonly Bin[];
  binS: number;
  /** The name of the arm picked out, when a filter is on. */
  picked: string | null;
}) {
  const [ref, { width }] = useElementSize<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  const totals = bins.map((bin) => bin.total ?? 0);
  const { top, step } = niceAxis(Math.max(...totals, 0));
  const plotW = Math.max(0, width - PLOT.left - PLOT.right);
  const plotH = HEIGHT_PX - PLOT.top - PLOT.bottom;
  const band = bins.length > 0 ? plotW / bins.length : 0;
  const bar = Math.max(2, band * 0.46);
  const y = (value: number) => PLOT.top + plotH - (value / top) * plotH;
  const x = (index: number) => PLOT.left + index * band + (band - bar) / 2;

  const ticks: number[] = [];
  for (let value = 0; value <= top; value += step) ticks.push(value);
  const every = Math.max(1, Math.ceil(TICK_ROOM_PX / Math.max(band, 1)));

  const measure = (bin: Bin) => (picked ? bin.selected : bin.total);
  const peak = bins.reduce<number | null>((best, bin, index) => {
    const value = measure(bin);
    if (value === null) return best;
    const held = best === null ? null : measure(bins[best] as Bin);
    return held === null || value > held ? index : best;
  }, null);
  const shown = hover ?? peak;
  const summary = picked ? `${picked} against all arms` : "All arms";

  return (
    <div ref={ref} className="relative mt-3">
      {width > 0 ? (
        <svg
          width={width}
          height={HEIGHT_PX}
          viewBox={`0 0 ${width} ${HEIGHT_PX}`}
          role="img"
          aria-label={`Entering vehicles per ${binS} seconds. ${summary}. The table after this chart lists every value.`}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={PLOT.left}
                x2={width}
                y1={y(tick)}
                y2={y(tick)}
                stroke={tick === 0 ? "var(--paint-4)" : "var(--rule-1)"}
              />
              <text
                x={PLOT.left - 8}
                y={y(tick) + 4}
                textAnchor="end"
                fill="var(--paint-3)"
                fontFamily="var(--font-mono)"
                fontSize="11"
              >
                {tick}
              </text>
            </g>
          ))}
          {bins.map((bin, index) => (
            <g key={bin.startS}>
              {bin.total !== null ? (
                <rect
                  x={x(index)}
                  y={y(bin.total)}
                  width={bar}
                  height={y(0) - y(bin.total)}
                  fill={picked ? "var(--paint-4)" : shown === index ? "var(--paint-1)" : "var(--paint-3)"}
                />
              ) : null}
              {picked && bin.selected !== null ? (
                <rect
                  x={x(index)}
                  y={y(bin.selected)}
                  width={bar}
                  height={y(0) - y(bin.selected)}
                  fill="var(--amber)"
                />
              ) : null}
              {index % every === 0 ? (
                <text
                  x={x(index) + bar / 2}
                  y={HEIGHT_PX - 8}
                  textAnchor="middle"
                  fill="var(--paint-3)"
                  fontFamily="var(--font-mono)"
                  fontSize="11"
                >
                  {bin.startS}
                </text>
              ) : null}
              <rect
                x={PLOT.left + index * band}
                y={PLOT.top}
                width={band}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
              />
            </g>
          ))}
          {shown !== null && measure(bins[shown] as Bin) !== null ? (
            <text
              x={x(shown) + bar / 2}
              y={y(measure(bins[shown] as Bin) as number) - 6}
              textAnchor="middle"
              fill="var(--paint-1)"
              fontFamily="var(--font-mono)"
              fontSize="12"
            >
              {measure(bins[shown] as Bin)}
            </text>
          ) : null}
        </svg>
      ) : (
        <div style={{ height: HEIGHT_PX }} />
      )}

      <div className="mt-1 flex items-baseline justify-between gap-4">
        <span className="flex items-center gap-4">
          {picked ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="size-2 bg-amber" />
                <Label tone="body">{picked}</Label>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden="true" className="size-2 bg-paint-4" />
                <Label tone="body">All arms</Label>
              </span>
            </>
          ) : (
            <Label tone="body">All arms</Label>
          )}
        </span>
        <Label>Seconds from the start of the analysis</Label>
      </div>

      <table className="sr-only">
        <caption>Entering vehicles per {binS} seconds</caption>
        <thead>
          <tr>
            <th scope="col">Start, s</th>
            <th scope="col">All arms</th>
            {picked ? <th scope="col">{picked}</th> : null}
          </tr>
        </thead>
        <tbody>
          {bins.map((bin) => (
            <tr key={bin.startS}>
              <td>{bin.startS}</td>
              <td>{bin.total ?? "No value"}</td>
              {picked ? <td>{bin.selected ?? "No value"}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
