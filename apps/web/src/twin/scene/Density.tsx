import { useFrame } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import {
  AdditiveBlending,
  BoxGeometry,
  BufferAttribute,
  Color,
  DataTexture,
  DynamicDrawUsage,
  InstancedBufferAttribute,
  type InstancedMesh,
  LinearFilter,
  Matrix4,
  MeshBasicMaterial,
  RedFormat,
  ShaderMaterial,
  Sphere,
  UnsignedByteType,
  Vector3,
  Vector4,
} from "three";
import {
  COLUMN_MAX_HEIGHT_M,
  COLUMN_MIN_LEVEL,
  accumulateDensity,
  blurCells,
  toHeatBytes,
  type DensityGrid,
} from "../density";
import { densitySummary } from "../controls";
import { LEVEL, ORDER, toWorldZ, type Palette, type Runtime } from "./runtime";

const VERTEX = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const FRAGMENT = /* glsl */ `
  varying vec3 vWorld;
  uniform sampler2D uMap;
  uniform vec4 uBox;
  uniform vec3 uLow;
  uniform vec3 uMid;
  uniform vec3 uHigh;
  uniform float uOpacity;

  void main() {
    vec2 uv = (vec2(vWorld.x, -vWorld.z) - uBox.xy) / uBox.zw;
    // Past the grid there are no cells: clamped edge texels would smear heat where no data is.
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
    float d = texture2D(uMap, uv).r;
    vec3 colour = mix(uLow, uMid, smoothstep(0.18, 0.72, d));
    colour = mix(colour, uHigh, smoothstep(0.78, 1.0, d));
    float alpha = smoothstep(0.06, 0.62, d) * uOpacity;
    gl_FragColor = vec4(colour, alpha);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

const HEAT_CELL_M = 1;
const HEAT_SIGMA_CELLS = 0.8;
export const COLUMN_CELL_M = 4;

/** Occupancy as a ground layer: one DataTexture, rewritten only when the filters change. */
export function HeatLayer({
  rt,
  palette,
  frames,
  revision,
  opacity,
  reports = true,
}: {
  rt: Runtime;
  palette: Palette;
  frames: readonly [number, number];
  revision: number;
  opacity: number;
  /** Publish this grid's numbers to the legend. False when another layer owns the legend. */
  reports?: boolean;
}) {
  const { bounds } = rt.model;
  const material = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: VERTEX,
        fragmentShader: FRAGMENT,
        transparent: true,
        depthWrite: false,
        uniforms: {
          uMap: { value: null },
          uBox: { value: new Vector4() },
          // Lane white, the colour of observed positions. Amber is kept for the selection alone.
          uLow: { value: palette.ruleStrong.clone() },
          uMid: { value: palette.paintFaint.clone() },
          uHigh: { value: palette.paint.clone().multiplyScalar(0.8) },
          uOpacity: { value: opacity },
        },
      }),
    // opacity is pushed to the uniform below; it must not rebuild the shader.
    [palette],
  );

  useEffect(() => {
    const grid = accumulateDensity(rt.model, rt.mask, bounds, frames, HEAT_CELL_M);
    const texture = new DataTexture(
      toHeatBytes(blurCells(grid, HEAT_SIGMA_CELLS)),
      grid.cols,
      grid.rows,
      RedFormat,
      UnsignedByteType,
    );
    texture.minFilter = LinearFilter;
    texture.magFilter = LinearFilter;
    texture.needsUpdate = true;
    material.uniforms.uMap.value = texture;
    material.uniforms.uBox.value.set(grid.uMin, grid.vMin, grid.cols * grid.cellM, grid.rows * grid.cellM);
    if (reports) {
      densitySummary.set({
        cellM: grid.cellM,
        peakVehicleSeconds: grid.peak,
        totalVehicleSeconds: grid.total,
      });
    }
    return () => {
      texture.dispose();
    };
  }, [rt, bounds, frames, revision, material, reports]);

  useEffect(() => {
    material.uniforms.uOpacity.value = opacity;
  }, [material, opacity]);

  useEffect(() => () => material.dispose(), [material]);

  const centreU = (bounds.uMin + bounds.uMax) / 2;
  const centreV = (bounds.vMin + bounds.vMax) / 2;
  return (
    <mesh
      position={[centreU, LEVEL.heat, toWorldZ(centreV)]}
      rotation-x={-Math.PI / 2}
      material={material}
      renderOrder={ORDER.heat}
    >
      <planeGeometry args={[bounds.uMax - bounds.uMin + 24, bounds.vMax - bounds.vMin + 24]} />
    </mesh>
  );
}

/** A unit column, darker at its foot, so depth reads without lighting. */
function columnGeometry(): BoxGeometry {
  const geometry = new BoxGeometry(1, 1, 1);
  geometry.translate(0, 0.5, 0);
  const position = geometry.getAttribute("position");
  const shades = new Float32Array(position.count * 3);
  for (let i = 0; i < position.count; i++) {
    const shade = 0.16 + position.getY(i) * 0.84;
    shades.set([shade, shade, shade], i * 3);
  }
  geometry.setAttribute("color", new BufferAttribute(shades, 3));
  return geometry;
}

const SITE_SPHERE = new Sphere(new Vector3(), 2000);

/**
 * Geo mode's density columns. Height is linear in vehicle-seconds per cell, so two columns can
 * be compared by eye, and the peak is on the legend.
 */
export function Columns({
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
  const { bounds } = rt.model;
  const capacity = useMemo(() => {
    const cols = Math.ceil((bounds.uMax - bounds.uMin) / COLUMN_CELL_M) + 3;
    const rows = Math.ceil((bounds.vMax - bounds.vMin) / COLUMN_CELL_M) + 3;
    return cols * rows;
  }, [bounds]);
  const geometry = useMemo(columnGeometry, []);
  // Light, not solid: additive and slim, so a column reads as a measure standing on the lane, never
  // as a building on the road. Height and tone still carry the value.
  const material = useMemo(
    () =>
      new MeshBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.78,
        blending: AdditiveBlending,
        depthWrite: false,
      }),
    [],
  );
  const mesh = useRef<InstancedMesh>(null);
  const target = useRef(new Float32Array(capacity));
  const shown = useRef(new Float32Array(capacity));
  // Per cell, not per instance: instances are compacted each frame, so their slots keep moving.
  const cellColours = useRef(new Float32Array(capacity * 3));
  const grid = useRef<DensityGrid | null>(null);
  const settled = useRef(false);

  useLayoutEffect(() => {
    const node = mesh.current;
    if (!node) return;
    node.instanceMatrix.setUsage(DynamicDrawUsage);
    node.instanceColor = new InstancedBufferAttribute(new Float32Array(capacity * 3), 3);
    node.frustumCulled = false;
    node.boundingSphere = SITE_SPHERE;
  }, [capacity]);

  useEffect(() => {
    const next = accumulateDensity(rt.model, rt.mask, bounds, frames, COLUMN_CELL_M);
    grid.current = next;
    target.current.fill(0);
    if (next.peak > 0) {
      for (let i = 0; i < next.cells.length && i < capacity; i++) {
        const level = next.cells[i] / next.peak;
        target.current[i] = level < COLUMN_MIN_LEVEL ? 0 : level * COLUMN_MAX_HEIGHT_M;
      }
    }
    settled.current = false;
    const colour = new Color();
    for (let i = 0; i < next.cells.length && i < capacity; i++) {
      const level = next.peak > 0 ? next.cells[i] / next.peak : 0;
      colour.copy(palette.ruleStrong).lerp(palette.paintFaint, Math.min(1, Math.sqrt(level) * 1.6));
      if (level > 0.4) colour.lerp(palette.paint, (level - 0.4) * 1.5);
      cellColours.current.set([colour.r, colour.g, colour.b], i * 3);
    }
    densitySummary.set({
      cellM: next.cellM,
      peakVehicleSeconds: next.peak,
      totalVehicleSeconds: next.total,
    });
  }, [rt, bounds, frames, revision, capacity, palette]);

  const matrix = useMemo(() => new Matrix4(), []);
  useFrame((_, delta) => {
    const node = mesh.current;
    const cells = grid.current;
    if (!node || !cells || settled.current) return;
    const ease = 1 - Math.exp(-delta * 7);
    const side = COLUMN_CELL_M * 0.42;
    const colours = node.instanceColor?.array as Float32Array;
    let moving = false;
    let slot = 0;
    for (let i = 0; i < cells.cells.length && i < capacity; i++) {
      const gap = target.current[i] - shown.current[i];
      shown.current[i] += gap * ease;
      if (Math.abs(gap) > 0.02) moving = true;
      else shown.current[i] = target.current[i];
      if (shown.current[i] < 0.03) continue;
      const u = cells.uMin + ((i % cells.cols) + 0.5) * cells.cellM;
      const v = cells.vMin + (Math.floor(i / cells.cols) + 0.5) * cells.cellM;
      matrix.makeScale(side, shown.current[i], side);
      matrix.setPosition(u, 0, toWorldZ(v));
      node.setMatrixAt(slot, matrix);
      colours[slot * 3] = cellColours.current[i * 3];
      colours[slot * 3 + 1] = cellColours.current[i * 3 + 1];
      colours[slot * 3 + 2] = cellColours.current[i * 3 + 2];
      slot++;
    }
    node.count = slot;
    node.instanceMatrix.needsUpdate = true;
    if (node.instanceColor) node.instanceColor.needsUpdate = true;
    settled.current = !moving;
  });

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  return <instancedMesh ref={mesh} args={[geometry, material, capacity]} />;
}
