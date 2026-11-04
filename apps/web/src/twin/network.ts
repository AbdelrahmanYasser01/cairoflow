/**
 * One continuous road network for the scene, derived from the site's lanes and gates and widened
 * to the analysis's own track envelope, so no drawn vehicle leaves the asphalt. Pure maths, no
 * three.js. Everything here is approximate and shown as such: lanes come from the site file, the
 * kerb line from where vehicles were seen, and the arms run on past the data to the scene edge.
 */
import { NO_GROUND, type Trajectories } from "../lib/analysis";
import type { Arm, Point2, SiteGeometry } from "./site";

export const ARMS: readonly Arm[] = ["N", "E", "S", "W"];

/** Along the arm, away from the junction, and across it, in u-v terms. */
const AXIS: Record<Arm, { along: Point2; across: Point2 }> = {
  N: { along: [0, 1], across: [1, 0] },
  S: { along: [0, -1], across: [1, 0] },
  E: { along: [1, 0], across: [0, 1] },
  W: { along: [-1, 0], across: [0, 1] },
};

/** Half a wide vehicle body: a centre position this close to the kerb still fits on the road. */
const BODY_MARGIN_M = 1.25;
/** The envelope may widen an arm by at most this much past its lanes, so one stray fix can't. */
const MAX_WIDEN_M = 6;
/** Points this far past the stop line are on the arm, not in the junction box. */
const ARM_CLEAR_M = 3;
/** Points this far either side of the lanes still count as this arm's traffic. */
const ARM_BAND_M = 8;
/** Kerb return radius at each corner of the junction box. */
const CORNER_RADIUS_M = 7;
const CORNER_STEPS = 10;
/** Arms run this far from the centre; the floor fades out well before. */
export const ARM_REACH_M = 420;
export const SIDEWALK_M = 4.5;

export interface ArmSection {
  arm: Arm;
  /** Across range of the carriageway, in the arm's across coordinate (u for N/S, v for E/W). */
  lo: number;
  hi: number;
  /** Distance from the centre to the stop line along the arm. */
  mouth: number;
  /** The same range from lanes and gates alone, before the envelope widened it. */
  laneLo: number;
  laneHi: number;
}

export interface RoadNetwork {
  sections: Record<Arm, ArmSection>;
  /** The carriageway outline, clockwise in u-v (N, E, S, W). */
  outline: readonly Point2[];
  /** One kerb line per corner, each running from one arm's far end round to the next's. Road on the right. */
  kerbs: readonly (readonly Point2[])[];
  /** Ground positions of this analysis that still fall outside the outline. */
  offRoad: number;
}

function dot(p: Point2, q: Point2): number {
  return p[0] * q[0] + p[1] * q[1];
}

function sectionsFrom(site: SiteGeometry): Record<Arm, ArmSection> {
  const out = {} as Record<Arm, ArmSection>;
  for (const arm of ARMS) {
    const { along, across } = AXIS[arm];
    let lo = Infinity;
    let hi = -Infinity;
    let mouth = Infinity;
    for (const lane of site.lanes) {
      if (lane.arm !== arm) continue;
      for (const point of lane.polygon_m) {
        lo = Math.min(lo, dot(point, across));
        hi = Math.max(hi, dot(point, across));
        mouth = Math.min(mouth, dot(point, along));
      }
    }
    for (const gate of site.gates) {
      if (gate.arm !== arm) continue;
      for (const point of gate.segment_m) {
        lo = Math.min(lo, dot(point, across));
        hi = Math.max(hi, dot(point, across));
        if (gate.role === "stop_line") mouth = Math.min(mouth, dot(point, along));
      }
    }
    if (!Number.isFinite(lo)) {
      // An arm the site doesn't describe: a plain two-lane road on the axis.
      lo = -7;
      hi = 7;
      mouth = 18;
    }
    out[arm] = { arm, lo, hi, mouth, laneLo: lo, laneHi: hi };
  }
  return out;
}

/** Widens each arm to the positions seen on it, within a cap. */
function widen(sections: Record<Arm, ArmSection>, trajectories: Trajectories | null, scale: number): void {
  if (!trajectories) return;
  const { gx, gy } = trajectories;
  const seenLo: Record<Arm, number> = { N: Infinity, E: Infinity, S: Infinity, W: Infinity };
  const seenHi: Record<Arm, number> = { N: -Infinity, E: -Infinity, S: -Infinity, W: -Infinity };
  for (let i = 0; i < gx.length; i++) {
    if (gx[i] === NO_GROUND) continue;
    const point: Point2 = [gx[i] * scale, gy[i] * scale];
    for (const arm of ARMS) {
      const section = sections[arm];
      const { along, across } = AXIS[arm];
      if (dot(point, along) < section.mouth + ARM_CLEAR_M) continue;
      const a = dot(point, across);
      if (a < section.laneLo - ARM_BAND_M || a > section.laneHi + ARM_BAND_M) continue;
      if (a < seenLo[arm]) seenLo[arm] = a;
      if (a > seenHi[arm]) seenHi[arm] = a;
    }
  }
  for (const arm of ARMS) {
    const section = sections[arm];
    if (Number.isFinite(seenLo[arm])) {
      section.lo = Math.max(Math.min(section.lo, seenLo[arm] - BODY_MARGIN_M), section.laneLo - MAX_WIDEN_M);
    }
    if (Number.isFinite(seenHi[arm])) {
      section.hi = Math.min(Math.max(section.hi, seenHi[arm] + BODY_MARGIN_M), section.laneHi + MAX_WIDEN_M);
    }
  }
}

/**
 * A quarter-circle kerb return between two straight kerbs meeting at `corner`. `from` and `to` are
 * the unit directions the kerb runs in before and after the corner.
 */
function fillet(corner: Point2, from: Point2, to: Point2, radius: number): Point2[] {
  const start: Point2 = [corner[0] - from[0] * radius, corner[1] - from[1] * radius];
  const centre: Point2 = [start[0] + to[0] * radius, start[1] + to[1] * radius];
  const points: Point2[] = [];
  for (let step = 0; step <= CORNER_STEPS; step++) {
    const t = (step / CORNER_STEPS) * (Math.PI / 2);
    // From pointing back along -to, round to pointing back along -from.
    const c = Math.cos(t);
    const s = Math.sin(t);
    points.push([
      centre[0] - to[0] * radius * c + from[0] * radius * s,
      centre[1] - to[1] * radius * c + from[1] * radius * s,
    ]);
  }
  return points;
}

export function kerbLines(sections: Record<Arm, ArmSection>, reach = ARM_REACH_M): Point2[][] {
  const { N, E, S, W } = sections;
  const r = CORNER_RADIUS_M;
  return [
    // North arm's east kerb round to the east arm's north kerb.
    [[N.hi, reach], ...fillet([N.hi, E.hi], [0, -1], [1, 0], r), [reach, E.hi]],
    [[reach, E.lo], ...fillet([S.hi, E.lo], [-1, 0], [0, -1], r), [S.hi, -reach]],
    [[S.lo, -reach], ...fillet([S.lo, W.lo], [0, 1], [-1, 0], r), [-reach, W.lo]],
    [[-reach, W.hi], ...fillet([N.lo, W.hi], [1, 0], [0, 1], r), [N.lo, reach]],
  ];
}

/** Even-odd ray test. */
export function insidePolygon(polygon: readonly Point2[], u: number, v: number): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [ui, vi] = polygon[i];
    const [uj, vj] = polygon[j];
    if (vi > v !== vj > v && u < ((uj - ui) * (v - vi)) / (vj - vi) + ui) inside = !inside;
  }
  return inside;
}

export function buildNetwork(site: SiteGeometry, trajectories: Trajectories | null, scale: number): RoadNetwork {
  const sections = sectionsFrom(site);
  widen(sections, trajectories, scale);
  const kerbs = kerbLines(sections);
  const outline = kerbs.flat();
  let offRoad = 0;
  if (trajectories) {
    const { gx, gy } = trajectories;
    for (let i = 0; i < gx.length; i++) {
      if (gx[i] === NO_GROUND) continue;
      if (!insidePolygon(outline, gx[i] * scale, gy[i] * scale)) offRoad++;
    }
  }
  return { sections, outline, kerbs, offRoad };
}

/** A lane polygon with its far end pushed out to the end of the arm, for its paint. */
export function extendLane(polygon: readonly Point2[], arm: Arm, reach = ARM_REACH_M): Point2[] {
  const { along } = AXIS[arm];
  const ends = polygon.map((point) => dot(point, along));
  const far = Math.max(...ends);
  return polygon.map((point, i) => {
    if (far - ends[i] > 0.5) return point;
    const push = reach - ends[i];
    return [point[0] + along[0] * push, point[1] + along[1] * push] as Point2;
  });
}

export function armAxis(arm: Arm): { along: Point2; across: Point2 } {
  return AXIS[arm];
}
