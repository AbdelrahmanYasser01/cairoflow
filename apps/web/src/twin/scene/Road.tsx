import { useEffect, useMemo } from "react";
import {
  BufferAttribute,
  BufferGeometry,
  type Color,
  DoubleSide,
  MeshBasicMaterial,
  MeshStandardMaterial,
  ShapeUtils,
  Vector2,
} from "three";
import type { Point2, SiteGate, SiteLane } from "../site";
import { LEVEL, ORDER, toWorldZ, type Palette } from "./runtime";

/** Lane road surface, straight from the lane polygons. */
function surfaceGeometry(lanes: readonly SiteLane[]): BufferGeometry {
  const positions: number[] = [];
  for (const lane of lanes) {
    const contour = lane.polygon_m.map(([u, v]) => new Vector2(u, v));
    for (const [a, b, c] of ShapeUtils.triangulateShape(contour, [])) {
      const [au, av] = lane.polygon_m[a];
      const [bu, bv] = lane.polygon_m[b];
      const [cu, cv] = lane.polygon_m[c];
      // Polygons come in either winding. Every triangle must face up: a double-sided material
      // flips the normal of a back face, which leaves it lit from below, so black.
      const facesUp = (bu - au) * (cv - av) - (cu - au) * (bv - av) > 0;
      for (const index of facesUp ? [a, b, c] : [a, c, b]) {
        const [u, v] = lane.polygon_m[index];
        positions.push(u, LEVEL.road, toWorldZ(v));
      }
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  // The road is flat: every normal is straight up.
  const normals = new Float32Array(positions.length);
  for (let i = 1; i < normals.length; i += 3) normals[i] = 1;
  geometry.setAttribute("normal", new BufferAttribute(normals, 3));
  return geometry;
}

class Paint {
  positions: number[] = [];
  colours: number[] = [];

  triangle(a: Point2, b: Point2, c: Point2, colour: Color): void {
    for (const [u, v] of [a, c, b]) {
      this.positions.push(u, LEVEL.paint, toWorldZ(v));
      this.colours.push(colour.r, colour.g, colour.b);
    }
  }

  polygon(points: readonly Point2[], colour: Color): void {
    for (let i = 1; i + 1 < points.length; i++) this.triangle(points[0], points[i], points[i + 1], colour);
  }

  strip(from: Point2, to: Point2, width: number, colour: Color): void {
    const du = to[0] - from[0];
    const dv = to[1] - from[1];
    const length = Math.hypot(du, dv);
    if (length < 1e-6) return;
    const su = (-dv / length) * (width / 2);
    const sv = (du / length) * (width / 2);
    this.polygon(
      [
        [from[0] + su, from[1] + sv],
        [to[0] + su, to[1] + sv],
        [to[0] - su, to[1] - sv],
        [from[0] - su, from[1] - sv],
      ],
      colour,
    );
  }

  dashes(from: Point2, to: Point2, width: number, on: number, off: number, colour: Color): void {
    const length = Math.hypot(to[0] - from[0], to[1] - from[1]);
    for (let at = 0; at < length; at += on + off) {
      const end = Math.min(at + on, length);
      const point = (d: number): Point2 => [
        from[0] + ((to[0] - from[0]) * d) / length,
        from[1] + ((to[1] - from[1]) * d) / length,
      ];
      this.strip(point(at), point(end), width, colour);
    }
  }

  build(): BufferGeometry {
    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new BufferAttribute(new Float32Array(this.positions), 3));
    geometry.setAttribute("color", new BufferAttribute(new Float32Array(this.colours), 3));
    return geometry;
  }
}

function edgeKey(a: Point2, b: Point2): string {
  const round = ([u, v]: Point2) => `${u.toFixed(2)},${v.toFixed(2)}`;
  return [round(a), round(b)].sort().join("|");
}

/** The two long sides of a lane polygon: its boundaries with whatever lies beside it. */
function longEdges(polygon: readonly Point2[]): [Point2, Point2][] {
  const edges = polygon.map((point, i): [Point2, Point2] => [point, polygon[(i + 1) % polygon.length]]);
  const length = ([a, b]: [Point2, Point2]) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  return [...edges].sort((p, q) => length(q) - length(p)).slice(0, 2);
}

/** The ends of the lane: the middle of its short side nearest the junction, and the far one. */
function laneAxis(polygon: readonly Point2[]): { inner: Point2; outer: Point2 } {
  const [first, second] = longEdges(polygon);
  const mid = (p: Point2, q: Point2): Point2 => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
  const closer = (p: Point2, q: Point2) => (Math.hypot(...p) <= Math.hypot(...q) ? p : q);
  const further = (p: Point2, q: Point2) => (Math.hypot(...p) > Math.hypot(...q) ? p : q);
  return {
    inner: mid(closer(first[0], first[1]), closer(second[0], second[1])),
    outer: mid(further(first[0], first[1]), further(second[0], second[1])),
  };
}

/** Glyphs in lane terms: x across (right positive), y along (forward positive), metres. */
const ARROW_PARTS: Record<string, readonly (readonly Point2[])[]> = {
  through: [
    [
      [-0.13, 0],
      [0.13, 0],
      [0.13, 2.1],
      [-0.13, 2.1],
    ],
    [
      [-0.4, 2.1],
      [0.4, 2.1],
      [0, 3.2],
    ],
  ],
  left: [
    [
      [-0.13, 0],
      [0.13, 0],
      [0.13, 1.8],
      [-0.13, 1.8],
    ],
    [
      [-0.72, 1.52],
      [0.13, 1.52],
      [0.13, 1.8],
      [-0.72, 1.8],
    ],
    [
      [-0.72, 1.18],
      [-0.72, 2.14],
      [-1.34, 1.66],
    ],
  ],
};

function turnArrow(paint: Paint, lane: SiteLane, colour: Color): void {
  const { inner, outer } = laneAxis(lane.polygon_m);
  const du = inner[0] - outer[0];
  const dv = inner[1] - outer[1];
  const length = Math.hypot(du, dv);
  const along: Point2 = [du / length, dv / length];
  const right: Point2 = [along[1], -along[0]];
  const base: Point2 = [inner[0] - along[0] * 10, inner[1] - along[1] * 10];
  const place = ([x, y]: Point2): Point2 => [
    base[0] + right[0] * x + along[0] * y,
    base[1] + right[1] * x + along[1] * y,
  ];
  for (const turn of lane.allowed_turns) {
    const mirrored = turn === "right";
    const parts = ARROW_PARTS[mirrored ? "left" : turn];
    if (!parts) continue;
    for (const part of parts) {
      paint.polygon(
        part.map(([x, y]) => place(mirrored ? [-x, y] : [x, y])),
        colour,
      );
    }
  }
}

function paintGeometry(
  lanes: readonly SiteLane[],
  gates: readonly SiteGate[],
  palette: Palette,
): BufferGeometry {
  const paint = new Paint();
  const lanePaint = palette.paintDim.clone().multiplyScalar(0.85);
  const edgePaint = palette.paintDim.clone().multiplyScalar(0.6);
  const arrowPaint = palette.paintDim.clone().multiplyScalar(0.5);
  const stopPaint = palette.paint.clone().multiplyScalar(0.9);
  const exitPaint = palette.paintFaint.clone().multiplyScalar(0.5);

  const uses = new Map<string, number>();
  for (const lane of lanes) {
    for (const [a, b] of longEdges(lane.polygon_m)) {
      const key = edgeKey(a, b);
      uses.set(key, (uses.get(key) ?? 0) + 1);
    }
  }
  const drawn = new Set<string>();
  for (const lane of lanes) {
    for (const [a, b] of longEdges(lane.polygon_m)) {
      const key = edgeKey(a, b);
      if (drawn.has(key)) continue;
      drawn.add(key);
      // Measure dashes from the junction end, so they don't look cut off at the stop line.
      const start = Math.hypot(...a) < Math.hypot(...b) ? a : b;
      const end = start === a ? b : a;
      if ((uses.get(key) ?? 0) > 1) paint.dashes(start, end, 0.15, 3, 6, lanePaint);
      else paint.strip(start, end, 0.15, edgePaint);
    }
    if (lane.direction === "approach") turnArrow(paint, lane, arrowPaint);
  }
  for (const gate of gates) {
    const [from, to] = gate.segment_m;
    if (gate.role === "stop_line") paint.strip(from, to, 0.6, stopPaint);
    else paint.dashes(from, to, 0.22, 1, 1, exitPaint);
  }
  return paint.build();
}

export function Road({
  lanes,
  gates,
  palette,
}: {
  lanes: readonly SiteLane[];
  gates: readonly SiteGate[];
  palette: Palette;
}) {
  const surface = useMemo(() => surfaceGeometry(lanes), [lanes]);
  const markings = useMemo(() => paintGeometry(lanes, gates, palette), [lanes, gates, palette]);
  const surfaceMaterial = useMemo(
    () =>
      new MeshStandardMaterial({
        // Lit albedo, not a screen colour: under night light it lands near the asphalt tokens.
        color: palette.road.clone().multiplyScalar(2.5),
        roughness: 0.96,
        metalness: 0,
        transparent: true,
        depthWrite: false,
        side: DoubleSide,
        envMapIntensity: 0.12,
      }),
    [palette],
  );
  const paintMaterial = useMemo(
    () =>
      new MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        depthWrite: false,
        side: DoubleSide,
      }),
    [],
  );

  useEffect(
    () => () => {
      surface.dispose();
      markings.dispose();
      surfaceMaterial.dispose();
      paintMaterial.dispose();
    },
    [surface, markings, surfaceMaterial, paintMaterial],
  );

  return (
    <>
      <mesh geometry={surface} material={surfaceMaterial} renderOrder={ORDER.road} />
      <mesh geometry={markings} material={paintMaterial} renderOrder={ORDER.paint} />
    </>
  );
}
