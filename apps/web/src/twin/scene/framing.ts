/**
 * Where the camera stands for each named view. Pure maths on the site's real extent, with no scene
 * attached, so it runs under vitest against a real projection.
 */
import { Vector3 } from "three";
import type { TwinMode, TwinView } from "../controls";
import { COLUMN_MAX_HEIGHT_M } from "../density";
import type { Bounds, TwinModel } from "../model";
import type { Point2 } from "../site";
import { toWorldZ, type Insets } from "./runtime";

export type Pose = readonly [number, number, number, number, number, number];
export type SiteExtent = Pick<TwinModel, "bounds" | "footprint" | "vehicleReach">;

export const FOV_DEG = 30;

/** The stage in pixels, and the part of it no instrument covers. */
export interface StageFrame {
  width: number;
  height: number;
  insets: Insets;
}

const DEG = Math.PI / 180;
const EMPTY_SITE: readonly Point2[] = [
  [-50, -50],
  [50, 50],
];
/** Metres kept clear around the site in a plan view. */
const PLAN_MARGIN_M = 16;
/** Sits the eye a hair off the vertical, which is what sets which way is up the screen. */
const PLAN_TILT_M = 0.05;

/** Ground direction pointing up the screen in Plan. */
function planUp(mode: TwinMode, bounds: Bounds, aspect: number, north: Point2 | null): Point2 {
  if (mode === "geo" && north) return north;
  // A long thin site across a wide stage: lie it along the stage instead of down it.
  const tall = bounds.vMax - bounds.vMin > bounds.uMax - bounds.uMin;
  return tall && aspect > 1 ? [-1, 0] : [0, 1];
}

/** Share of the free area a fitted view fills, so nothing sits hard against an instrument. */
const FIT_FILL = 0.97;
const FIT_ROUNDS = 6;
/** Along Lankershim: from behind the south arm, just off the centre line. */
const ALONG_AZIMUTH = 5 * DEG;
const ALONG_ELEVATION = 11 * DEG;
/** Tallest columns stand near the stop lines, so this much of the site's length has to clear them. */
const COLUMN_REACH = 0.6;

/** What a view must keep in frame: the road and the vehicles, with the column tops in Geo. */
function framePoints(footprint: readonly Point2[], mode: TwinMode): Vector3[] {
  const points = footprint.map(([u, v]) => new Vector3(u, 0, toWorldZ(v)));
  if (mode !== "geo") return points;
  return [
    ...points,
    ...footprint.map(([u, v]) => new Vector3(u, COLUMN_MAX_HEIGHT_M, toWorldZ(v * COLUMN_REACH))),
  ];
}

/**
 * Eye and target from which every point lands inside the free part of the stage, for an eye on this
 * azimuth and elevation. Worked from the points rather than a rule of thumb, because stages differ
 * and the near arm looms: the target walks along the ground until the image is centred in that part.
 */
function fitPose(points: readonly Vector3[], azimuth: number, elevation: number, stage: StageFrame): Pose {
  const { insets, width, height } = stage;
  const half = Math.tan((FOV_DEG / 2) * DEG) * FIT_FILL;
  const tanX = (half * Math.max(width - insets.left - insets.right, 1)) / height;
  const tanY = (half * Math.max(height - insets.top - insets.bottom, 1)) / height;
  const toEye = new Vector3(
    Math.sin(azimuth) * Math.cos(elevation),
    Math.sin(elevation),
    Math.cos(azimuth) * Math.cos(elevation),
  );
  const right = new Vector3(0, 1, 0).cross(toEye).normalize();
  const up = new Vector3().crossVectors(toEye, right);
  const ahead = new Vector3(-toEye.x, 0, -toEye.z).normalize();
  const target = new Vector3();
  for (const point of points) target.add(point);
  target.divideScalar(Math.max(points.length, 1)).setY(0);

  const offset = new Vector3();
  const distanceToFit = (): number => {
    let distance = 0;
    for (const point of points) {
      offset.subVectors(point, target);
      const across = Math.abs(offset.dot(right)) / tanX;
      const along = Math.abs(offset.dot(up)) / tanY;
      distance = Math.max(distance, offset.dot(toEye) + Math.max(across, along));
    }
    return distance;
  };

  for (let round = 0; round < FIT_ROUNDS; round++) {
    const distance = distanceToFit();
    let left = Infinity;
    let rightmost = -Infinity;
    let low = Infinity;
    let high = -Infinity;
    for (const point of points) {
      offset.subVectors(point, target);
      const range = distance - offset.dot(toEye);
      const x = offset.dot(right) / range;
      const y = offset.dot(up) / range;
      left = Math.min(left, x);
      rightmost = Math.max(rightmost, x);
      low = Math.min(low, y);
      high = Math.max(high, y);
    }
    target.addScaledVector(right, ((left + rightmost) / 2) * distance);
    target.addScaledVector(ahead, (((low + high) / 2) * distance) / Math.sin(elevation));
  }

  const distance = distanceToFit();
  return [
    target.x + toEye.x * distance,
    toEye.y * distance,
    target.z + toEye.z * distance,
    target.x,
    0,
    target.z,
  ];
}

/**
 * Eye then target, in scene coordinates. Distances follow the site's real extent and the free part
 * of the stage: the view offset in ViewFrame centres on that part, and these fill it.
 */
export function poseFor(
  view: Exclude<TwinView, "follow">,
  mode: TwinMode,
  site: SiteExtent,
  stage: StageFrame,
  north: Point2 | null,
): Pose {
  const { bounds } = site;
  // A site with no lanes and no positions still has a place to look at.
  const footprint = site.footprint.length > 0 ? site.footprint : EMPTY_SITE;
  if (view === "along") {
    const reach = site.vehicleReach.length > 0 ? site.vehicleReach : footprint;
    return fitPose(framePoints(reach, "twin"), ALONG_AZIMUTH, ALONG_ELEVATION, stage);
  }
  if (view === "oblique") {
    // Across the road rather than down it, so the site runs corner to corner of a wide stage.
    const azimuth = (mode === "geo" ? 44 : 56) * DEG;
    const elevation = (mode === "geo" ? 34 : 29) * DEG;
    return fitPose(framePoints(footprint, mode), azimuth, elevation, stage);
  }

  const halfV = (bounds.vMax - bounds.vMin) / 2;
  const halfU = (bounds.uMax - bounds.uMin) / 2;
  const centreU = (bounds.uMin + bounds.uMax) / 2;
  const centreV = (bounds.vMin + bounds.vMax) / 2;
  const { insets } = stage;
  const freeWidth = Math.max(stage.width - insets.left - insets.right, 1);
  const freeHeight = Math.max(stage.height - insets.top - insets.bottom, 1);
  const aspect = freeWidth / freeHeight;
  const up = planUp(mode, bounds, aspect, north);
  const halfUp = Math.abs(up[0]) * halfU + Math.abs(up[1]) * halfV + PLAN_MARGIN_M;
  const halfAcross = Math.abs(up[1]) * halfU + Math.abs(up[0]) * halfV + PLAN_MARGIN_M;
  const fit = Math.max(halfUp, halfAcross / Math.max(aspect, 0.4));
  const height = (fit / Math.tan((FOV_DEG / 2) * DEG)) * (stage.height / freeHeight);
  return [
    centreU - up[0] * PLAN_TILT_M,
    height,
    toWorldZ(centreV - up[1] * PLAN_TILT_M),
    centreU,
    0,
    toWorldZ(centreV),
  ];
}
