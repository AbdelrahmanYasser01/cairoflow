import { motion, useReducedMotion } from "motion/react";
import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { DUR, EASE } from "./motion";

export interface PlanGeometry {
  width: number;
  height: number;
  /** Junction centre, in the same pixels as width and height. */
  cx: number;
  cy: number;
  /** Half the carriageway width of the north-south and east-west roads. */
  halfNs: number;
  halfEw: number;
}

export function useElementSize<T extends HTMLElement>(): [RefObject<T | null>, { width: number; height: number }] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => setSize({ width: node.clientWidth, height: node.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, size];
}

/**
 * US road paint as a schematic: kerbs, double yellow centrelines, lane dashes, stop bars and
 * ladder crosswalks, right-hand traffic. It is a drawing of a junction and not a survey of the
 * site, so anything showing it must carry an `assumed` badge saying so.
 */
export function PlanDrawing({
  geometry,
  labels,
  drawIn = true,
  className = "",
}: {
  geometry: PlanGeometry;
  /** Street names, set in a lane the way a map does. */
  labels?: { ns?: string; ew?: string };
  drawIn?: boolean;
  className?: string;
}) {
  const reduced = useReducedMotion();
  const animate = drawIn && !reduced;
  const { width: w, height: h, cx, cy, halfNs: ns, halfEw: ew } = geometry;
  if (w === 0 || h === 0) return null;

  const corner = Math.min(ns, ew) * 0.42;
  const zone = Math.min(ns, ew) * 0.62; // depth of the crosswalk and stop bar zone
  const gap = 2.5; // half the gap between the two yellow lines
  const nsLanes = 3;

  const kerbs = [
    `M${cx + ns} 0V${cy - ew - corner}Q${cx + ns} ${cy - ew} ${cx + ns + corner} ${cy - ew}H${w}`,
    `M${cx - ns} 0V${cy - ew - corner}Q${cx - ns} ${cy - ew} ${cx - ns - corner} ${cy - ew}H0`,
    `M${cx + ns} ${h}V${cy + ew + corner}Q${cx + ns} ${cy + ew} ${cx + ns + corner} ${cy + ew}H${w}`,
    `M${cx - ns} ${h}V${cy + ew + corner}Q${cx - ns} ${cy + ew} ${cx - ns - corner} ${cy + ew}H0`,
  ];

  const north = cy - ew - zone;
  const south = cy + ew + zone;
  const west = cx - ns - zone;
  const east = cx + ns + zone;

  const centrelines = [
    `M${cx - gap} 0V${north}`,
    `M${cx + gap} 0V${north}`,
    `M${cx - gap} ${h}V${south}`,
    `M${cx + gap} ${h}V${south}`,
    `M0 ${cy - gap}H${west}`,
    `M0 ${cy + gap}H${west}`,
    `M${w} ${cy - gap}H${east}`,
    `M${w} ${cy + gap}H${east}`,
  ];

  const laneDashes: string[] = [];
  for (let lane = 1; lane < nsLanes; lane += 1) {
    const offset = (ns / nsLanes) * lane;
    laneDashes.push(`M${cx - offset} 0V${north}`, `M${cx + offset} 0V${north}`);
    laneDashes.push(`M${cx - offset} ${h}V${south}`, `M${cx + offset} ${h}V${south}`);
  }
  laneDashes.push(`M0 ${cy - ew / 2}H${west}`, `M0 ${cy + ew / 2}H${west}`);
  laneDashes.push(`M${w} ${cy - ew / 2}H${east}`, `M${w} ${cy + ew / 2}H${east}`);

  // Right-hand traffic: each stop bar spans only the approach half of its arm.
  const stopBars = [
    `M${cx - ns + 3} ${north}H${cx - gap - 5}`,
    `M${cx + gap + 5} ${south}H${cx + ns - 3}`,
    `M${east} ${cy - ew + 3}V${cy - gap - 5}`,
    `M${west} ${cy + gap + 5}V${cy + ew - 3}`,
  ];

  const ladder = zone * 0.5;
  const crosswalks = [
    `M${cx - ns + 4} ${cy - ew - zone * 0.46}H${cx + ns - 4}`,
    `M${cx - ns + 4} ${cy + ew + zone * 0.46}H${cx + ns - 4}`,
    `M${cx - ns - zone * 0.46} ${cy - ew + 4}V${cy + ew - 4}`,
    `M${cx + ns + zone * 0.46} ${cy - ew + 4}V${cy + ew - 4}`,
  ];

  const draw = (delay: number) =>
    animate
      ? {
          initial: { pathLength: 0 },
          animate: { pathLength: 1 },
          transition: { duration: DUR.draw, ease: EASE, delay },
        }
      : {};
  const appear = (delay: number) =>
    animate
      ? {
          initial: { opacity: 0 },
          animate: { opacity: 1 },
          transition: { duration: DUR.slow, delay },
        }
      : {};

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      fill="none"
      aria-hidden="true"
      className={`pointer-events-none ${className}`}
    >
      <motion.g stroke="var(--paint-4)" strokeWidth={ladder} strokeDasharray="5 8" {...appear(0.55)}>
        {crosswalks.map((d) => (
          <path key={d} d={d} />
        ))}
      </motion.g>
      <motion.g stroke="var(--paint-4)" strokeWidth="1.5" strokeDasharray="16 26" {...appear(0.7)}>
        {laneDashes.map((d) => (
          <path key={d} d={d} />
        ))}
      </motion.g>
      <g stroke="var(--paint-4)" strokeWidth="1.25">
        {kerbs.map((d, index) => (
          <motion.path key={d} d={d} {...draw(index * 0.05)} />
        ))}
      </g>
      <g stroke="var(--amber)" strokeWidth="1.25">
        {centrelines.map((d, index) => (
          <motion.path key={d} d={d} {...draw(0.15 + index * 0.025)} />
        ))}
      </g>
      <motion.g stroke="var(--paint-2)" strokeWidth="3.5" {...appear(0.85)}>
        {stopBars.map((d) => (
          <path key={d} d={d} />
        ))}
      </motion.g>
      {labels ? (
        <motion.g
          fill="var(--paint-3)"
          fontFamily="var(--font-mono)"
          fontSize="11"
          letterSpacing="0.02em"
          {...appear(1)}
        >
          {labels.ew ? (
            <text x="32" y={cy - ew * 0.25 + 4}>
              {labels.ew}
            </text>
          ) : null}
          {labels.ns ? (
            <text
              x={cx + ns / 2 - 4}
              y={24}
              textAnchor="start"
              transform={`rotate(90 ${cx + ns / 2 - 4} 24)`}
            >
              {labels.ns}
            </text>
          ) : null}
        </motion.g>
      ) : null}
    </svg>
  );
}

/** A plan that fills its box, junction centred unless told otherwise. */
export function PlanCanvas({
  halfNs,
  halfEw,
  xRatio = 0.5,
  yRatio = 0.5,
  drawIn = true,
  className = "",
}: {
  halfNs: number;
  halfEw: number;
  xRatio?: number;
  yRatio?: number;
  drawIn?: boolean;
  className?: string;
}) {
  const [ref, size] = useElementSize<HTMLDivElement>();
  return (
    <div ref={ref} className={`relative overflow-hidden ${className}`}>
      <PlanDrawing
        className="absolute inset-0"
        drawIn={drawIn}
        geometry={{
          width: size.width,
          height: size.height,
          cx: size.width * xRatio,
          cy: size.height * yRatio,
          halfNs,
          halfEw,
        }}
      />
    </div>
  );
}
