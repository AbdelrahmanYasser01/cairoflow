import { useEffect, useMemo } from "react";
import {
  BufferAttribute,
  BufferGeometry,
  type Color,
  DoubleSide,
  type Material,
  MeshBasicMaterial,
  MeshStandardMaterial,
  ShaderMaterial,
  ShapeUtils,
  Vector2,
} from "three";
import type { Bounds } from "../model";
import { SIDEWALK_M, extendLane, type RoadNetwork } from "../network";
import type { Point2, SiteGate, SiteLane } from "../site";
import { FADE_FROM, FADE_TO, LEVEL, ORDER, fadeFrame, toWorldZ, type Palette } from "./runtime";

/** Kerb height. Real kerbs are about this; it reads as an edge in any oblique view. */
const KERB_H = 0.16;
/** The light kerb stone along the road edge of the sidewalk. */
const KERB_STONE_M = 0.35;

/** The carriageway outline as one watertight surface, every triangle facing up. */
function surfaceGeometry(outline: readonly Point2[]): BufferGeometry {
  const contour = outline.map(([u, v]) => new Vector2(u, v));
  const positions: number[] = [];
  for (const [a, b, c] of ShapeUtils.triangulateShape(contour, [])) {
    const [au, av] = outline[a];
    const [bu, bv] = outline[b];
    const [cu, cv] = outline[c];
    const facesUp = (bu - au) * (cv - av) - (cu - au) * (bv - av) > 0;
    for (const index of facesUp ? [a, b, c] : [a, c, b]) {
      const [u, v] = outline[index];
      positions.push(u, LEVEL.road, toWorldZ(v));
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  return geometry;
}

/** Points of a polyline moved `distance` to the left of its direction of travel, mitred. */
function offsetLeft(line: readonly Point2[], distance: number): Point2[] {
  const normals = line.slice(1).map((point, i): Point2 => {
    const du = point[0] - line[i][0];
    const dv = point[1] - line[i][1];
    const length = Math.hypot(du, dv) || 1;
    return [-dv / length, du / length];
  });
  return line.map((point, i) => {
    const before = normals[Math.max(0, i - 1)];
    const after = normals[Math.min(normals.length - 1, i)];
    let nu = before[0] + after[0];
    let nv = before[1] + after[1];
    const length = Math.hypot(nu, nv) || 1;
    nu /= length;
    nv /= length;
    const mitre = 1 / Math.max(0.5, nu * after[0] + nv * after[1]);
    return [point[0] + nu * distance * mitre, point[1] + nv * distance * mitre];
  });
}

/**
 * Raised sidewalks with a kerb face toward the road. Everything outside the carriageway that is
 * drawn at all is this, so nothing else in the scene can sit on the road.
 */
function sidewalkGeometry(kerbs: readonly (readonly Point2[])[], palette: Palette): BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  const colours: number[] = [];
  // Lit albedos: under the night key light these land just above the asphalt, never brighter than paint.
  const stone = palette.paintFaint.clone().multiplyScalar(0.2);
  const paving = palette.surface.clone().multiplyScalar(1.05);
  const face = palette.paintFaint.clone().multiplyScalar(0.12);
  const push = (u: number, y: number, v: number, n: readonly [number, number, number], colour: Color) => {
    positions.push(u, y, toWorldZ(v));
    normals.push(...n);
    colours.push(colour.r, colour.g, colour.b);
  };
  const quad = (
    a: readonly [number, number, number],
    b: readonly [number, number, number],
    c: readonly [number, number, number],
    d: readonly [number, number, number],
    n: readonly [number, number, number],
    colour: Color,
  ) => {
    for (const p of [a, b, c, a, c, d]) push(p[0], p[1], p[2], n, colour);
  };
  const UP = [0, 1, 0] as const;
  for (const kerb of kerbs) {
    const inner = offsetLeft(kerb, KERB_STONE_M);
    const outer = offsetLeft(kerb, SIDEWALK_M);
    for (let i = 0; i + 1 < kerb.length; i++) {
      const [k0, k1] = [kerb[i], kerb[i + 1]];
      const [s0, s1] = [inner[i], inner[i + 1]];
      const [o0, o1] = [outer[i], outer[i + 1]];
      quad([k0[0], KERB_H, k0[1]], [k1[0], KERB_H, k1[1]], [s1[0], KERB_H, s1[1]], [s0[0], KERB_H, s0[1]], UP, stone);
      quad([s0[0], KERB_H, s0[1]], [s1[0], KERB_H, s1[1]], [o1[0], KERB_H, o1[1]], [o0[0], KERB_H, o0[1]], UP, paving);
      // The kerb face looks at the road, which lies to the right of the kerb's direction.
      const du = k1[0] - k0[0];
      const dv = k1[1] - k0[1];
      const length = Math.hypot(du, dv) || 1;
      const n = [dv / length, 0, du / length] as const;
      quad([k0[0], 0, k0[1]], [k1[0], 0, k1[1]], [k1[0], KERB_H, k1[1]], [k0[0], KERB_H, k0[1]], n, face);
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  geometry.setAttribute("normal", new BufferAttribute(new Float32Array(normals), 3));
  geometry.setAttribute("color", new BufferAttribute(new Float32Array(colours), 3));
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

/** Zebra bars just inside the stop line of a Lankershim arm, across its whole carriageway. */
function crosswalk(paint: Paint, network: RoadNetwork, arm: "N" | "S", colour: Color): void {
  const section = network.sections[arm];
  const sign = arm === "N" ? 1 : -1;
  const near = (section.mouth - 1.2) * sign;
  const far = (section.mouth - 4.4) * sign;
  for (let u = section.lo + 0.9; u + 0.55 <= section.hi - 0.6; u += 1.25) {
    paint.polygon(
      [
        [u, near],
        [u + 0.55, near],
        [u + 0.55, far],
        [u, far],
      ],
      colour,
    );
  }
}

function paintGeometry(
  lanes: readonly SiteLane[],
  gates: readonly SiteGate[],
  network: RoadNetwork,
  palette: Palette,
): BufferGeometry {
  const paint = new Paint();
  const lanePaint = palette.paintDim.clone().multiplyScalar(0.85);
  const edgePaint = palette.paintDim.clone().multiplyScalar(0.62);
  const arrowPaint = palette.paintDim.clone().multiplyScalar(0.5);
  const stopPaint = palette.paint.clone().multiplyScalar(0.9);
  const zebraPaint = palette.paintDim.clone().multiplyScalar(0.55);
  const exitPaint = palette.paintFaint.clone().multiplyScalar(0.5);

  // Each lane runs on to the end of its arm, so the paint is as continuous as the road.
  const extended = lanes.map((lane) => ({ ...lane, polygon_m: extendLane(lane.polygon_m, lane.arm) }));
  const uses = new Map<string, number>();
  for (const lane of extended) {
    for (const [a, b] of longEdges(lane.polygon_m)) {
      const key = edgeKey(a, b);
      uses.set(key, (uses.get(key) ?? 0) + 1);
    }
  }
  const drawn = new Set<string>();
  for (const lane of extended) {
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
  crosswalk(paint, network, "N", zebraPaint);
  crosswalk(paint, network, "S", zebraPaint);
  return paint.build();
}

const ASPHALT_VERTEX = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

/** Asphalt: a flat dark tone with fine aggregate and slow patching, fading on the floor's ellipse. */
const ASPHALT_FRAGMENT = /* glsl */ `
  varying vec3 vWorld;
  uniform vec3 uAsphalt;
  uniform vec3 uRoom;
  uniform vec2 uCentre;
  uniform vec2 uRadius;

  float hash(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
  }
  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 w = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), w.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), w.x), w.y);
  }

  void main() {
    vec2 p = vec2(vWorld.x, -vWorld.z);
    float grain = noise(p * 3.1) * 0.6 + noise(p * 0.21) * 0.4;
    vec3 colour = uAsphalt * (0.9 + grain * 0.2);
    float reach = length((p - uCentre) / uRadius);
    colour = mix(colour, uRoom, smoothstep(${FADE_FROM.toFixed(3)}, ${FADE_TO.toFixed(3)}, reach));
    gl_FragColor = vec4(colour, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

/**
 * Fades a built-in material on the floor's ellipse, the same way the ground shader does: toward
 * the room colour when opaque, out to nothing when it is paint.
 */
function fadeOnFloor<T extends Material>(material: T, bounds: Bounds, room: Color, mode: "colour" | "alpha"): T {
  const { centre, radius } = fadeFrame(bounds);
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uFadeCentre = { value: new Vector2(...centre) };
    shader.uniforms.uFadeRadius = { value: new Vector2(...radius) };
    shader.uniforms.uFadeRoom = { value: room };
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vFadeWorld;")
      .replace(
        "#include <project_vertex>",
        "#include <project_vertex>\nvFadeWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;",
      );
    const apply =
      mode === "alpha"
        ? "gl_FragColor.a *= 1.0 - fadeAmount;"
        : "gl_FragColor.rgb = mix(gl_FragColor.rgb, uFadeRoom, fadeAmount);";
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        "#include <common>\nvarying vec3 vFadeWorld;\nuniform vec2 uFadeCentre;\nuniform vec2 uFadeRadius;\nuniform vec3 uFadeRoom;",
      )
      .replace(
        "#include <tonemapping_fragment>",
        `{
          vec2 fadePoint = vec2(vFadeWorld.x, -vFadeWorld.z);
          float fadeAmount = smoothstep(${FADE_FROM.toFixed(3)}, ${FADE_TO.toFixed(3)}, length((fadePoint - uFadeCentre) / uFadeRadius));
          ${apply}
        }
        #include <tonemapping_fragment>`,
      );
  };
  material.customProgramCacheKey = () => `cf-floor-fade-${mode}`;
  return material;
}

export function Road({
  lanes,
  gates,
  network,
  bounds,
  palette,
}: {
  lanes: readonly SiteLane[];
  gates: readonly SiteGate[];
  network: RoadNetwork;
  bounds: Bounds;
  palette: Palette;
}) {
  const surface = useMemo(() => surfaceGeometry(network.outline), [network]);
  const sidewalks = useMemo(() => sidewalkGeometry(network.kerbs, palette), [network, palette]);
  const markings = useMemo(() => paintGeometry(lanes, gates, network, palette), [lanes, gates, network, palette]);
  const surfaceMaterial = useMemo(() => {
    const { centre, radius } = fadeFrame(bounds);
    return new ShaderMaterial({
      vertexShader: ASPHALT_VERTEX,
      fragmentShader: ASPHALT_FRAGMENT,
      depthWrite: false,
      side: DoubleSide,
      uniforms: {
        uAsphalt: { value: palette.road.clone().multiplyScalar(1.12) },
        uRoom: { value: palette.room },
        uCentre: { value: new Vector2(...centre) },
        uRadius: { value: new Vector2(...radius) },
      },
    });
  }, [palette, bounds]);
  const sidewalkMaterial = useMemo(
    () =>
      fadeOnFloor(
        new MeshStandardMaterial({ vertexColors: true, roughness: 0.92, metalness: 0, envMapIntensity: 0.25 }),
        bounds,
        palette.room,
        "colour",
      ),
    [palette, bounds],
  );
  const paintMaterial = useMemo(
    () =>
      fadeOnFloor(
        new MeshBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false, side: DoubleSide }),
        bounds,
        palette.room,
        "alpha",
      ),
    [palette, bounds],
  );

  useEffect(
    () => () => {
      surface.dispose();
      sidewalks.dispose();
      markings.dispose();
      surfaceMaterial.dispose();
      sidewalkMaterial.dispose();
      paintMaterial.dispose();
    },
    [surface, sidewalks, markings, surfaceMaterial, sidewalkMaterial, paintMaterial],
  );

  return (
    <>
      <mesh geometry={surface} material={surfaceMaterial} renderOrder={ORDER.road} />
      <mesh geometry={markings} material={paintMaterial} renderOrder={ORDER.paint} />
      <mesh geometry={sidewalks} material={sidewalkMaterial} receiveShadow />
    </>
  );
}
