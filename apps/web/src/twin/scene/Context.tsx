/**
 * Context massing: dark extruded blocks on the corner blocks, with faint window lines and a lit
 * edge. Assumed, not surveyed: the legend says so, and the blocks go with the assumed layers.
 * They stand only where `contextBlocks` found clear ground, so they never touch the road.
 */
import { useEffect, useMemo } from "react";
import { BufferAttribute, BufferGeometry, Color, LineBasicMaterial, MeshStandardMaterial } from "three";
import { contextBlocks, type ContextBlock, type Rect } from "../context";
import type { TwinModel } from "../model";
import { fadeFrame, toWorldZ, type Palette } from "./runtime";

/** Blocks stay well inside the floor's fade, so none stands out in the dark past the ground. */
const FLOOR_REACH = 0.8;
const FLOOR_M = 3;
const BAY_M = 4;
const SILL_M = 0.9;
const LIFT_M = 0.03;

function walls(block: ContextBlock): [number, number, number, number][] {
  const { uMin, uMax, vMin, vMax } = block;
  // Each wall as a run along the ground, wound so its outward normal faces away from the block.
  return [
    [uMin, vMin, uMax, vMin],
    [uMax, vMin, uMax, vMax],
    [uMax, vMax, uMin, vMax],
    [uMin, vMax, uMin, vMin],
  ];
}

function massing(blocks: readonly ContextBlock[]) {
  const position: number[] = [];
  const normal: number[] = [];
  const push = (x: number, y: number, z: number, n: [number, number, number]) => {
    position.push(x, y, z);
    normal.push(...n);
  };
  const edges: number[] = [];
  const windows: number[] = [];
  for (const block of blocks) {
    const h = block.height;
    for (const [u0, v0, u1, v1] of walls(block)) {
      const du = u1 - u0;
      const dv = v1 - v0;
      const length = Math.hypot(du, dv);
      // Outward normal in u-v, turned into world x-z (z = -v).
      const nu = dv / length;
      const nv = -du / length;
      const n: [number, number, number] = [nu, 0, -nv];
      const a: [number, number, number] = [u0, 0, toWorldZ(v0)];
      const b: [number, number, number] = [u1, 0, toWorldZ(v1)];
      const c: [number, number, number] = [u1, h, toWorldZ(v1)];
      const d: [number, number, number] = [u0, h, toWorldZ(v0)];
      for (const p of [a, b, c, a, c, d]) push(p[0], p[1], p[2], n);
      // Silhouette: the vertical edge at the start of the wall and the roof line along it.
      edges.push(u0, 0, toWorldZ(v0), u0, h, toWorldZ(v0), u0, h, toWorldZ(v0), u1, h, toWorldZ(v1));
      // Window grid, lifted off the wall so it never fights the face for depth.
      const ou = nu * LIFT_M;
      const ov = nv * LIFT_M;
      const top = h - 0.6;
      for (let y = SILL_M; y < top; y += FLOOR_M) {
        windows.push(u0 + ou, y, toWorldZ(v0 + ov), u1 + ou, y, toWorldZ(v1 + ov));
      }
      const bays = Math.floor(length / BAY_M);
      for (let k = 1; k < bays; k++) {
        const t = k / bays;
        const u = u0 + du * t + ou;
        const v = v0 + dv * t + ov;
        windows.push(u, SILL_M, toWorldZ(v), u, Math.max(SILL_M, top), toWorldZ(v));
      }
    }
    // Roof.
    const { uMin, uMax, vMin, vMax } = block;
    const up: [number, number, number] = [0, 1, 0];
    const r1: [number, number, number] = [uMin, h, toWorldZ(vMin)];
    const r2: [number, number, number] = [uMax, h, toWorldZ(vMin)];
    const r3: [number, number, number] = [uMax, h, toWorldZ(vMax)];
    const r4: [number, number, number] = [uMin, h, toWorldZ(vMax)];
    for (const p of [r1, r2, r3, r1, r3, r4]) push(p[0], p[1], p[2], up);
  }
  const faces = new BufferGeometry();
  faces.setAttribute("position", new BufferAttribute(new Float32Array(position), 3));
  faces.setAttribute("normal", new BufferAttribute(new Float32Array(normal), 3));
  const edgeLines = new BufferGeometry();
  edgeLines.setAttribute("position", new BufferAttribute(new Float32Array(edges), 3));
  const windowLines = new BufferGeometry();
  windowLines.setAttribute("position", new BufferAttribute(new Float32Array(windows), 3));
  return { faces, edgeLines, windowLines };
}

export function ContextBlocks({ model, palette, avoid }: { model: TwinModel; palette: Palette; avoid: readonly Rect[] }) {
  const blocks = useMemo(() => {
    const { centre, radius } = fadeFrame(model.bounds);
    const inside = (u: number, v: number) =>
      Math.hypot((u - centre[0]) / radius[0], (v - centre[1]) / radius[1]) <= FLOOR_REACH;
    return contextBlocks(model.network, model.coverage, inside, avoid);
  }, [model, avoid]);
  const geometry = useMemo(() => massing(blocks), [blocks]);
  const materials = useMemo(() => {
    const body = new Color(palette.surface).lerp(palette.room, 0.3);
    return {
      body: new MeshStandardMaterial({
        color: body,
        roughness: 0.92,
        metalness: 0,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
      }),
      edge: new LineBasicMaterial({ color: palette.ruleStrong, transparent: true, opacity: 0.9 }),
      window: new LineBasicMaterial({ color: palette.paintFaint, transparent: true, opacity: 0.1, depthWrite: false }),
    };
  }, [palette]);
  useEffect(
    () => () => {
      geometry.faces.dispose();
      geometry.edgeLines.dispose();
      geometry.windowLines.dispose();
    },
    [geometry],
  );
  useEffect(
    () => () => {
      materials.body.dispose();
      materials.edge.dispose();
      materials.window.dispose();
    },
    [materials],
  );
  if (blocks.length === 0) return null;
  return (
    <group name="context-blocks">
      <mesh geometry={geometry.faces} material={materials.body} raycast={() => null} />
      <lineSegments geometry={geometry.edgeLines} material={materials.edge} raycast={() => null} />
      <lineSegments geometry={geometry.windowLines} material={materials.window} raycast={() => null} />
    </group>
  );
}
