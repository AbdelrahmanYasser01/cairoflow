import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { BufferAttribute, BufferGeometry, type Color, LineBasicMaterial, Vector3 } from "three";
import {
  formatLat,
  formatLon,
  gridNorthInJunction,
  junctionToLatLon,
  type GeoAnchor,
} from "../geo";
import type { Bounds } from "../model";
import { clipToVerge, onStreet, type RoadNetwork } from "../network";
import type { Arm, Point2, SiteGeometry } from "../site";
import { CHROME_ATTRIBUTE, overlayNode, toCanvasPixels } from "./overlay";
import { LEVEL, ORDER, toWorldZ, type Insets, type Palette } from "./runtime";

interface LabelLine {
  text: string;
  /** Text classes for this line. */
  tone?: string;
}

export interface LabelSpec {
  id: string;
  /** u, height, v in metres. */
  at: readonly [number, number, number];
  lines: readonly LabelLine[];
  /** Where the label sits against its point: on it, to its left, or under it. */
  align?: "centre" | "before" | "below";
}

const TICK_GAP_PX = 10;
const EDGE_PX = 2;
const CHROME_SCAN_S = 0.25;

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface PlacedLabel {
  spec: LabelSpec;
  node: HTMLDivElement;
  width: number;
  height: number;
  visible: boolean;
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

/**
 * Every label in the scene. A label that would fall behind an instrument, off the stage or on top
 * of another label fades out rather than show as a clipped fragment. Earlier labels win.
 */
export function SceneLabels({ labels, insets }: { labels: readonly LabelSpec[]; insets: Insets }) {
  const gl = useThree((state) => state.gl);
  const placed = useRef<PlacedLabel[]>([]);
  const chrome = useRef<Box[]>([]);
  const lastScan = useRef(-Infinity);
  const point = useMemo(() => new Vector3(), []);

  useLayoutEffect(() => {
    const host = overlayNode(gl, "div", "pointer-events-none absolute inset-0 overflow-hidden");
    placed.current = labels.map((spec) => {
      const node = document.createElement("div");
      node.className = "mono absolute top-0 left-0 whitespace-nowrap bg-asphalt-0/70 px-1 text-paint-3 select-none";
      node.style.opacity = "0";
      node.style.transition = "opacity 160ms linear";
      node.style.willChange = "transform";
      for (const line of spec.lines) {
        const row = document.createElement("span");
        row.className = `block ${line.tone ?? ""}`;
        row.textContent = line.text;
        node.appendChild(row);
      }
      host.appendChild(node);
      return { spec, node, width: node.offsetWidth, height: node.offsetHeight, visible: false };
    });
    return () => {
      host.remove();
      placed.current = [];
    };
  }, [gl, labels]);

  useFrame(({ camera, size, clock }) => {
    if (clock.elapsedTime - lastScan.current >= CHROME_SCAN_S) {
      lastScan.current = clock.elapsedTime;
      const canvas = gl.domElement.getBoundingClientRect();
      chrome.current = [...document.querySelectorAll(`[${CHROME_ATTRIBUTE}]`)].map((element) => {
        const box = element.getBoundingClientRect();
        return {
          left: box.left - canvas.left,
          top: box.top - canvas.top,
          right: box.right - canvas.left,
          bottom: box.bottom - canvas.top,
        };
      });
    }
    camera.updateMatrixWorld();
    const taken: Box[] = [];
    for (const label of placed.current) {
      const { spec, node, width, height } = label;
      point.set(spec.at[0], spec.at[1], toWorldZ(spec.at[2]));
      toCanvasPixels(point, camera, size);
      const left = spec.align === "before" ? point.x - width - TICK_GAP_PX : point.x - width / 2;
      const top = spec.align === "below" ? point.y + TICK_GAP_PX : point.y - height / 2;
      const box = { left, top, right: left + width, bottom: top + height };
      const onStage =
        point.z < 1 &&
        box.left >= EDGE_PX &&
        box.top >= Math.max(EDGE_PX, insets.top) &&
        box.right <= size.width - EDGE_PX &&
        box.bottom <= size.height - EDGE_PX;
      const clear =
        onStage &&
        !chrome.current.some((other) => overlaps(other, box)) &&
        !taken.some((other) => overlaps(other, box));
      if (clear) taken.push(box);
      node.style.transform = `translate3d(${left.toFixed(1)}px, ${top.toFixed(1)}px, 0)`;
      if (clear !== label.visible) {
        label.visible = clear;
        node.style.opacity = clear ? "1" : "0";
      }
    }
  });

  return null;
}

const ARM_DIRECTION: Record<Arm, Point2> = { N: [0, 1], E: [1, 0], S: [0, -1], W: [-1, 0] };

/** Above the road, so a low view doesn't set the label on the vehicles at the far end of an arm. */
const ARM_LABEL_HEIGHT_M = 7;

/** At the far end of each arm, with its real lane counts. */
export function armLabels(site: SiteGeometry): LabelSpec[] {
  const seen = new Map<Arm, { in: number; out: number; u: number; v: number; reach: number }>();
  for (const lane of site.lanes) {
    const [du, dv] = ARM_DIRECTION[lane.arm];
    const entry = seen.get(lane.arm) ?? { in: 0, out: 0, u: 0, v: 0, reach: 0 };
    if (lane.direction === "approach") entry.in++;
    else entry.out++;
    for (const [u, v] of lane.polygon_m) {
      const along = u * du + v * dv;
      if (along > entry.reach) {
        entry.reach = along;
        entry.u = du !== 0 ? u : entry.u;
        entry.v = dv !== 0 ? v : entry.v;
      }
    }
    seen.set(lane.arm, entry);
  }
  return [...seen.entries()].map(([arm, entry]) => {
    const [du, dv] = ARM_DIRECTION[arm];
    const points = site.lanes.filter((lane) => lane.arm === arm).flatMap((lane) => lane.polygon_m);
    const across = points.reduce((sum, [u, v]) => sum + (du !== 0 ? v : u), 0) / points.length;
    const at: Point2 = du !== 0 ? [entry.u + du * 9, across] : [across, entry.v + dv * 9];
    return {
      id: `arm-${arm}`,
      at: [at[0], ARM_LABEL_HEIGHT_M, at[1]],
      lines: [{ text: `${arm} · ${entry.in} in ${entry.out} out`, tone: "text-paint-2 text-[11px] tracking-wide" }],
    };
  });
}

type Segment = readonly [Point2, Point2];

/**
 * Hairlines on the ground, one pixel wide at any distance. As flat strips they were under a pixel
 * in Plan, where they broke into dots and gaps, and in a low view they read as objects on the road.
 */
function GroundLines({ segments, colour }: { segments: readonly Segment[]; colour: Color }) {
  const geometry = useMemo(() => {
    const positions = segments.flatMap(([from, to]) => [
      from[0],
      LEVEL.paint,
      toWorldZ(from[1]),
      to[0],
      LEVEL.paint,
      toWorldZ(to[1]),
    ]);
    const lines = new BufferGeometry();
    lines.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
    return lines;
  }, [segments]);
  const material = useMemo(
    () => new LineBasicMaterial({ color: colour, transparent: true, depthWrite: false }),
    [colour],
  );
  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );
  return <lineSegments geometry={geometry} material={material} renderOrder={ORDER.paint} />;
}

const SCALE_BAR_M = 50;
const SCALE_STEP_M = 10;

function scaleBarStart(bounds: Bounds): Point2 {
  return [bounds.uMax + 10, bounds.vMin + 6];
}

export function scaleBarLabel(bounds: Bounds): LabelSpec {
  const [u, v] = scaleBarStart(bounds);
  return {
    id: "scale",
    at: [u + 4, 0.6, v + SCALE_BAR_M / 2],
    lines: [{ text: `${SCALE_BAR_M} m` }],
  };
}

/** A hairline on the ground in metres, so it stays true under any camera. */
export function ScaleBar({ bounds, palette }: { bounds: Bounds; palette: Palette }) {
  const segments = useMemo(() => {
    const [u, v] = scaleBarStart(bounds);
    const lines: Segment[] = [[[u, v], [u, v + SCALE_BAR_M]]];
    for (let at = 0; at <= SCALE_BAR_M; at += SCALE_STEP_M) {
      const reach = at === 0 || at === SCALE_BAR_M ? 1.6 : 0.8;
      lines.push([[u - reach, v + at], [u + reach, v + at]]);
    }
    return lines;
  }, [bounds]);
  return <GroundLines segments={segments} colour={palette.paintFaint} />;
}

const COMPASS_MARKUP = `
  <circle r="23" fill="none" stroke="var(--rule-2)" />
  <g data-needle="axis"><line y1="0" y2="-20" stroke="var(--paint-3)" /></g>
  <g data-needle="north">
    <line y1="0" y2="-13" stroke="var(--paint-1)" />
    <path d="M0 -21 L3.2 -12 L-3.2 -12 Z" fill="var(--paint-1)" />
  </g>
  <text data-tip="axis" text-anchor="middle" dominant-baseline="central" fill="var(--paint-3)"
    font-family="var(--font-mono)" font-size="7.5"></text>
  <text data-tip="north" text-anchor="middle" dominant-baseline="central" fill="var(--paint-1)"
    font-family="var(--font-mono)" font-size="7.5">N</text>
`;

const COMPASS_SIZE_PX = 84;
const COMPASS_GAP_PX = 8;
/** Distance of the needle captions from the centre, in the compass's own units. */
const COMPASS_TIP_RADIUS = 28;

/** Turns a needle and carries its caption round the rim, keeping the caption upright. */
function aim(needle: SVGGElement | null, tip: SVGTextElement | null, degrees: number): void {
  needle?.setAttribute("transform", `rotate(${degrees.toFixed(2)})`);
  const radians = (degrees * Math.PI) / 180;
  tip?.setAttribute("x", (Math.sin(radians) * COMPASS_TIP_RADIUS).toFixed(2));
  tip?.setAttribute("y", (-Math.cos(radians) * COMPASS_TIP_RADIUS).toFixed(2));
}

/**
 * Two needles fixed to the screen: the frame's +v axis, and grid north from the calibration
 * bearing. They differ by that bearing, which is why "up the road" must not be read as north.
 * Held on the glass so neither goes edge-on in a low view.
 */
export function Compass({ anchor, insets, hidden = false }: { anchor: GeoAnchor | null; insets: Insets; hidden?: boolean }) {
  const gl = useThree((state) => state.gl);
  const parts = useRef<{
    axis: SVGGElement | null;
    north: SVGGElement | null;
    axisTip: SVGTextElement | null;
    northTip: SVGTextElement | null;
  } | null>(null);
  const vector = useMemo(() => new Vector3(), []);
  const lastTurn = useRef(Number.NaN);
  const northDeg = anchor ? -anchor.bearingDeg : null;

  useLayoutEffect(() => {
    const host = overlayNode(gl, "div", "pointer-events-none absolute flex flex-col items-center gap-1");
    host.style.right = `${Math.max(insets.right, 16) + COMPASS_GAP_PX}px`;
    host.style.bottom = `${insets.bottom + COMPASS_GAP_PX}px`;
    if (hidden) host.style.visibility = "hidden";
    host.setAttribute(CHROME_ATTRIBUTE, "");
    host.setAttribute("role", "img");
    host.setAttribute(
      "aria-label",
      anchor ? "Compass: grid north and the direction of Lankershim" : "Compass: direction of Lankershim only, no north recorded",
    );
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "-32 -32 64 64");
    svg.setAttribute("width", String(COMPASS_SIZE_PX));
    svg.setAttribute("height", String(COMPASS_SIZE_PX));
    svg.innerHTML = COMPASS_MARKUP;
    host.appendChild(svg);
    const caption = document.createElement("span");
    caption.className = "mono bg-asphalt-0/70 px-1 text-paint-3";
    caption.textContent = anchor ? "Grid north" : "No north recorded";
    host.appendChild(caption);
    parts.current = {
      axis: svg.querySelector<SVGGElement>('[data-needle="axis"]'),
      north: svg.querySelector<SVGGElement>('[data-needle="north"]'),
      axisTip: svg.querySelector<SVGTextElement>('[data-tip="axis"]'),
      northTip: svg.querySelector<SVGTextElement>('[data-tip="north"]'),
    };
    if (northDeg === null) {
      parts.current.north?.remove();
      parts.current.northTip?.remove();
    }
    lastTurn.current = Number.NaN;
    return () => {
      host.remove();
      parts.current = null;
    };
  }, [gl, anchor, insets, northDeg, hidden]);

  useFrame(({ camera }) => {
    if (!parts.current) return;
    // The ground direction that points up the screen, from the camera's own up and forward.
    camera.getWorldDirection(vector);
    const e = camera.matrixWorld.elements;
    const groundU = vector.x + e[4];
    const groundV = -(vector.z + e[6]);
    const turnDeg = (Math.atan2(groundU, groundV) * 180) / Math.PI;
    if (Math.abs(turnDeg - lastTurn.current) < 0.05) return;
    lastTurn.current = turnDeg;
    aim(parts.current.axis, parts.current.axisTip, -turnDeg);
    if (northDeg !== null) aim(parts.current.north, parts.current.northTip, northDeg - turnDeg);
  });

  return null;
}

const TICK_STEP_M = 50;
/** Every second tick is labelled: three lines of coordinates need the room. */
const TICK_LABEL_EVERY_M = 100;

function geoEdges(bounds: Bounds): { edgeU: number; edgeV: number; top: number; right: number } {
  return {
    edgeU: Math.floor((bounds.uMin - 26) / 10) * 10,
    edgeV: Math.floor((bounds.vMin - 20) / 10) * 10,
    top: Math.ceil(bounds.vMax / TICK_STEP_M) * TICK_STEP_M,
    right: Math.ceil(bounds.uMax / TICK_STEP_M) * TICK_STEP_M,
  };
}

function tickLines(anchor: GeoAnchor, u: number, v: number, axis: string): LabelLine[] {
  const latLon = junctionToLatLon(anchor, u, v);
  return [
    { text: axis, tone: "text-paint-2" },
    { text: `${formatLat(latLon.latDeg)} ${formatLon(latLon.lonDeg)}` },
  ];
}

/** Each names one point of the junction frame in WGS84 and EPSG:2229. */
export function geoTickLabels(bounds: Bounds, anchor: GeoAnchor, network?: RoadNetwork): LabelSpec[] {
  const { edgeU, edgeV, top, right } = geoEdges(bounds);
  const specs: LabelSpec[] = [];
  const signed = (value: number) => `${value >= 0 ? "+" : ""}${value}`;
  for (let v = Math.ceil(edgeV / TICK_LABEL_EVERY_M) * TICK_LABEL_EVERY_M; v <= top; v += TICK_LABEL_EVERY_M) {
    specs.push({
      id: `v${v}`,
      at: [edgeU - 3, 0.2, v],
      align: "before",
      lines: tickLines(anchor, edgeU, v, `${signed(v)} m along Lankershim`),
    });
  }
  for (let u = Math.ceil(edgeU / TICK_LABEL_EVERY_M) * TICK_LABEL_EVERY_M; u <= right; u += TICK_LABEL_EVERY_M) {
    specs.push({
      id: `u${u}`,
      at: [u, 0.2, edgeV - 3],
      align: "below",
      lines: tickLines(anchor, u, edgeV, `${signed(u)} m across`),
    });
  }
  // A tick label on the street would sit on the traffic; the ticks either side carry the axis.
  return network ? specs.filter((spec) => !onStreet(network, spec.at[0], spec.at[2], 12)) : specs;
}

/** Geo mode's axis lines and ticks. */
export function GeoTicks({ bounds, network, palette }: { bounds: Bounds; network: RoadNetwork; palette: Palette }) {
  const segments = useMemo(() => {
    const { edgeU, edgeV, top, right } = geoEdges(bounds);
    const lines: Segment[] = [
      [[edgeU, edgeV], [edgeU, top]],
      [[edgeU, edgeV], [right, edgeV]],
    ];
    for (let v = Math.ceil(edgeV / TICK_STEP_M) * TICK_STEP_M; v <= top; v += TICK_STEP_M) {
      lines.push([[edgeU - 3, v], [edgeU, v]]);
    }
    for (let u = Math.ceil(edgeU / TICK_STEP_M) * TICK_STEP_M; u <= right; u += TICK_STEP_M) {
      lines.push([[u, edgeV - 3], [u, edgeV]]);
    }
    // The axes stop at the verge: nothing but the road and its paint lies on the road.
    return lines.flatMap(([from, to]) => clipToVerge(network, from, to));
  }, [bounds, network]);
  return <GroundLines segments={segments} colour={palette.paintFaint} />;
}

export { gridNorthInJunction };
