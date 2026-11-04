/**
 * Everything the scene derives once from a loaded analysis, and the per-frame sampler. No three.js
 * here, so all of it runs under vitest. Nothing below adds a position that isn't in the file.
 */
import {
  NO_GROUND,
  NO_SPEED,
  POINT_OUTSIDE_VALID_REGION,
  type AnalysisRecord,
  type LoadedAnalysis,
  type TrackEntry,
  type Trajectories,
} from "../lib/analysis";
import type { Point2, SiteGeometry } from "./site";

export const FORMS = ["car", "truck", "bus", "motorcycle", "plain"] as const;
export type Form = (typeof FORMS)[number];

const FORM_OF: Record<TrackEntry["vehicle_class"], number> = {
  car: 0,
  truck: 1,
  bus: 2,
  motorcycle: 3,
  vehicle: 4,
  unknown: 4,
};

/** Neither height nor body shape is measured. These are drawing sizes, shown as assumed. */
export const FORM_HEIGHT_M: readonly number[] = [1.5, 3.6, 3.2, 1.5, 1.6];

/** Drawn when a track has no measured size, so it can be seen. Never shown as a value. */
const NOMINAL_LENGTH_M: readonly number[] = [4.5, 10, 12, 2.2, 4.5];
const NOMINAL_WIDTH_M: readonly number[] = [1.9, 2.5, 2.6, 0.8, 1.9];

const TURN = Math.PI * 2;
const HEADING_STEPS = 256;

/** Travel each side of a row that its display heading is measured across. */
const HEADING_CHORD_M = 2.5;
/** A chord shorter than this says more about position noise than about direction. */
const HEADING_MIN_CHORD_M = 1;
/** Movement under this isn't counted as travel, so jitter at a stop can't build up a chord. */
const TRAVEL_DEADBAND_M = 0.25;
/** Below this the published heading is noise. Only used for a track that never travels. */
const HEADING_MIN_SPEED_CM_S = 30;

/** Seconds a vehicle takes to appear at the start of its track, and to leave at the end. */
const APPEAR_S = 0.4;
const APPEAR_FROM = 0.3;

/** A step this long between two 10 Hz rows is 25 m/s: a tracking jump, not a vehicle moving. */
const POSITION_JUMP_M = 2.5;

/** Which point of the vehicle a track's ground position names. */
export type PositionAnchor = "front_centre" | "centre";

export interface Bounds {
  uMin: number;
  uMax: number;
  vMin: number;
  vMax: number;
}

/** Ground cells any vehicle crossed in this analysis: the only evidence of the junction floor. */
export interface Coverage {
  data: Uint8Array;
  cols: number;
  rows: number;
  cellM: number;
  uMin: number;
  vMin: number;
}

export interface TwinModel extends LoadedAnalysis {
  id: string;
  site: SiteGeometry;
  fps: number;
  frameCount: number;
  durationS: number;
  groundScale: number;
  /** Radians clockwise from +v, from the path itself, for drawing only. The inspector reads the raw column. */
  displayHeading: Float32Array;
  positionAnchor: PositionAnchor;
  /** Per track: metres from the ground position back to the middle of the body. */
  bodyOffsetM: Float32Array;
  form: Uint8Array;
  lengthM: Float32Array;
  widthM: Float32Array;
  indexOfTrack: ReadonlyMap<number, number>;
  /** Most tracks alive on one frame: the instance buffers are sized from this. */
  peak: number;
  groundRows: number;
  /** Tracks with not one ground row. Their vehicles can't be placed. */
  unplacedTracks: number;
  /** Tracks drawn at a nominal size because the file has no length or width for them. */
  nominalSizeTracks: number;
  /** Row-to-row steps too long to be motion, and the tracks holding them. Drawn as published. */
  positionJumps: number;
  jumpTracks: number;
  placeable: boolean;
  bounds: Bounds;
  /** Road corners and the furthest vehicles: what a wide view keeps in frame. Tighter than bounds. */
  footprint: readonly Point2[];
  /** The furthest vehicles alone. A camera may see only part of the site, and a close view follows that. */
  vehicleReach: readonly Point2[];
  coverage: Coverage | null;
  /** Vehicles alive per equal time bin, for the time filter's strip. */
  timeline: Uint16Array;
}

export function lerpAngle(from: number, to: number, t: number): number {
  const delta = ((((to - from + Math.PI) % TURN) + TURN) % TURN) - Math.PI;
  return from + delta * t;
}

/** The published column as one direction, for a track that never moved far enough to have a path. */
function publishedDirection(
  { heading, speedCmS }: Pick<Trajectories, "heading" | "speedCmS">,
  first: number,
  n: number,
): number {
  let x = 0;
  let y = 0;
  for (let row = first; row < first + n; row++) {
    const speed = speedCmS[row] === NO_SPEED ? 100 : speedCmS[row];
    if (speed < HEADING_MIN_SPEED_CM_S) continue;
    const angle = (heading[row] / HEADING_STEPS) * TURN;
    x += Math.sin(angle) * speed;
    y += Math.cos(angle) * speed;
  }
  return Math.hypot(x, y) > 1e-6 ? Math.atan2(x, y) : (heading[first] / HEADING_STEPS) * TURN;
}

/**
 * A heading fit to draw. The published column comes from velocity, so it turns to noise as a
 * vehicle slows and a stopped vehicle would keep whatever it last read. The path doesn't have that
 * problem: each row takes the chord between the points 2.5 m of travel behind and ahead of it, and
 * every row of a stop shares one chord, so a queue points down its lane. Near a track's ends the
 * chord runs from the end it has. A track that never travels falls back to the published column.
 */
export function displayHeadings(
  trajectories: Pick<Trajectories, "heading" | "speedCmS" | "gx" | "gy" | "header">,
  tracks: readonly Pick<TrackEntry, "first_row" | "n">[],
): Float32Array {
  const { heading, gx, gy, header } = trajectories;
  const scale = header.groundScale;
  const out = new Float32Array(heading.length);
  const longest = tracks.reduce((most, track) => Math.max(most, track.n), 0);
  const rows = new Int32Array(longest);
  const travel = new Float64Array(longest);
  for (const { first_row: first, n } of tracks) {
    let count = 0;
    let total = 0;
    let fromU = 0;
    let fromV = 0;
    for (let row = first; row < first + n; row++) {
      if (gx[row] === NO_GROUND) continue;
      const u = gx[row] * scale;
      const v = gy[row] * scale;
      const step = count === 0 ? 0 : Math.hypot(u - fromU, v - fromV);
      if (count === 0 || step >= TRAVEL_DEADBAND_M) {
        total += step;
        fromU = u;
        fromV = v;
      }
      rows[count] = row;
      travel[count] = total;
      count++;
    }

    out.fill(publishedDirection(trajectories, first, n), first, first + n);
    if (total < HEADING_MIN_CHORD_M) continue;

    let behind = 0;
    let ahead = 0;
    let held = Number.NaN;
    let firstKnown = -1;
    for (let k = 0; k < count; k++) {
      while (behind < k && travel[k] - travel[behind + 1] >= HEADING_CHORD_M) behind++;
      if (ahead < k) ahead = k;
      while (ahead < count - 1 && travel[ahead] - travel[k] < HEADING_CHORD_M) ahead++;
      const du = (gx[rows[ahead]] - gx[rows[behind]]) * scale;
      const dv = (gy[rows[ahead]] - gy[rows[behind]]) * scale;
      if (Math.hypot(du, dv) >= HEADING_MIN_CHORD_M) {
        held = Math.atan2(du, dv);
        if (firstKnown < 0) firstKnown = k;
      }
      if (!Number.isNaN(held)) out[rows[k]] = held;
    }
    if (firstKnown < 0) continue;
    for (let k = 0; k < firstKnown; k++) out[rows[k]] = out[rows[firstKnown]];
    // A row with no ground position keeps the direction of the row before it.
    let last = out[rows[0]];
    for (let row = first; row < first + n; row++) {
      if (gx[row] === NO_GROUND) out[row] = last;
      else last = out[row];
    }
  }
  return out;
}

/**
 * NGSIM's published global_x and global_y are the vehicle's front centre (see ngsim.py). Nothing
 * else is known to be. The origin has to be read as well as the source: camera-derived analyses of
 * the same footage carry an NGSIM source id, and their positions came from a detector.
 */
export function positionAnchorOf(
  record: { data_origin: AnalysisRecord["data_origin"]; source?: { source_id?: string | null } | null },
): PositionAnchor {
  const published = record.data_origin === "reference_dataset";
  return published && record.source?.source_id?.startsWith("ngsim-") ? "front_centre" : "centre";
}

function siteBounds(site: SiteGeometry): Bounds {
  const bounds: Bounds = { uMin: Infinity, uMax: -Infinity, vMin: Infinity, vMax: -Infinity };
  for (const lane of site.lanes) {
    for (const [u, v] of lane.polygon_m) {
      bounds.uMin = Math.min(bounds.uMin, u);
      bounds.uMax = Math.max(bounds.uMax, u);
      bounds.vMin = Math.min(bounds.vMin, v);
      bounds.vMax = Math.max(bounds.vMax, v);
    }
  }
  return bounds;
}

const DIAGONAL = Math.SQRT1_2;
/** The directions a site's furthest vehicles are looked for in. */
const REACH_DIRECTIONS: readonly Point2[] = [
  [1, 0],
  [-1, 0],
  [0, 1],
  [0, -1],
  [DIAGONAL, DIAGONAL],
  [DIAGONAL, -DIAGONAL],
  [-DIAGONAL, DIAGONAL],
  [-DIAGONAL, -DIAGONAL],
];

function furthestVehicles(trajectories: Trajectories, scale: number): Point2[] {
  const { gx, gy } = trajectories;
  const furthest = REACH_DIRECTIONS.map(() => ({ reach: -Infinity, u: 0, v: 0 }));
  for (let row = 0; row < gx.length; row++) {
    if (gx[row] === NO_GROUND) continue;
    const u = gx[row] * scale;
    const v = gy[row] * scale;
    REACH_DIRECTIONS.forEach(([du, dv], i) => {
      const reach = u * du + v * dv;
      if (reach > furthest[i].reach) furthest[i] = { reach, u, v };
    });
  }
  return furthest.filter(({ reach }) => reach > -Infinity).map(({ u, v }): Point2 => [u, v]);
}

const COVERAGE_CELL_M = 1;
const COVERAGE_MIN_HITS = 3;

function buildCoverage(trajectories: Trajectories, bounds: Bounds, scale: number): Coverage {
  const cellM = COVERAGE_CELL_M;
  const uMin = Math.floor(bounds.uMin) - 4;
  const vMin = Math.floor(bounds.vMin) - 4;
  const cols = Math.ceil(bounds.uMax - uMin) + 5;
  const rows = Math.ceil(bounds.vMax - vMin) + 5;
  const hits = new Uint16Array(cols * rows);
  const { gx, gy } = trajectories;
  for (let i = 0; i < gx.length; i++) {
    if (gx[i] === NO_GROUND) continue;
    const c = Math.floor((gx[i] * scale - uMin) / cellM);
    const r = Math.floor((gy[i] * scale - vMin) / cellM);
    if (c >= 0 && c < cols && r >= 0 && r < rows && hits[r * cols + c] < 65535) hits[r * cols + c]++;
  }
  // A vehicle is wider than one cell: spread each hit over its neighbours, then soften the edge.
  const solid = new Float32Array(cols * rows);
  for (let i = 0; i < solid.length; i++) solid[i] = hits[i] >= COVERAGE_MIN_HITS ? 1 : 0;
  const data = new Uint8Array(cols * rows);
  const reach = 2;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let total = 0;
      let count = 0;
      for (let dr = -reach; dr <= reach; dr++) {
        for (let dc = -reach; dc <= reach; dc++) {
          const rr = r + dr;
          const cc = c + dc;
          if (rr < 0 || rr >= rows || cc < 0 || cc >= cols) continue;
          total += solid[rr * cols + cc];
          count++;
        }
      }
      data[r * cols + c] = Math.round((total / count) * 255);
    }
  }
  return { data, cols, rows, cellM, uMin, vMin };
}

const TIMELINE_BINS = 90;

export function buildModel(id: string, loaded: LoadedAnalysis, site: SiteGeometry): TwinModel {
  const { record, tracks, trajectories } = loaded;
  const fps = record.fps;
  const frameCount = record.frame_count;
  const scale = trajectories.header.groundScale;

  const form = new Uint8Array(tracks.length);
  const lengthM = new Float32Array(tracks.length);
  const widthM = new Float32Array(tracks.length);
  const indexOfTrack = new Map<number, number>();
  const alive = new Int32Array(frameCount + 2);
  const timeline = new Uint16Array(TIMELINE_BINS);
  const binFrames = frameCount / TIMELINE_BINS;
  let unplacedTracks = 0;
  let nominalSizeTracks = 0;
  let groundRows = 0;
  let positionJumps = 0;
  let jumpTracks = 0;

  const bounds = siteBounds(site);
  tracks.forEach((track, index) => {
    const kind = FORM_OF[track.vehicle_class] ?? 4;
    form[index] = kind;
    // A camera track's size comes from a box on a near-overhead view and reads far too big where
    // vehicles merge, so its body takes the class's nominal size. Published sizes are kept.
    const published = record.data_origin === "reference_dataset";
    const measured = published && track.length_m != null && track.width_m != null;
    if (!measured) nominalSizeTracks++;
    lengthM[index] = measured ? (track.length_m as number) : NOMINAL_LENGTH_M[kind];
    widthM[index] = measured ? (track.width_m as number) : NOMINAL_WIDTH_M[kind];
    indexOfTrack.set(track.track_id, index);

    const first = Math.max(0, track.start_frame);
    const last = Math.min(frameCount, track.start_frame + track.n);
    alive[first] += 1;
    alive[last] -= 1;
    const firstBin = Math.floor(first / binFrames);
    const lastBin = Math.min(TIMELINE_BINS - 1, Math.floor((last - 1) / binFrames));
    for (let bin = firstBin; bin <= lastBin; bin++) timeline[bin]++;

    let placed = 0;
    let jumps = 0;
    for (let i = 0; i < track.n; i++) {
      const row = track.first_row + i;
      if (trajectories.gx[row] === NO_GROUND) continue;
      placed++;
      const u = trajectories.gx[row] * scale;
      const v = trajectories.gy[row] * scale;
      if (i > 0 && isJump(trajectories, row - 1, row, scale)) jumps++;
      if (u < bounds.uMin) bounds.uMin = u;
      if (u > bounds.uMax) bounds.uMax = u;
      if (v < bounds.vMin) bounds.vMin = v;
      if (v > bounds.vMax) bounds.vMax = v;
    }
    groundRows += placed;
    if (placed === 0) unplacedTracks++;
    positionJumps += jumps;
    if (jumps > 0) jumpTracks++;
  });

  let peak = 0;
  let running = 0;
  for (let frame = 0; frame <= frameCount; frame++) {
    running += alive[frame];
    if (running > peak) peak = running;
  }

  const placeable = groundRows > 0;
  const vehicleReach = furthestVehicles(trajectories, scale);
  const positionAnchor = positionAnchorOf(record);
  const bodyOffsetM = lengthM.map((length) => (positionAnchor === "front_centre" ? length / 2 : 0));
  return {
    ...loaded,
    id,
    site,
    fps,
    frameCount,
    durationS: Math.max(0, frameCount - 1) / fps,
    groundScale: scale,
    displayHeading: displayHeadings(trajectories, tracks),
    positionAnchor,
    bodyOffsetM,
    form,
    lengthM,
    widthM,
    indexOfTrack,
    peak: Math.max(peak, 1),
    groundRows,
    unplacedTracks,
    nominalSizeTracks,
    positionJumps,
    jumpTracks,
    placeable,
    bounds,
    footprint: [...site.lanes.flatMap((lane) => lane.polygon_m), ...vehicleReach],
    vehicleReach,
    coverage: placeable ? buildCoverage(trajectories, bounds, scale) : null,
    timeline,
  };
}

/** The vehicles of one instant. Preallocated and rewritten, so sampling allocates nothing. */
export interface FrameSample {
  count: number;
  /** Tracks alive now that pass the filters but have no ground position on this frame. */
  unplaced: number;
  outsideValid: number;
  track: Int32Array;
  row: Int32Array;
  /** The position as published. Trails and the inspector use this. */
  u: Float32Array;
  v: Float32Array;
  /** The middle of the drawn body: the position moved back along the heading if it names the nose. */
  bodyU: Float32Array;
  bodyV: Float32Array;
  heading: Float32Array;
  /** 0.3 to 1: how far the vehicle has appeared at the ends of its track. Draws only, counts stay. */
  appear: Float32Array;
}

export function createSample(capacity: number): FrameSample {
  return {
    count: 0,
    unplaced: 0,
    outsideValid: 0,
    track: new Int32Array(capacity),
    row: new Int32Array(capacity),
    u: new Float32Array(capacity),
    v: new Float32Array(capacity),
    bodyU: new Float32Array(capacity),
    bodyV: new Float32Array(capacity),
    heading: new Float32Array(capacity),
    appear: new Float32Array(capacity),
  };
}

type SampleSource = Pick<
  TwinModel,
  | "tracks"
  | "trajectories"
  | "active"
  | "displayHeading"
  | "groundScale"
  | "bodyOffsetM"
  | "fps"
  | "frameCount"
>;

function isJump(
  { gx, gy }: Pick<Trajectories, "gx" | "gy">,
  from: number,
  to: number,
  scale: number,
): boolean {
  if (gx[from] === NO_GROUND || gx[to] === NO_GROUND) return false;
  return Math.hypot(gx[to] - gx[from], gy[to] - gy[from]) * scale > POSITION_JUMP_M;
}

/** Catmull-Rom through four values. Straight-line data stays straight. */
function spline(p0: number, p1: number, p2: number, p3: number, t: number): number {
  return (
    p1 +
    0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0)))
  );
}

function smoothstep(x: number): number {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
}

/**
 * Where every kept vehicle is at a fractional frame. The file holds 10 rows a second and the screen
 * shows 60. Between two rows a vehicle follows a curve through its neighbours, so its speed doesn't
 * step at each row. It goes straight where a neighbour is a tracking jump, which stays visible
 * rather than smoothed away. A track on its last row, or whose next row has no ground position,
 * stays on the row it has.
 */
export function sampleFrame(
  source: SampleSource,
  frame: number,
  mask: Uint8Array,
  out: FrameSample,
): void {
  const { tracks, trajectories, active, displayHeading, groundScale: scale, bodyOffsetM } = source;
  const { gx, gy, pointFlags } = trajectories;
  const whole = Math.floor(frame);
  const t = frame - whole;
  const appearFrames = Math.max(1, APPEAR_S * source.fps);
  const lastFrame = source.frameCount - 1;
  out.count = 0;
  out.unplaced = 0;
  out.outsideValid = 0;
  active.forEach(whole, (index, row) => {
    if (!mask[index]) return;
    if (gx[row] === NO_GROUND) {
      out.unplaced++;
      return;
    }
    const track = tracks[index];
    const offset = whole - track.start_frame;
    const hasNext = offset + 1 < track.n && gx[row + 1] !== NO_GROUND;
    const next = hasNext ? row + 1 : row;
    const i = out.count;
    if (i >= out.track.length) return;

    const hasBefore = offset > 0 && gx[row - 1] !== NO_GROUND;
    const hasAfter = hasNext && offset + 2 < track.n && gx[row + 2] !== NO_GROUND;
    const jumped =
      isJump(trajectories, row, next, scale) ||
      (hasBefore && isJump(trajectories, row - 1, row, scale)) ||
      (hasAfter && isJump(trajectories, next, row + 2, scale));
    let u: number;
    let v: number;
    if (!hasNext || jumped) {
      u = gx[row] + (gx[next] - gx[row]) * t;
      v = gy[row] + (gy[next] - gy[row]) * t;
    } else {
      // A missing neighbour is continued in a straight line, so a track's end rows don't bend.
      const u0 = hasBefore ? gx[row - 1] : 2 * gx[row] - gx[next];
      const v0 = hasBefore ? gy[row - 1] : 2 * gy[row] - gy[next];
      const u3 = hasAfter ? gx[row + 2] : 2 * gx[next] - gx[row];
      const v3 = hasAfter ? gy[row + 2] : 2 * gy[next] - gy[row];
      u = spline(u0, gx[row], gx[next], u3, t);
      v = spline(v0, gy[row], gy[next], v3, t);
    }

    const heading = lerpAngle(displayHeading[row], displayHeading[next], t);
    out.track[i] = index;
    out.row[i] = row;
    out.u[i] = u * scale;
    out.v[i] = v * scale;
    out.heading[i] = heading;
    out.bodyU[i] = u * scale - Math.sin(heading) * bodyOffsetM[index];
    out.bodyV[i] = v * scale - Math.cos(heading) * bodyOffsetM[index];

    // A track cut by the start or end of the analysis didn't appear or leave there.
    const end = track.start_frame + track.n - 1;
    const sinceStart = track.start_frame <= 0 ? appearFrames : frame - track.start_frame;
    const untilEnd = end >= lastFrame ? appearFrames : end - frame;
    out.appear[i] =
      APPEAR_FROM + (1 - APPEAR_FROM) * smoothstep(Math.min(sinceStart, untilEnd) / appearFrames);

    if (pointFlags[row] & POINT_OUTSIDE_VALID_REGION) out.outsideValid++;
    out.count = i + 1;
  });
}
