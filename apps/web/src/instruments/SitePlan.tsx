import { motion, useReducedMotion } from "motion/react";
import { useMemo, type ReactNode } from "react";
import { DUR, EASE, PlanDrawing, useElementSize } from "../design";
import {
  ARM_ORDER,
  fitAcross,
  inbound,
  onArm,
  projector,
  sitePlan,
  type ArmPlan,
  type LaneStrip,
  type PlanFrame,
  type Project,
  type SitePlanArms,
} from "../domain/plan";
import type { Point2, SiteGeometry } from "../twin/site";

/** Below this many pixels per metre a turn arrow is smaller than the lane line beside it. */
const ARROW_MIN_SCALE = 3.4;
const CENTRELINE_GAP_PX = 2.5;
const SCHEMATIC_HALF_NS_PX = 62;
const SCHEMATIC_HALF_EW_PX = 50;

const px = (value: number) => Math.round(value * 10) / 10;

function pathThrough(project: Project, points: readonly Point2[]): string {
  return points
    .map(([u, v], index) => {
      const [x, y] = project(u, v);
      return `${index === 0 ? "M" : "L"}${px(x)} ${px(y)}`;
    })
    .join("");
}

/** One kerb: out along one arm, round the corner, out along the next. */
function kerb(project: Project, a: ArmPlan, aEdge: number, b: ArmPlan, bEdge: number, control: Point2): string {
  const [cx, cy] = project(...control);
  const [ex, ey] = project(...onArm(b.arm, bEdge, b.near));
  const start = pathThrough(project, [onArm(a.arm, aEdge, a.far), onArm(a.arm, aEdge, a.near)]);
  const [fx, fy] = project(...onArm(b.arm, bEdge, b.far));
  return `${start}Q${px(cx)} ${px(cy)} ${px(ex)} ${px(ey)}L${px(fx)} ${px(fy)}`;
}

function kerbs(project: Project, arms: SitePlanArms): string[] {
  const { N, E, S, W } = arms;
  const paths: string[] = [];
  if (N && E) paths.push(kerb(project, N, N.hi, E, E.hi, [N.hi, E.hi]));
  if (E && S) paths.push(kerb(project, E, E.lo, S, S.hi, [S.hi, E.lo]));
  if (S && W) paths.push(kerb(project, S, S.lo, W, W.lo, [S.lo, W.lo]));
  if (W && N) paths.push(kerb(project, W, W.hi, N, N.lo, [N.lo, W.hi]));
  return paths;
}

/** Lines between neighbouring lanes running the same way. */
function dividers(project: Project, plan: ArmPlan): string[] {
  const paths: string[] = [];
  for (const group of [plan.approach, plan.exit]) {
    group.slice(1).forEach((lane, index) => {
      const before = group[index] as LaneStrip;
      const near = Math.max(lane.near, before.near);
      const far = Math.min(lane.far, before.far);
      paths.push(pathThrough(project, [onArm(plan.arm, lane.lo, near), onArm(plan.arm, lane.lo, far)]));
    });
  }
  return paths;
}

/** A painted arrow for each turn a lane carries, just behind the stop line. */
function arrows(project: Project, plan: ArmPlan): string[] {
  const [fu, fv] = inbound(plan.arm);
  const left: Point2 = [-fv, fu];
  const paths: string[] = [];
  for (const lane of plan.approach) {
    const cross = (lane.lo + lane.hi) / 2;
    const base = onArm(plan.arm, cross, lane.near + 10);
    const bend = onArm(plan.arm, cross, lane.near + 5.5);
    for (const turn of lane.lane.allowed_turns) {
      const side = turn === "left" ? 1 : turn === "right" ? -1 : 0;
      const tip: Point2 =
        side === 0
          ? onArm(plan.arm, cross, lane.near + 3)
          : [bend[0] + fu * 1.6 + left[0] * side * 1.6, bend[1] + fv * 1.6 + left[1] * side * 1.6];
      const heading: Point2 = side === 0 ? [fu, fv] : [left[0] * side, left[1] * side];
      const across: Point2 = [-heading[1], heading[0]];
      const wing = (sign: number): Point2 => [
        tip[0] - heading[0] * 1.3 + across[0] * sign * 0.9,
        tip[1] - heading[1] * 1.3 + across[1] * sign * 0.9,
      ];
      paths.push(pathThrough(project, side === 0 ? [base, tip] : [base, bend, tip]));
      paths.push(pathThrough(project, [wing(1), tip, wing(-1)]));
    }
  }
  return paths;
}

export interface SitePlanProps {
  site: SiteGeometry;
  frame: PlanFrame;
  /** Carry the road past the tracked lanes to the edge of the frame, as a continuation. */
  continuation?: boolean;
  drawIn?: boolean;
  /** Anything drawn in the plan's own coordinates. */
  overlay?: ((project: Project, arms: SitePlanArms) => ReactNode) | undefined;
  className?: string;
}

/**
 * The junction as the data has it: lanes, stop lines and turn arrows, all derived from trajectories.
 * It is approximate and it stops where tracking stopped, so anything showing it says so.
 */
export function SitePlan({
  site,
  frame,
  continuation = false,
  drawIn = true,
  overlay,
  className = "",
}: SitePlanProps) {
  const reduced = useReducedMotion();
  const animate = drawIn && !reduced;
  const arms = useMemo(() => sitePlan(site), [site]);
  const project = useMemo(() => projector(frame), [frame]);
  const { width: w, height: h, scale } = frame;
  if (w === 0 || h === 0) return null;

  const present = ARM_ORDER.flatMap((arm) => {
    const plan = arms[arm];
    return plan ? [plan] : [];
  });
  const reach = (w + h) / scale;
  const gap = CENTRELINE_GAP_PX / scale;

  const centre: string[] = [];
  const beyond: string[] = [];
  const edges: string[] = [];
  for (const plan of present) {
    if (plan.median !== null) {
      for (const offset of [-gap, gap]) {
        const cross = plan.median + offset;
        centre.push(pathThrough(project, [onArm(plan.arm, cross, plan.near), onArm(plan.arm, cross, plan.far)]));
        beyond.push(pathThrough(project, [onArm(plan.arm, cross, plan.far), onArm(plan.arm, cross, plan.far + reach)]));
      }
    }
    for (const edge of [plan.lo, plan.hi]) {
      edges.push(pathThrough(project, [onArm(plan.arm, edge, plan.far), onArm(plan.arm, edge, plan.far + reach)]));
    }
  }

  const stopLines = site.gates
    .filter((gate) => gate.role === "stop_line")
    .map((gate) => pathThrough(project, gate.segment_m));

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
      {continuation ? (
        <motion.g {...appear(0.9)}>
          <g stroke="var(--rule-2)" strokeWidth="1" strokeDasharray="3 7">
            {edges.map((d) => (
              <path key={d} d={d} />
            ))}
          </g>
          <g stroke="var(--amber)" strokeWidth="1.25" opacity="0.5">
            {beyond.map((d) => (
              <path key={d} d={d} />
            ))}
          </g>
        </motion.g>
      ) : null}
      <motion.g stroke="var(--paint-4)" strokeWidth="1" strokeDasharray="10 12" {...appear(0.6)}>
        {present.flatMap((plan) => dividers(project, plan)).map((d) => (
          <path key={d} d={d} />
        ))}
      </motion.g>
      {scale >= ARROW_MIN_SCALE ? (
        <motion.g stroke="var(--paint-3)" strokeWidth="1.25" {...appear(0.8)}>
          {present.flatMap((plan) => arrows(project, plan)).map((d) => (
            <path key={d} d={d} />
          ))}
        </motion.g>
      ) : null}
      <g stroke="var(--paint-3)" strokeWidth="1.25">
        {kerbs(project, arms).map((d, index) => (
          <motion.path key={d} d={d} {...draw(index * 0.05)} />
        ))}
      </g>
      <g stroke="var(--amber)" strokeWidth="1.25">
        {centre.map((d, index) => (
          <motion.path key={d} d={d} {...draw(0.15 + index * 0.025)} />
        ))}
      </g>
      <motion.g stroke="var(--paint-1)" strokeWidth="3" {...appear(0.85)}>
        {stopLines.map((d) => (
          <path key={d} d={d} />
        ))}
      </motion.g>
      {overlay ? overlay(project, arms) : null}
    </svg>
  );
}

/**
 * A plan that fills its box. With no geometry yet it falls back to the schematic, so the page keeps
 * its shape; `metres` is how much of the junction the shorter side of the box spans.
 */
export function SitePlanCanvas({
  site,
  metres,
  xRatio = 0.5,
  yRatio = 0.5,
  overlay,
  className = "",
}: {
  site: SiteGeometry | null;
  metres: number;
  xRatio?: number;
  yRatio?: number;
  overlay?: SitePlanProps["overlay"];
  className?: string;
}) {
  const [ref, size] = useElementSize<HTMLDivElement>();
  return (
    <div ref={ref} className={`relative overflow-hidden ${className}`}>
      {site ? (
        <SitePlan
          site={site}
          frame={fitAcross(size.width, size.height, metres, xRatio, yRatio)}
          continuation
          overlay={overlay}
          className="absolute inset-0"
        />
      ) : (
        <PlanDrawing
          className="absolute inset-0"
          geometry={{
            width: size.width,
            height: size.height,
            cx: size.width * xRatio,
            cy: size.height * yRatio,
            halfNs: SCHEMATIC_HALF_NS_PX,
            halfEw: SCHEMATIC_HALF_EW_PX,
          }}
        />
      )}
    </div>
  );
}
