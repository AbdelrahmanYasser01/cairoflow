import { PerspectiveCamera, Vector3 } from "three";
import { describe, expect, it } from "vitest";
import { FOV_DEG, poseFor } from "./scene/framing";
import { ActiveTracks, NO_GROUND, type TrackEntry, type Trajectories } from "../lib/analysis";
import { NO_FILTERS, NOT_RECORDED, filterValue, trackMask } from "./controls";
import { accumulateDensity, blurCells, toHeatBytes } from "./density";
import {
  anchorFrom,
  gridNorthInJunction,
  gridToLatLon,
  junctionToGrid,
  latLonToGrid,
  METRES_PER_US_FOOT,
} from "./geo";
import { createSample, displayHeadings, lerpAngle, positionAnchorOf, sampleFrame } from "./model";
import { buildNetwork, clipToVerge, insidePolygon, onStreet } from "./network";
import { CONTEXT_MAX_HEIGHT_M, contextBlocks } from "./context";
import { parseAnalysisRows, parseSite } from "./site";

const SCALE = 0.02;

function track(over: Partial<TrackEntry>): TrackEntry {
  return {
    track_id: 0,
    first_row: 0,
    start_frame: 0,
    n: 1,
    vehicle_class: "car",
    class_source: "size",
    status: "complete",
    ...over,
  };
}

function trajectories(columns: {
  gx: number[];
  gy: number[];
  heading?: number[];
  speed?: number[];
}): Trajectories {
  const n = columns.gx.length;
  return {
    header: { nPoints: n, nTracks: 1, nFrames: n, fps: 10, pxScale: 1, groundScale: SCALE, flags: 0 },
    x: new Uint16Array(n),
    y: new Uint16Array(n),
    w: new Uint16Array(n),
    h: new Uint16Array(n),
    gx: Int16Array.from(columns.gx),
    gy: Int16Array.from(columns.gy),
    speedCmS: Uint16Array.from(columns.speed ?? columns.gx.map(() => 1000)),
    heading: Uint8Array.from(columns.heading ?? columns.gx.map(() => 0)),
    conf: new Uint8Array(n),
    pointFlags: new Uint8Array(n),
  };
}

function source(t: Trajectories, tracks: TrackEntry[], bodyOffset = 0) {
  return {
    tracks,
    trajectories: t,
    active: new ActiveTracks(
      tracks.map((x) => ({ firstRow: x.first_row, startFrame: x.start_frame, n: x.n })),
      t.header.nFrames,
    ),
    displayHeading: displayHeadings(t, tracks),
    groundScale: SCALE,
    fps: 10,
    frameCount: t.header.nFrames,
    bodyOffsetM: new Float32Array(tracks.length).fill(bodyOffset),
  };
}

describe("sampleFrame", () => {
  const t = trajectories({ gx: [0, 100, 200], gy: [0, 0, 0], heading: [64, 64, 64] });
  const s = source(t, [track({ n: 3 })]);
  const all = new Uint8Array([1]);

  it("reads ground metres as stored value times ground scale", () => {
    const out = createSample(4);
    sampleFrame(s, 1, all, out);
    expect(out.u[0]).toBeCloseTo(100 * SCALE, 5);
  });

  it("interpolates a fractional frame between rows", () => {
    const out = createSample(4);
    sampleFrame(s, 0.25, all, out);
    expect(out.count).toBe(1);
    expect(out.u[0]).toBeCloseTo(25 * SCALE, 5);
  });

  it("holds the last row rather than running past the end of a track", () => {
    const out = createSample(4);
    sampleFrame(s, 2.9, all, out);
    expect(out.u[0]).toBeCloseTo(200 * SCALE, 5);
  });

  it("drops filtered tracks and tracks that aren't alive", () => {
    const out = createSample(4);
    sampleFrame(s, 1, new Uint8Array([0]), out);
    expect(out.count).toBe(0);
    sampleFrame(s, 5, all, out);
    expect(out.count).toBe(0);
  });

  it("counts a track with no ground position as unplaced, and places nothing", () => {
    const blind = source(trajectories({ gx: [NO_GROUND, NO_GROUND], gy: [NO_GROUND, NO_GROUND] }), [
      track({ n: 2 }),
    ]);
    const out = createSample(4);
    sampleFrame(blind, 0.5, all, out);
    expect(out.count).toBe(0);
    expect(out.unplaced).toBe(1);
  });

  it("won't interpolate toward a row that has no ground position", () => {
    const gap = source(trajectories({ gx: [100, NO_GROUND], gy: [0, NO_GROUND] }), [track({ n: 2 })]);
    const out = createSample(4);
    sampleFrame(gap, 0.5, all, out);
    expect(out.u[0]).toBeCloseTo(100 * SCALE, 5);
  });

  it("follows a curve where the speed changes, and still passes through the rows", () => {
    const braking = source(trajectories({ gx: [0, 100, 150, 175], gy: [0, 0, 0, 0] }), [track({ n: 4 })]);
    const out = createSample(4);
    sampleFrame(braking, 1, all, out);
    expect(out.u[0]).toBeCloseTo(100 * SCALE, 5);
    sampleFrame(braking, 1.5, all, out);
    expect(out.u[0]).toBeGreaterThan(125 * SCALE);
    expect(out.u[0]).toBeLessThan(150 * SCALE);
  });

  it("goes straight across a tracking jump instead of bending around it", () => {
    const leap = source(trajectories({ gx: [0, 10, 500, 510], gy: [0, 0, 0, 0] }), [track({ n: 4 })]);
    const out = createSample(4);
    sampleFrame(leap, 1.5, all, out);
    expect(out.u[0]).toBeCloseTo(255 * SCALE, 5);
  });
});

describe("body anchor", () => {
  const all = new Uint8Array([1]);
  const moving = trajectories({ gx: [0, 0, 0], gy: [0, 500, 1000] });

  it("puts the body behind a position that names the front of the vehicle", () => {
    const out = createSample(4);
    sampleFrame(source(moving, [track({ n: 3 })], 2.5), 1, all, out);
    expect(out.v[0]).toBeCloseTo(500 * SCALE, 4);
    expect(out.bodyV[0]).toBeCloseTo(500 * SCALE - 2.5, 4);
    expect(out.bodyU[0]).toBeCloseTo(0, 4);
  });

  it("leaves the body on the position for any other source", () => {
    const out = createSample(4);
    sampleFrame(source(moving, [track({ n: 3 })], 0), 1, all, out);
    expect(out.bodyV[0]).toBeCloseTo(out.v[0], 5);
  });

  it("only treats published NGSIM positions as front centres", () => {
    const published = { source_id: "ngsim-lankershim-trajectories" };
    expect(positionAnchorOf({ data_origin: "reference_dataset", source: published })).toBe("front_centre");
    expect(positionAnchorOf({ data_origin: "reference_dataset", source: { source_id: "other" } })).toBe("centre");
    expect(positionAnchorOf({ data_origin: "reference_dataset", source: null })).toBe("centre");
  });

  it("doesn't shift camera-derived positions, even from NGSIM footage", () => {
    const footage = { source_id: "ngsim-lankershim-cam2-0830" };
    expect(positionAnchorOf({ data_origin: "camera_cv", source: footage })).toBe("centre");
  });
});

describe("appearing", () => {
  const rows = Array.from({ length: 30 }, (_, i) => i * 100);
  const t = trajectories({ gx: rows, gy: rows.map(() => 0) });
  const all = new Uint8Array([1]);
  const out = createSample(4);

  it("starts small and reaches full size inside 0.4 s, while staying counted", () => {
    const mid = source(t, [track({ start_frame: 5, n: 20 })]);
    sampleFrame(mid, 5, all, out);
    expect(out.count).toBe(1);
    expect(out.appear[0]).toBeCloseTo(0.3, 5);
    sampleFrame(mid, 12, all, out);
    expect(out.appear[0]).toBe(1);
    sampleFrame(mid, 24, all, out);
    expect(out.appear[0]).toBeCloseTo(0.3, 5);
  });

  it("doesn't shrink a track cut off by the start or end of the analysis", () => {
    const whole = source(t, [track({ start_frame: 0, n: 30 })]);
    sampleFrame(whole, 0, all, out);
    expect(out.appear[0]).toBe(1);
    sampleFrame(whole, 29, all, out);
    expect(out.appear[0]).toBe(1);
  });
});

describe("headings", () => {
  it("turns the short way across north", () => {
    expect(lerpAngle(Math.PI * 1.9, Math.PI * 0.1, 0.5) % (Math.PI * 2)).toBeCloseTo(0, 5);
  });

  it("reads 64 steps as a quarter turn clockwise from +v", () => {
    const t = trajectories({ gx: [0, 1, 2], gy: [0, 0, 0], heading: [64, 64, 64] });
    const out = displayHeadings(t, [track({ n: 3 })]);
    expect(out[1]).toBeCloseTo(Math.PI / 2, 4);
  });

  it("keeps a stopped vehicle's direction when the published column jumps", () => {
    const t = trajectories({
      gx: Array(12).fill(0),
      gy: Array(12).fill(0),
      heading: [64, 64, 64, 200, 10, 120, 33, 200, 10, 120, 33, 200],
      speed: [500, 500, 500, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    });
    const out = displayHeadings(t, [track({ n: 12 })]);
    expect(out[11]).toBeCloseTo(Math.PI / 2, 4);
  });

  it("takes direction from the path when the file records no heading at all", () => {
    const t = trajectories({ gx: [0, 100, 200, 300], gy: [0, 0, 0, 0] });
    const out = displayHeadings(t, [track({ n: 4 })]);
    expect(out[1]).toBeCloseTo(Math.PI / 2, 4);
  });

  it("points a queued vehicle down its lane whatever the column says at the stop", () => {
    // Travels up +v at 1 m a row, stops for eight rows, then goes on: the column is noise at the stop.
    const gy = [0, 50, 100, 150, 200, 250, ...Array(8).fill(300), 350, 400, 450, 500, 550, 600];
    const heading = [0, 0, 0, 0, 0, 96, 32, 160, 224, 64, 128, 192, 96, 32, 0, 0, 0, 0, 0, 0];
    const t = trajectories({ gx: gy.map(() => 0), gy, heading, speed: gy.map(() => 40) });
    const out = displayHeadings(t, [track({ n: gy.length })]);
    for (let i = 0; i < gy.length; i++) expect(Math.abs(out[i])).toBeLessThan(0.01);
  });

  it("gives every row of a stop one direction, and one that follows a turn", () => {
    // East along +u, stopped at the corner, then north along +v.
    const gx = [0, 50, 100, 150, 200, 250, 250, 250, 250, 250, 250, 250, 250, 250, 250];
    const gy = [0, 0, 0, 0, 0, 0, 0, 0, 0, 50, 100, 150, 200, 250, 300];
    const out = displayHeadings(trajectories({ gx, gy }), [track({ n: gx.length })]);
    expect(out[6]).toBeCloseTo(out[8], 5);
    expect(out[0]).toBeCloseTo(Math.PI / 2, 4);
    expect(out[gx.length - 1]).toBeCloseTo(0, 4);
    expect(out[7]).toBeCloseTo(Math.PI / 4, 4);
  });

  it("ignores jitter at a stop, which a raw path length would count as travel", () => {
    const jitter = Array.from({ length: 40 }, (_, i) => (i % 2 ? 5 : -5));
    const t = trajectories({
      gx: [...jitter, 0, 0, 0, 0, 0, 0],
      gy: [...jitter.map(() => 0), 50, 100, 150, 200, 250, 300],
    });
    const out = displayHeadings(t, [track({ n: 46 })]);
    expect(Math.abs(out[20])).toBeLessThan(0.1);
  });
});

describe("camera framing", () => {
  const STAGE = { width: 1282, height: 724, insets: { top: 84, right: 24, bottom: 195, left: 24 } };
  // The real site's shape: a long thin road with short cross arms.
  const site = {
    bounds: { uMin: -30, uMax: 32, vMin: -110, vMax: 110 },
    footprint: [
      [-18, 110],
      [16, 110],
      [-18, -110],
      [16, -110],
      [-30, -12],
      [-30, 12],
      [32, -12],
      [32, 12],
    ] as [number, number][],
    vehicleReach: [
      [-18, 110],
      [16, -110],
      [32, 12],
      [-30, -12],
    ] as [number, number][],
  };

  /** Pixels on the stage through a real camera with the same view offset ViewFrame sets. */
  function project(view: "oblique" | "along", mode: "twin" | "geo", stage = STAGE) {
    const [x, y, z, tx, ty, tz] = poseFor(view, mode, site, stage, null);
    const camera = new PerspectiveCamera(FOV_DEG, stage.width / stage.height, 1, 2600);
    const { insets } = stage;
    camera.setViewOffset(
      stage.width,
      stage.height,
      (insets.right - insets.left) / 2,
      (insets.bottom - insets.top) / 2,
      stage.width,
      stage.height,
    );
    camera.position.set(x, y, z);
    camera.lookAt(tx, ty, tz);
    camera.updateMatrixWorld();
    // Oblique holds the whole site; Along holds where the vehicles went.
    const kept = view === "along" ? site.vehicleReach : site.footprint;
    return kept.map(([u, v]) => {
      const point = new Vector3(u, 0, -v).project(camera);
      return [(point.x * 0.5 + 0.5) * stage.width, (0.5 - point.y * 0.5) * stage.height];
    });
  }

  for (const view of ["oblique", "along"] as const) {
    for (const mode of ["twin", "geo"] as const) {
      it(`keeps the site clear of the instruments in ${view}, ${mode} mode`, () => {
        const { width, height, insets } = STAGE;
        for (const [px, py] of project(view, mode)) {
          expect(px).toBeGreaterThanOrEqual(insets.left - 0.5);
          expect(px).toBeLessThanOrEqual(width - insets.right + 0.5);
          expect(py).toBeGreaterThanOrEqual(insets.top - 0.5);
          expect(py).toBeLessThanOrEqual(height - insets.bottom + 0.5);
        }
      });
    }
  }

  it("fills the free area rather than just fitting inside it", () => {
    const { width, height, insets } = STAGE;
    const points = project("oblique", "twin");
    const spanX = Math.max(...points.map((p) => p[0])) - Math.min(...points.map((p) => p[0]));
    const spanY = Math.max(...points.map((p) => p[1])) - Math.min(...points.map((p) => p[1]));
    const fillX = spanX / (width - insets.left - insets.right);
    const fillY = spanY / (height - insets.top - insets.bottom);
    expect(Math.max(fillX, fillY)).toBeGreaterThan(0.9);
  });

  it("refits when the dock takes more of the stage", () => {
    const tall = { ...STAGE, insets: { ...STAGE.insets, bottom: 320 } };
    for (const [, py] of project("oblique", "twin", tall)) {
      expect(py).toBeLessThanOrEqual(tall.height - 320 + 0.5);
    }
  });

  it("has somewhere to look when the site has no extent", () => {
    for (const view of ["oblique", "along"] as const) {
      const pose = poseFor(view, "twin", { ...site, footprint: [], vehicleReach: [] }, STAGE, null);
      expect(pose.every(Number.isFinite)).toBe(true);
    }
  });

  it("closes in along the road when vehicles only reach part of the site", () => {
    const eyeToTarget = ([x, y, z, tx, ty, tz]: readonly number[]) => Math.hypot(x - tx, y - ty, z - tz);
    const whole = poseFor("along", "twin", site, STAGE, null);
    const part = poseFor("along", "twin", { ...site, vehicleReach: [[-13, 82], [12, -41]] }, STAGE, null);
    expect(eyeToTarget(part)).toBeLessThan(eyeToTarget(whole));
  });
});

describe("filters", () => {
  const tracks = [
    track({ track_id: 1, vehicle_class: "bus", approach: "N", turn: "left", start_frame: 0, n: 10 }),
    track({ track_id: 2, approach: null, turn: null, start_frame: 100, n: 10 }),
  ];

  it("keeps everything by default, absent fields included", () => {
    expect([...trackMask(tracks, NO_FILTERS, 10)]).toEqual([1, 1]);
  });

  it("treats a null field as its own value", () => {
    expect(filterValue(tracks[1], "turns")).toBe(NOT_RECORDED);
    const hidden = { ...NO_FILTERS.hidden, turns: new Set([NOT_RECORDED]) };
    expect([...trackMask(tracks, { ...NO_FILTERS, hidden }, 10)]).toEqual([1, 0]);
  });

  it("keeps tracks that overlap the time window, and only those", () => {
    expect([...trackMask(tracks, { ...NO_FILTERS, timeS: [0, 5] }, 10)]).toEqual([1, 0]);
    expect([...trackMask(tracks, { ...NO_FILTERS, timeS: [10.5, 20] }, 10)]).toEqual([0, 1]);
  });
});

describe("density", () => {
  const t = trajectories({ gx: [0, 0, 0, 500], gy: [0, 0, 0, 0] });
  const s = source(t, [track({ n: 4 })]);
  const bounds = { uMin: 0, uMax: 20, vMin: 0, vMax: 4 };

  it("holds vehicle-seconds: three rows at 10 fps in one cell is 0.3", () => {
    const grid = accumulateDensity(s, new Uint8Array([1]), bounds, [0, 3], 2);
    expect(grid.peak).toBeCloseTo(0.3, 5);
    expect(grid.total).toBeCloseTo(0.4, 5);
  });

  it("honours the mask and the frame window", () => {
    expect(accumulateDensity(s, new Uint8Array([0]), bounds, [0, 3], 2).total).toBe(0);
    expect(accumulateDensity(s, new Uint8Array([1]), bounds, [3, 3], 2).total).toBeCloseTo(0.1, 5);
  });

  it("blurs without losing the shape, and maps the fullest cell to 255", () => {
    const grid = accumulateDensity(s, new Uint8Array([1]), bounds, [0, 3], 2);
    const bytes = toHeatBytes(blurCells(grid, 1));
    expect(Math.max(...bytes)).toBe(255);
    expect(toHeatBytes(new Float32Array(4)).every((b) => b === 0)).toBe(true);
  });
});

describe("EPSG:2229", () => {
  // The site's frame: origin_state_plane_ft in scenario.json, lat/lon in record.calibration.
  const origin = { latDeg: 34.1390320740147, lonDeg: -118.36214356208536 };
  const stated = { eastFt: 6452083.0, northFt: 1873176.0 };

  it("agrees with the state plane origin the repository recorded", () => {
    const grid = latLonToGrid(origin);
    expect(Math.abs(grid.eastFt - stated.eastFt)).toBeLessThan(0.05);
    expect(Math.abs(grid.northFt - stated.northFt)).toBeLessThan(0.05);
  });

  it("round-trips grid to latitude and longitude", () => {
    const back = gridToLatLon(stated);
    expect(back.latDeg).toBeCloseTo(origin.latDeg, 8);
    expect(back.lonDeg).toBeCloseTo(origin.lonDeg, 8);
  });

  it("puts +v on the recorded grid bearing", () => {
    const anchor = anchorFrom({
      mode: "calibrated",
      origin_lat_deg: origin.latDeg,
      origin_lon_deg: origin.lonDeg,
      bearing_deg: 25.8215,
    });
    if (!anchor) throw new Error("anchor expected");
    const ahead = junctionToGrid(anchor, 0, 100);
    const east = (ahead.eastFt - anchor.originGrid.eastFt) * METRES_PER_US_FOOT;
    const north = (ahead.northFt - anchor.originGrid.northFt) * METRES_PER_US_FOOT;
    expect((Math.atan2(east, north) * 180) / Math.PI).toBeCloseTo(25.8215, 6);
    expect(Math.hypot(east, north)).toBeCloseTo(100, 6);
  });

  it("points grid north counter-clockwise of +v by the bearing", () => {
    const [u, v] = gridNorthInJunction(25.8215);
    expect(u).toBeLessThan(0);
    expect(v).toBeGreaterThan(0);
    expect(Math.hypot(u, v)).toBeCloseTo(1, 9);
  });

  it("has no anchor without an origin", () => {
    expect(anchorFrom({ mode: "uncalibrated" })).toBeNull();
    expect(anchorFrom(null)).toBeNull();
  });
});

describe("site parsing", () => {
  const lane = {
    lane_id: "N1",
    arm: "N",
    direction: "approach",
    polygon_m: [
      [0, 0],
      [1, 0],
      [1, 1],
    ],
    allowed_turns: ["through"],
    provenance: "approximated",
    source: "test",
  };

  it("takes a well formed response and defaults what's missing", () => {
    const site = parseSite({ lanes: [lane], gates: [] });
    expect(site.lanes).toHaveLength(1);
    expect(site.calibration).toBeNull();
  });

  it("names what is wrong with a bad one", () => {
    expect(() => parseSite(null)).toThrow(/no lanes/);
    expect(() => parseSite({ lanes: [{ ...lane, polygon_m: [[0, 0]] }], gates: [] })).toThrow(/N1/);
    expect(() =>
      parseSite({ lanes: [], gates: [{ arm: "N", role: "stop_line", segment_m: [[0, 0]] }] }),
    ).toThrow(/stop_line/);
  });

  it("reads analysis rows in the server's shape and the shared type's shape", () => {
    expect(parseAnalysisRows({ analyses: [{ id: "a", title: "A", valid: true }] })[0].id).toBe("a");
    expect(parseAnalysisRows([{ analysis_id: "b", title: "B", valid: false }])[0]).toEqual({
      id: "b",
      title: "B",
      valid: false,
    });
    expect(parseAnalysisRows(null)).toEqual([]);
  });
});

describe("road network", () => {
  const rect = (u0: number, u1: number, v0: number, v1: number) => [
    [u0, v0],
    [u1, v0],
    [u1, v1],
    [u0, v1],
  ];
  const lane = (id: string, arm: string, direction: string, polygon: number[][]) => ({
    lane_id: id,
    arm,
    direction,
    polygon_m: polygon,
    allowed_turns: ["through"],
    provenance: "approximated",
    source: "test",
  });
  const site = parseSite({
    lanes: [
      lane("N1", "N", "approach", rect(-7, 0, 20, 100)),
      lane("Nx", "N", "exit", rect(0, 7, 25, 55)),
      lane("S1", "S", "approach", rect(0, 7, -100, -20)),
      lane("E1", "E", "approach", rect(14, 40, 0, 7)),
      lane("W1", "W", "approach", rect(-40, -14, -7, 0)),
    ],
    gates: [],
  });
  // Metres to stored units at the test's ground scale.
  const at = (points: number[][]) =>
    trajectories({ gx: points.map(([u]) => Math.round(u / SCALE)), gy: points.map(([, v]) => Math.round(v / SCALE)) });

  it("is one closed outline that holds every lane and the junction box", () => {
    const network = buildNetwork(site, null, SCALE);
    for (const [u, v] of [[-3, 60], [3, 40], [3, -60], [30, 3], [-30, -3], [0, 0], [-6, 10], [6, -10]]) {
      expect(insidePolygon(network.outline, u, v)).toBe(true);
    }
    // Off the corners of the box and well clear of every arm.
    expect(insidePolygon(network.outline, 40, 40)).toBe(false);
    expect(insidePolygon(network.outline, -40, -40)).toBe(false);
    // The arms run on past the data to the scene edge.
    expect(insidePolygon(network.outline, 3, 300)).toBe(true);
    expect(insidePolygon(network.outline, -300, -3)).toBe(true);
  });

  it("widens to where vehicles drove, so none is drawn off the asphalt", () => {
    const network = buildNetwork(site, at([[9.5, 40], [-3, 60], [3, -60]]), SCALE);
    expect(network.sections.N.hi).toBeGreaterThanOrEqual(9.5);
    expect(network.offRoad).toBe(0);
  });

  it("opens a side entrance where vehicles left the arm, rather than widening all of it", () => {
    const network = buildNetwork(site, at([[18, 80], [19, 82], [17.5, 84]]), SCALE);
    expect(network.offRoad).toBe(0);
    expect(network.sections.N.hi).toBeLessThan(17.5);
    expect(insidePolygon(network.outline, 18, 50)).toBe(false);
  });

  it("stands context blocks only on clear ground, off the road, sidewalks and side entrances", () => {
    const network = buildNetwork(site, at([[18, 80], [19, 82], [17.5, 84]]), SCALE);
    const blocks = contextBlocks(network, null, (u, v) => Math.hypot(u, v) < 200);
    expect(blocks.length).toBeGreaterThan(4);
    let onRoad = 0;
    for (const block of blocks) {
      expect(block.height).toBeLessThanOrEqual(CONTEXT_MAX_HEIGHT_M);
      for (let u = block.uMin; u <= block.uMax; u += 1) {
        for (let v = block.vMin; v <= block.vMax; v += 1) {
          if (onStreet(network, u, v) || insidePolygon(network.outline, u, v)) onRoad++;
        }
      }
    }
    expect(onRoad).toBe(0);
    // Low enough near the road that a camera ray at 8 degrees or more clears every block.
    const near = blocks.filter((b) => b.uMin > 0 && b.vMin > 0);
    for (const block of near) {
      const fromRoad = Math.min(block.uMin - network.sections.N.hi, block.vMin - network.sections.E.hi) + 1.25;
      expect(block.height).toBeLessThanOrEqual(1 + fromRoad * Math.tan((8 * Math.PI) / 180) + 1e-9);
    }
    // Nothing stands on the side entrance vehicles used.
    expect(blocks.some((b) => b.uMin <= 18 && b.uMax >= 18 && b.vMin <= 82 && b.vMax >= 82)).toBe(false);
  });

  it("keeps the geo axes off the street", () => {
    const network = buildNetwork(site, null, SCALE);
    const runs = clipToVerge(network, [-60, -150], [-60, 150]);
    expect(runs.length).toBe(2);
    for (const [from, to] of runs) {
      expect(onStreet(network, from[0], from[1])).toBe(false);
      expect(onStreet(network, to[0], to[1])).toBe(false);
    }
  });
});
