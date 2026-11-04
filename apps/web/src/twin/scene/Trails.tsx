import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  DynamicDrawUsage,
  MeshBasicMaterial,
} from "three";
import { NO_GROUND } from "../../lib/analysis";
import type { TwinModel } from "../model";
import { LEVEL, ORDER, toWorldZ, type Palette, type Runtime } from "./runtime";

const TRAIL_SECONDS = 6;
const TRAIL_POINTS = 26;
const FULL_PATH_POINTS = 420;
const VERTICES_PER_SEGMENT = 6;

/** Writes ribbon quads flat on the ground into one buffer pair. */
class RibbonWriter {
  count = 0;

  constructor(
    private positions: Float32Array,
    private colours: Float32Array,
    private y: number,
  ) {}

  get capacity(): number {
    return this.positions.length / 3;
  }

  reset(): void {
    this.count = 0;
  }

  segment(
    u0: number,
    v0: number,
    u1: number,
    v1: number,
    halfWidth: number,
    r: number,
    g: number,
    b: number,
    a0: number,
    a1: number,
  ): void {
    if (this.count + VERTICES_PER_SEGMENT > this.capacity) return;
    const du = u1 - u0;
    const dv = v1 - v0;
    const length = Math.hypot(du, dv);
    if (length < 1e-4) return;
    const su = (-dv / length) * halfWidth;
    const sv = (du / length) * halfWidth;
    const y = this.y;
    let p = this.count * 3;
    let c = this.count * 4;
    const put = (u: number, v: number, a: number) => {
      this.positions[p++] = u;
      this.positions[p++] = y;
      this.positions[p++] = toWorldZ(v);
      this.colours[c++] = r;
      this.colours[c++] = g;
      this.colours[c++] = b;
      this.colours[c++] = a;
    };
    put(u0 + su, v0 + sv, a0);
    put(u1 + su, v1 + sv, a1);
    put(u1 - su, v1 - sv, a1);
    put(u0 + su, v0 + sv, a0);
    put(u1 - su, v1 - sv, a1);
    put(u0 - su, v0 - sv, a0);
    this.count += VERTICES_PER_SEGMENT;
  }
}

function ribbonGeometry(vertices: number): {
  geometry: BufferGeometry;
  positions: Float32Array;
  colours: Float32Array;
} {
  const positions = new Float32Array(vertices * 3);
  const colours = new Float32Array(vertices * 4);
  const geometry = new BufferGeometry();
  const position = new BufferAttribute(positions, 3);
  const colour = new BufferAttribute(colours, 4);
  position.setUsage(DynamicDrawUsage);
  colour.setUsage(DynamicDrawUsage);
  geometry.setAttribute("position", position);
  geometry.setAttribute("color", colour);
  return { geometry, positions, colours };
}

/**
 * Every trail and the selected vehicle's whole path, in one dynamic buffer and one draw call.
 * Recent history fades out behind each vehicle; the selected path is bright where it has been and
 * dim where it is going.
 */
export function Trails({ rt, palette }: { rt: Runtime; palette: Palette }) {
  const capacity = (rt.model.peak * TRAIL_POINTS + FULL_PATH_POINTS) * VERTICES_PER_SEGMENT;
  const buffers = useMemo(() => ribbonGeometry(capacity), [capacity]);
  const writer = useMemo(
    () => new RibbonWriter(buffers.positions, buffers.colours, LEVEL.trail),
    [buffers],
  );
  const material = useMemo(
    () =>
      new MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        depthWrite: false,
        side: DoubleSide,
        toneMapped: false,
      }),
    [],
  );
  const drawnStamp = useRef(-1);

  useEffect(
    () => () => {
      buffers.geometry.dispose();
      material.dispose();
    },
    [buffers, material],
  );

  useFrame(() => {
    if (drawnStamp.current === rt.stamp) return;
    drawnStamp.current = rt.stamp;
    const { model, sample } = rt;
    const { gx, gy } = model.trajectories;
    const scale = model.groundScale;
    const step = Math.max(1, Math.round((TRAIL_SECONDS * model.fps) / TRAIL_POINTS));
    const { r, g, b } = palette.paintDim;
    writer.reset();

    for (let i = 0; rt.showTrails && i < sample.count; i++) {
      const index = sample.track[i];
      if (index === rt.selected) continue;
      const first = model.tracks[index].first_row;
      const dimmed = rt.selected >= 0 ? 0.45 : 1;
      let u = sample.u[i];
      let v = sample.v[i];
      let row = sample.row[i];
      for (let k = 0; k < TRAIL_POINTS && row >= first; k++) {
        if (gx[row] === NO_GROUND) break;
        const pu = gx[row] * scale;
        const pv = gy[row] * scale;
        const near = 1 - k / TRAIL_POINTS;
        const far = 1 - (k + 1) / TRAIL_POINTS;
        writer.segment(u, v, pu, pv, 0.09, r, g, b, near * near * 0.45 * dimmed, far * far * 0.45 * dimmed);
        u = pu;
        v = pv;
        row -= step;
      }
    }

    if (rt.selected >= 0 && rt.showVehicles) writeFullPath(writer, model, rt, palette);

    const position = buffers.geometry.getAttribute("position") as BufferAttribute;
    const colour = buffers.geometry.getAttribute("color") as BufferAttribute;
    position.clearUpdateRanges();
    colour.clearUpdateRanges();
    position.addUpdateRange(0, writer.count * 3);
    colour.addUpdateRange(0, writer.count * 4);
    position.needsUpdate = true;
    colour.needsUpdate = true;
    buffers.geometry.setDrawRange(0, writer.count);
  });

  return (
    <mesh
      geometry={buffers.geometry}
      material={material}
      frustumCulled={false}
      renderOrder={ORDER.trail}
    />
  );
}

function writeFullPath(writer: RibbonWriter, model: TwinModel, rt: Runtime, palette: Palette): void {
  const track = model.tracks[rt.selected];
  const { gx, gy } = model.trajectories;
  const scale = model.groundScale;
  const step = Math.max(1, Math.ceil(track.n / FULL_PATH_POINTS));
  const frame = rt.frame;
  const { r, g, b } = palette.amber;
  // A selected track the filters have dropped stays as a dim reference, and the inspector says why.
  const kept = rt.mask[rt.selected] === 1 ? 1 : 0.32;
  let u = Number.NaN;
  let v = Number.NaN;
  for (let i = 0; i < track.n; i += step) {
    const row = track.first_row + i;
    if (gx[row] === NO_GROUND) continue;
    const nu = gx[row] * scale;
    const nv = gy[row] * scale;
    if (!Number.isNaN(u)) {
      const travelled = track.start_frame + i <= frame;
      const strength = (travelled ? 1.5 : 0.55) * kept;
      writer.segment(
        u,
        v,
        nu,
        nv,
        travelled ? 0.26 : 0.16,
        r * strength,
        g * strength,
        b * strength,
        (travelled ? 0.95 : 0.5) * kept,
        (travelled ? 0.95 : 0.5) * kept,
      );
    }
    u = nu;
    v = nv;
  }
}

/** Decimated so a full 15 minutes stays a sensible size. */
const RIBBON_VERTEX_BUDGET = 420_000;
const RIBBON_MIN_SPACING_M = 2.5;

/**
 * Geo mode's full-window ribbons: every kept track's path over the chosen time, on the ground.
 * Static between filter changes. Overlaps build towards the ribbon colour and stop there, so heavy
 * movements read as brighter bands without climbing past the bloom threshold, which is the selection's.
 */
export function Ribbons({
  rt,
  palette,
  frames,
  revision,
}: {
  rt: Runtime;
  palette: Palette;
  frames: readonly [number, number];
  revision: number;
}) {
  const buffers = useMemo(() => ribbonGeometry(RIBBON_VERTEX_BUDGET), []);
  const material = useMemo(
    () =>
      new MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        depthWrite: false,
        side: DoubleSide,
      }),
    [],
  );

  useEffect(() => {
    const { model, mask } = rt;
    const { gx, gy } = model.trajectories;
    const scale = model.groundScale;
    const writer = new RibbonWriter(buffers.positions, buffers.colours, LEVEL.trail);
    const { r, g, b } = palette.paintDim;
    const kept = mask.reduce((total, on) => total + on, 0);
    const alpha = Math.min(0.5, Math.max(0.1, 26 / Math.sqrt(Math.max(kept, 1)) / 8));
    const spacing = Math.max(RIBBON_MIN_SPACING_M, (kept * 200 * VERTICES_PER_SEGMENT) / RIBBON_VERTEX_BUDGET);
    model.tracks.forEach((track, index) => {
      if (!mask[index]) return;
      const first = Math.max(0, Math.ceil(frames[0]) - track.start_frame);
      const last = Math.min(track.n - 1, Math.floor(frames[1]) - track.start_frame);
      let u = Number.NaN;
      let v = Number.NaN;
      for (let i = first; i <= last; i++) {
        const row = track.first_row + i;
        if (gx[row] === NO_GROUND) continue;
        const nu = gx[row] * scale;
        const nv = gy[row] * scale;
        if (Number.isNaN(u)) {
          u = nu;
          v = nv;
        } else if (Math.hypot(nu - u, nv - v) >= spacing || i === last) {
          writer.segment(u, v, nu, nv, 0.11, r, g, b, alpha, alpha);
          u = nu;
          v = nv;
        }
      }
    });
    const position = buffers.geometry.getAttribute("position") as BufferAttribute;
    const colour = buffers.geometry.getAttribute("color") as BufferAttribute;
    position.clearUpdateRanges();
    colour.clearUpdateRanges();
    position.addUpdateRange(0, writer.count * 3);
    colour.addUpdateRange(0, writer.count * 4);
    position.needsUpdate = true;
    colour.needsUpdate = true;
    buffers.geometry.setDrawRange(0, writer.count);
  }, [rt, palette, frames, revision, buffers]);

  useEffect(
    () => () => {
      buffers.geometry.dispose();
      material.dispose();
    },
    [buffers, material],
  );

  return (
    <mesh
      geometry={buffers.geometry}
      material={material}
      frustumCulled={false}
      renderOrder={ORDER.trail}
    />
  );
}
