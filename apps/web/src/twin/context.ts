/**
 * Context blocks: low, dark massing on the four corner blocks, for depth only. Nothing here is
 * surveyed and nothing is measured from it. Pure maths, no three.js. A block is kept only when its
 * whole footprint, with a setback, is clear of the carriageway, the sidewalks and every ground
 * cell a vehicle crossed, so it can never stand on the road or over a vehicle.
 */
import type { Coverage } from "./model";
import { SIDEWALK_M, insidePolygon, onStreet, type RoadNetwork } from "./network";

export interface ContextBlock {
  uMin: number;
  uMax: number;
  vMin: number;
  vMax: number;
  height: number;
}

export interface Rect {
  uMin: number;
  uMax: number;
  vMin: number;
  vMax: number;
}

/** Clear ground kept between the outer edge of a sidewalk and the first block. */
export const CONTEXT_SETBACK_M = 4;
const ALLEY_M = 7;
const SAMPLE_M = 2;
/** Lot sizes and heights cycle through these, so the layout is the same on every load. */
const WIDTHS = [26, 18, 34, 22, 30];
const DEPTHS = [20, 30, 16, 26];
const HEIGHTS = [4.2, 6.4, 3.4, 5.6, 7.2, 4.8];
export const CONTEXT_MAX_HEIGHT_M = Math.max(...HEIGHTS);

/**
 * Heights stay under a plane rising at this angle from the kerb side of the nearest lane, so a
 * camera ray that reaches a vehicle from higher than this passes over every block. The lowest
 * camera, Along Lankershim, looks down at 11 degrees.
 */
const CLEAR_RAY_DEG = 8;
const VEHICLE_TOP_M = 1;
const LANE_INSET_M = 1.25;

type Quadrant = { su: 1 | -1; sv: 1 | -1; u0: number; v0: number; edgeU: number; edgeV: number };

function quadrants(network: RoadNetwork): Quadrant[] {
  const { N, E, S, W } = network.sections;
  // One metre more than the sampled setback, so the nearest lots clear the sidewalk test.
  const gap = SIDEWALK_M + CONTEXT_SETBACK_M + 1;
  return [
    { su: 1, sv: 1, u0: N.hi + gap, v0: E.hi + gap, edgeU: N.hi, edgeV: E.hi },
    { su: 1, sv: -1, u0: S.hi + gap, v0: E.lo - gap, edgeU: S.hi, edgeV: E.lo },
    { su: -1, sv: -1, u0: S.lo - gap, v0: W.lo - gap, edgeU: S.lo, edgeV: W.lo },
    { su: -1, sv: 1, u0: N.lo - gap, v0: W.hi + gap, edgeU: N.lo, edgeV: W.hi },
  ];
}

function covered(coverage: Coverage | null, u: number, v: number): boolean {
  if (!coverage) return false;
  const c = Math.floor((u - coverage.uMin) / coverage.cellM);
  const r = Math.floor((v - coverage.vMin) / coverage.cellM);
  if (c < 0 || r < 0 || c >= coverage.cols || r >= coverage.rows) return false;
  return (coverage.data[r * coverage.cols + c] ?? 0) > 0;
}

function overlaps(a: Rect, b: Rect): boolean {
  return a.uMin < b.uMax && a.uMax > b.uMin && a.vMin < b.vMax && a.vMax > b.vMin;
}

/** True when every sample of the footprint, grown by the setback, is off the street and unvisited. */
export function blockClear(network: RoadNetwork, coverage: Coverage | null, rect: Rect): boolean {
  const pad = CONTEXT_SETBACK_M;
  for (let u = rect.uMin - pad; u <= rect.uMax + pad + 1e-6; u += SAMPLE_M) {
    for (let v = rect.vMin - pad; v <= rect.vMax + pad + 1e-6; v += SAMPLE_M) {
      if (onStreet(network, u, v, SIDEWALK_M)) return false;
      if (covered(coverage, u, v)) return false;
      // Side entrances are in the outline but not in the arms' bands.
      if (insidePolygon(network.outline, u, v)) return false;
    }
  }
  return true;
}

/**
 * Lots laid out from each inner corner outwards, inside `limit` (a test of a ground point, true
 * where the floor still shows) and clear of `avoid` (ground marks that must stay visible).
 */
export function contextBlocks(
  network: RoadNetwork,
  coverage: Coverage | null,
  limit: (u: number, v: number) => boolean,
  avoid: readonly Rect[] = [],
): ContextBlock[] {
  const blocks: ContextBlock[] = [];
  let seed = 0;
  const slope = Math.tan((CLEAR_RAY_DEG * Math.PI) / 180);
  for (const { su, sv, u0, v0, edgeU, edgeV } of quadrants(network)) {
    let along = 0;
    for (let row = 0; row < 8; row++) {
      const depth = DEPTHS[(row + seed) % DEPTHS.length] as number;
      let across = 0;
      for (let col = 0; col < 8; col++) {
        const width = WIDTHS[(col + row * 2 + seed) % WIDTHS.length] as number;
        const ua = u0 + su * across;
        const ub = u0 + su * (across + width);
        const va = v0 + sv * along;
        const vb = v0 + sv * (along + depth);
        const rect: Rect = {
          uMin: Math.min(ua, ub),
          uMax: Math.max(ua, ub),
          vMin: Math.min(va, vb),
          vMax: Math.max(va, vb),
        };
        across += width + ALLEY_M;
        const corners: [number, number][] = [
          [rect.uMin, rect.vMin],
          [rect.uMin, rect.vMax],
          [rect.uMax, rect.vMin],
          [rect.uMax, rect.vMax],
        ];
        if (!corners.every(([u, v]) => limit(u, v))) continue;
        if (avoid.some((mark) => overlaps(mark, rect))) continue;
        if (!blockClear(network, coverage, rect)) continue;
        const nearU = su > 0 ? rect.uMin : rect.uMax;
        const nearV = sv > 0 ? rect.vMin : rect.vMax;
        const fromRoad = Math.min(Math.abs(nearU - edgeU), Math.abs(nearV - edgeV)) + LANE_INSET_M;
        const ceiling = VEHICLE_TOP_M + fromRoad * slope;
        const height = Math.min(HEIGHTS[(row * 3 + col + seed) % HEIGHTS.length] as number, ceiling);
        blocks.push({ ...rect, height });
      }
      along += depth + ALLEY_M;
    }
    seed += 1;
  }
  return blocks;
}
