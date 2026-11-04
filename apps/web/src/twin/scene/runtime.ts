/**
 * Shared per-scene state. Layers read it inside useFrame; none of it goes through React. `stamp`
 * moves when the sample changes, so a paused scene rewrites nothing.
 */
import { Color } from "three";
import { createSample, type FrameSample, type TwinModel } from "../model";

export interface Runtime {
  model: TwinModel;
  mask: Uint8Array;
  sample: FrameSample;
  frame: number;
  stamp: number;
  dirty: boolean;
  /** Track indices, -1 for none. */
  selected: number;
  hovered: number;
  /** Footprints only: drops the assumed height and body shape. */
  flat: boolean;
  showVehicles: boolean;
  showTrails: boolean;
}

export function createRuntime(model: TwinModel): Runtime {
  return {
    model,
    mask: new Uint8Array(model.tracks.length).fill(1),
    sample: createSample(model.peak),
    frame: -1,
    stamp: 0,
    dirty: true,
    selected: -1,
    hovered: -1,
    flat: false,
    showVehicles: true,
    showTrails: true,
  };
}

/** Pixels of the stage covered by instruments, so the camera can frame what stays free. */
export interface Insets {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export const NO_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 };

/** Painter's order for the ground layers, which all lie within a few centimetres of y = 0. */
export const ORDER = { ground: -10, road: 1, heat: 2, shadow: 3, paint: 4, trail: 5, mark: 6 } as const;
export const LEVEL = { road: 0.02, shadow: 0.03, paint: 0.04, heat: 0.05, trail: 0.07, mark: 0.09 } as const;

function token(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** The product's tokens, read from CSS so the scene can't drift from the rest of the app. */
export function readPalette() {
  return {
    room: new Color(token("--asphalt-0", "#0a0c0e")),
    surface: new Color(token("--asphalt-2", "#161a1e")),
    road: new Color(token("--asphalt-3", "#1e2328")),
    rule: new Color(token("--rule-1", "#20262c")),
    ruleStrong: new Color(token("--rule-2", "#313a42")),
    paint: new Color(token("--paint-1", "#efeadf")),
    paintDim: new Color(token("--paint-2", "#b9b7af")),
    paintFaint: new Color(token("--paint-3", "#8b8d89")),
    amber: new Color(token("--amber", "#f5a623")),
  };
}

export type Palette = ReturnType<typeof readPalette>;

/** x = u, z = -v. The one place the scene's axes are stated in code. */
export function toWorldZ(v: number): number {
  return -v;
}
