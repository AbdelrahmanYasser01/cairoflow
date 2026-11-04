/**
 * Plan geometry from the site's lanes and counting lines. Everything is read off the data, in the
 * junction frame: u east and v north, in metres, with the origin at the junction centre.
 */
import type { Arm, Point2, SiteGate, SiteGeometry, SiteLane } from "../twin/site";

export const ARM_ORDER: readonly Arm[] = ["N", "E", "S", "W"];

/** A lane as a strip: its span across the arm, and how far it runs along it. */
export interface LaneStrip {
  lane: SiteLane;
  /** Across the arm: u on the north and south arms, v on the east and west arms. */
  lo: number;
  hi: number;
  /** Along the arm, as distance from the centre. */
  near: number;
  far: number;
}

export interface ArmPlan {
  arm: Arm;
  /** Outer edges of the arm, in the same across-the-arm coordinate. */
  lo: number;
  hi: number;
  /** Where the lanes begin and end, as distance from the centre. */
  near: number;
  far: number;
  /** The line between approach and exit traffic. Null when only one side is known. */
  median: number | null;
  approach: LaneStrip[];
  exit: LaneStrip[];
}

const northSouth = (arm: Arm) => arm === "N" || arm === "S";

function across(arm: Arm, [u, v]: Point2): number {
  return northSouth(arm) ? u : v;
}

function along(arm: Arm, [u, v]: Point2): number {
  return Math.abs(northSouth(arm) ? v : u);
}

function strip(lane: SiteLane): LaneStrip {
  const crosses = lane.polygon_m.map((point) => across(lane.arm, point));
  const alongs = lane.polygon_m.map((point) => along(lane.arm, point));
  return {
    lane,
    lo: Math.min(...crosses),
    hi: Math.max(...crosses),
    near: Math.min(...alongs),
    far: Math.max(...alongs),
  };
}

function spanOf(items: readonly { lo: number; hi: number }[]): { lo: number; hi: number } | null {
  if (items.length === 0) return null;
  return { lo: Math.min(...items.map((item) => item.lo)), hi: Math.max(...items.map((item) => item.hi)) };
}

function gateSpan(arm: Arm, gate: SiteGate | undefined): { lo: number; hi: number } | null {
  if (!gate) return null;
  const [a, b] = gate.segment_m;
  return { lo: Math.min(across(arm, a), across(arm, b)), hi: Math.max(across(arm, a), across(arm, b)) };
}

/** The line between two sides of an arm, when the sides do not overlap. */
function medianBetween(a: { lo: number; hi: number } | null, b: { lo: number; hi: number } | null): number | null {
  if (!a || !b) return null;
  if (a.hi <= b.lo) return (a.hi + b.lo) / 2;
  if (b.hi <= a.lo) return (b.hi + a.lo) / 2;
  return null;
}

export function armPlan(site: SiteGeometry, arm: Arm): ArmPlan | null {
  const strips = site.lanes.filter((lane) => lane.arm === arm).map(strip);
  if (strips.length === 0) return null;
  const byCross = (a: LaneStrip, b: LaneStrip) => a.lo - b.lo;
  const approach = strips.filter((item) => item.lane.direction === "approach").sort(byCross);
  const exit = strips.filter((item) => item.lane.direction === "exit").sort(byCross);
  const gates = site.gates.filter((gate) => gate.arm === arm);
  const stop = gateSpan(arm, gates.find((gate) => gate.role === "stop_line"));
  const leaving = gateSpan(arm, gates.find((gate) => gate.role === "exit_line"));
  const edges = [...strips, ...(stop ? [stop] : []), ...(leaving ? [leaving] : [])];
  const span = spanOf(edges) as { lo: number; hi: number };
  return {
    arm,
    lo: span.lo,
    hi: span.hi,
    near: Math.min(...strips.map((item) => item.near)),
    far: Math.max(...strips.map((item) => item.far)),
    median: medianBetween(spanOf(approach), spanOf(exit) ?? leaving),
    approach,
    exit,
  };
}

export type SitePlanArms = Partial<Record<Arm, ArmPlan>>;

export function sitePlan(site: SiteGeometry): SitePlanArms {
  const arms: SitePlanArms = {};
  for (const arm of ARM_ORDER) {
    const plan = armPlan(site, arm);
    if (plan) arms[arm] = plan;
  }
  return arms;
}

/** Puts metres on screen: x = cx + u * scale, y = cy - v * scale. */
export interface PlanFrame {
  width: number;
  height: number;
  cx: number;
  cy: number;
  /** Pixels per metre. */
  scale: number;
}

export interface Band {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Fits the junction so both roads fill a band of the screen, which is how the Command grid is cut. */
export function fitToBand(arms: SitePlanArms, band: Band, width: number, height: number): PlanFrame {
  const ns = spanOf([arms.N, arms.S].filter((arm): arm is ArmPlan => Boolean(arm)));
  const ew = spanOf([arms.E, arms.W].filter((arm): arm is ArmPlan => Boolean(arm)));
  const spanU = ns ?? { lo: -1, hi: 1 };
  const spanV = ew ?? { lo: -1, hi: 1 };
  const scale = Math.min(
    (band.right - band.left) / (spanU.hi - spanU.lo),
    (band.bottom - band.top) / (spanV.hi - spanV.lo),
  );
  return {
    width,
    height,
    scale,
    cx: (band.left + band.right) / 2 - (scale * (spanU.lo + spanU.hi)) / 2,
    cy: (band.top + band.bottom) / 2 + (scale * (spanV.lo + spanV.hi)) / 2,
  };
}

/** Fits a square window of the given width in metres, centred on the junction. */
export function fitAcross(width: number, height: number, metres: number, xRatio = 0.5, yRatio = 0.5): PlanFrame {
  return {
    width,
    height,
    scale: Math.min(width, height) / metres,
    cx: width * xRatio,
    cy: height * yRatio,
  };
}

export type Project = (u: number, v: number) => readonly [number, number];

export function projector(frame: PlanFrame): Project {
  return (u, v) => [frame.cx + u * frame.scale, frame.cy - v * frame.scale];
}

const INBOUND: Record<Arm, Point2> = { N: [0, -1], E: [-1, 0], S: [0, 1], W: [1, 0] };

/** The unit vector of traffic entering the junction on this arm. */
export function inbound(arm: Arm): Point2 {
  return INBOUND[arm];
}

/** A point on an arm, from its across-the-arm position and its distance from the centre. */
export function onArm(arm: Arm, cross: number, distance: number): Point2 {
  switch (arm) {
    case "N":
      return [cross, distance];
    case "S":
      return [cross, -distance];
    case "E":
      return [distance, cross];
    case "W":
      return [-distance, cross];
  }
}

/** Middle of the lanes that carry a turn, falling back to the whole approach. */
export function turnCross(plan: ArmPlan, turn: string): number {
  const lanes = plan.approach.filter((item) => item.lane.allowed_turns.includes(turn));
  const span = spanOf(lanes.length > 0 ? lanes : plan.approach);
  return span ? (span.lo + span.hi) / 2 : (plan.lo + plan.hi) / 2;
}

/** Middle of an arm's exit side: its exit lanes, or its exit line where no lanes are drawn. */
export function exitCross(site: SiteGeometry, plan: ArmPlan): number {
  const span = spanOf(plan.exit) ?? gateSpan(plan.arm, site.gates.find((gate) => gate.arm === plan.arm && gate.role === "exit_line"));
  return span ? (span.lo + span.hi) / 2 : (plan.lo + plan.hi) / 2;
}
