import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  DoubleSide,
  DynamicDrawUsage,
  type Group,
  InstancedBufferAttribute,
  type InstancedMesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Sphere,
  Vector3,
} from "three";
import { NO_SPEED } from "../../lib/analysis";
import { buildForms } from "../forms";
import { FORM_HEIGHT_M, FORMS } from "../model";
import { overlayNode, toCanvasPixels } from "./overlay";
import { LEVEL, ORDER, toWorldZ, type Palette, type Runtime } from "./runtime";

/** Lamps emit without a light on them. Headlamps just cross the bloom threshold: a soft glow, no more. */
function vehicleMaterial(): MeshStandardMaterial {
  const material = new MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.42,
    metalness: 0.3,
    envMapIntensity: 0.55,
  });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader =
      "attribute float aGlow;\nvarying float vGlow;\n" +
      shader.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\nvGlow = aGlow;");
    shader.fragmentShader =
      "varying float vGlow;\n" +
      shader.fragmentShader.replace(
        "#include <emissivemap_fragment>",
        `#include <emissivemap_fragment>
         totalEmissiveRadiance += vGlow > 0.0
           ? vec3(0.92, 0.86, 0.72) * vGlow * 1.65
           : vec3(0.62, 0.30, 0.06) * (-vGlow) * 1.4;`,
      );
  };
  return material;
}

/** Small per-vehicle variation, so a queue reads as separate vehicles. Colour isn't measured. */
function tone(trackIndex: number): number {
  const hash = Math.sin(trackIndex * 12.9898) * 43758.5453;
  return 0.8 + (hash - Math.floor(hash)) * 0.28;
}

const FLAT_HEIGHT_M = 0.08;
/** Lifts a footprint clear of the heat layer, which would otherwise tint its underside. */
const FLAT_LIFT_M = 0.06;
const SITE_SPHERE = new Sphere(new Vector3(), 2000);

export function Vehicles({ rt, palette, shadows }: { rt: Runtime; palette: Palette; shadows: boolean }) {
  const forms = useMemo(buildForms, []);
  const material = useMemo(vehicleMaterial, []);
  const meshes = useRef<(InstancedMesh | null)[]>([]);
  const drawnStamp = useRef(-1);
  const capacity = rt.model.peak;
  const counts = useMemo(() => new Int32Array(FORMS.length), []);
  const hot = useMemo(() => palette.amber.clone().multiplyScalar(1.5), [palette]);
  const bright = useMemo(() => palette.paint.clone().multiplyScalar(1.05), [palette]);
  const base = useMemo(() => palette.paintDim.clone().multiplyScalar(0.92), [palette]);
  const shade = useMemo(() => new Color(), []);

  useLayoutEffect(() => {
    for (const mesh of meshes.current) {
      if (!mesh) continue;
      mesh.instanceMatrix.setUsage(DynamicDrawUsage);
      mesh.instanceColor = new InstancedBufferAttribute(new Float32Array(capacity * 3), 3);
      mesh.instanceColor.setUsage(DynamicDrawUsage);
      mesh.frustumCulled = false;
      // Instances move every frame. A fixed sphere keeps three from caching a stale one.
      mesh.boundingSphere = SITE_SPHERE;
      mesh.count = 0;
    }
    drawnStamp.current = -1;
  }, [capacity]);

  useEffect(
    () => () => {
      for (const form of forms) form.dispose();
      material.dispose();
    },
    [forms, material],
  );

  useFrame(() => {
    if (drawnStamp.current === rt.stamp) return;
    drawnStamp.current = rt.stamp;
    const { model, sample } = rt;
    counts.fill(0);
    const focused = rt.selected >= 0;
    for (let i = 0; rt.showVehicles && i < sample.count; i++) {
      const index = sample.track[i];
      const form = model.form[index];
      const mesh = meshes.current[form];
      if (!mesh) continue;
      const slot = counts[form]++;
      const cos = Math.cos(sample.heading[i]);
      const sin = Math.sin(sample.heading[i]);
      const grown = sample.appear[i];
      const width = model.widthM[index] * grown;
      const height = (rt.flat ? FLAT_HEIGHT_M : FORM_HEIGHT_M[form]) * grown;
      const length = model.lengthM[index] * grown;
      // Rotation about y by -heading: the nose at -z turns to (sin h, -cos h) = heading in x, z.
      const m = mesh.instanceMatrix.array as Float32Array;
      const o = slot * 16;
      m[o] = cos * width;
      m[o + 1] = 0;
      m[o + 2] = sin * width;
      m[o + 3] = 0;
      m[o + 4] = 0;
      m[o + 5] = height;
      m[o + 6] = 0;
      m[o + 7] = 0;
      m[o + 8] = -sin * length;
      m[o + 9] = 0;
      m[o + 10] = cos * length;
      m[o + 11] = 0;
      m[o + 12] = sample.bodyU[i];
      m[o + 13] = rt.flat ? FLAT_LIFT_M : 0;
      m[o + 14] = toWorldZ(sample.bodyV[i]);
      m[o + 15] = 1;

      if (index === rt.selected) shade.copy(hot);
      else if (index === rt.hovered) shade.copy(bright);
      else shade.copy(base).multiplyScalar(tone(index) * (focused ? 0.55 : 1));
      const c = mesh.instanceColor?.array as Float32Array;
      c[slot * 3] = shade.r;
      c[slot * 3 + 1] = shade.g;
      c[slot * 3 + 2] = shade.b;
    }
    for (let form = 0; form < FORMS.length; form++) {
      const mesh = meshes.current[form];
      if (!mesh) continue;
      mesh.count = counts[form];
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  });

  return (
    <>
      {FORMS.map((form, index) => (
        <instancedMesh
          key={form}
          ref={(node) => {
            meshes.current[index] = node;
          }}
          args={[forms[index], material, capacity]}
          castShadow={shadows}
        />
      ))}
    </>
  );
}

/** Four corner brackets on a unit square: a footprint, drawn like a registration mark. */
function bracketGeometry(): BufferGeometry {
  const positions: number[] = [];
  const arm = 0.26;
  const thick = 0.045;
  const quad = (u0: number, v0: number, u1: number, v1: number) => {
    positions.push(u0, 0, v0, u1, 0, v0, u1, 0, v1, u0, 0, v0, u1, 0, v1, u0, 0, v1);
  };
  for (const su of [-1, 1]) {
    for (const sv of [-1, 1]) {
      quad(su * 0.5, sv * 0.5, su * (0.5 - arm), sv * (0.5 - thick));
      quad(su * 0.5, sv * 0.5, su * (0.5 - thick), sv * (0.5 - arm));
    }
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(new Float32Array(positions), 3));
  return geometry;
}

const BRACKET_MARGIN_M = 1.5;

/** Selection and hover marks. The selected one is above 1.0 in linear light: that alone blooms. */
export function Marks({ rt, palette, labels }: { rt: Runtime; palette: Palette; labels: boolean }) {
  const geometry = useMemo(bracketGeometry, []);
  const selectedMaterial = useMemo(
    () =>
      new MeshBasicMaterial({
        color: palette.amber.clone().multiplyScalar(3.2),
        transparent: true,
        depthWrite: false,
        depthTest: false,
        side: DoubleSide,
        toneMapped: false,
      }),
    [palette],
  );
  const hoverMaterial = useMemo(
    () =>
      new MeshBasicMaterial({
        color: palette.paint,
        transparent: true,
        depthWrite: false,
        depthTest: false,
        side: DoubleSide,
      }),
    [palette],
  );
  const gl = useThree((state) => state.gl);
  const selected = useRef<Group>(null);
  const hovered = useRef<Group>(null);
  const tag = useRef<HTMLSpanElement | null>(null);
  const tagPoint = useMemo(() => new Vector3(), []);
  const lastTag = useRef({ at: 0, track: -1 });

  useLayoutEffect(() => {
    if (!labels) return;
    const node = overlayNode(
      gl,
      "span",
      "mono pointer-events-none absolute top-0 left-0 hidden border border-amber bg-asphalt-1 px-1.5 py-0.5 whitespace-nowrap text-amber",
    );
    tag.current = node;
    return () => {
      node.remove();
      tag.current = null;
    };
  }, [gl, labels]);

  useEffect(
    () => () => {
      geometry.dispose();
      selectedMaterial.dispose();
      hoverMaterial.dispose();
    },
    [geometry, selectedMaterial, hoverMaterial],
  );

  useFrame(({ clock: threeClock, camera, size }) => {
    const { sample, model } = rt;
    const place = (group: Group | null, index: number): number => {
      if (!group) return -1;
      if (index < 0) {
        group.visible = false;
        return -1;
      }
      for (let i = 0; i < sample.count; i++) {
        if (sample.track[i] !== index) continue;
        group.visible = true;
        group.position.set(sample.bodyU[i], LEVEL.mark, toWorldZ(sample.bodyV[i]));
        group.rotation.y = -sample.heading[i];
        group.scale.set(model.widthM[index] + BRACKET_MARGIN_M, 1, model.lengthM[index] + BRACKET_MARGIN_M);
        return i;
      }
      group.visible = false;
      return -1;
    };
    // Hidden evidence draws nothing, marks included. Filtered-out tracks aren't in the sample at all.
    const at = place(selected.current, rt.showVehicles ? rt.selected : -1);
    const hover = rt.hovered === rt.selected ? -1 : rt.hovered;
    place(hovered.current, rt.showVehicles ? hover : -1);

    const label = tag.current;
    if (!label) return;
    if (at < 0) {
      label.style.display = "none";
      return;
    }
    label.style.display = "block";
    camera.updateMatrixWorld();
    tagPoint.set(sample.bodyU[at], FORM_HEIGHT_M[model.form[rt.selected]] + 1.2, toWorldZ(sample.bodyV[at]));
    toCanvasPixels(tagPoint, camera, size);
    label.style.transform = `translate3d(${tagPoint.x.toFixed(1)}px, ${tagPoint.y.toFixed(1)}px, 0) translate(-50%, -100%)`;
    const now = threeClock.elapsedTime;
    if (now - lastTag.current.at > 0.2 || lastTag.current.track !== rt.selected) {
      lastTag.current = { at: now, track: rt.selected };
      const track = model.tracks[rt.selected];
      const speed = model.trajectories.speedCmS[sample.row[at]];
      const reading = speed === NO_SPEED ? "speed absent" : `${(speed / 100).toFixed(1)} m/s`;
      label.textContent = `#${track.track_id} ${track.vehicle_class} ${reading}`;
    }
  });

  return (
    <>
      <group ref={selected} visible={false}>
        <mesh geometry={geometry} material={selectedMaterial} renderOrder={ORDER.mark} />
      </group>
      <group ref={hovered} visible={false}>
        <mesh geometry={geometry} material={hoverMaterial} renderOrder={ORDER.mark} />
      </group>
    </>
  );
}
