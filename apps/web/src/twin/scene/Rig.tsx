import { CameraControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { PerspectiveCamera, Plane, Raycaster, Vector2, Vector3 } from "three";
import { display, type TwinMode, type TwinView } from "../controls";
import { hoverTrack, selectTrack } from "../selection";
import { lerpAngle } from "../model";
import type { Point2 } from "../site";
import { poseFor } from "./framing";
import { toWorldZ, type Insets, type Runtime } from "./runtime";

const DEG = Math.PI / 180;

/**
 * Centres the camera on the part of the stage instruments don't cover. Picking and labels read the
 * same projection, so nothing else has to know. Canvas resize keeps the offset by re-running here.
 */
export function ViewFrame({
  insets,
  panelPx = 0,
  reducedMotion = false,
}: {
  insets: Insets;
  /** A side panel opened over the right of the stage. The scene slides clear of it; the camera stays put. */
  panelPx?: number;
  reducedMotion?: boolean;
}) {
  const camera = useThree((state) => state.camera);
  const size = useThree((state) => state.size);
  const slide = useRef(panelPx);
  const place = useCallback(() => {
    if (!(camera instanceof PerspectiveCamera)) return;
    camera.setViewOffset(
      size.width,
      size.height,
      (insets.right + slide.current - insets.left) / 2,
      (insets.bottom - insets.top) / 2,
      size.width,
      size.height,
    );
  }, [camera, size, insets]);
  useLayoutEffect(() => {
    place();
    return () => camera.clearViewOffset();
  }, [camera, place]);
  useFrame((_, delta) => {
    const gap = panelPx - slide.current;
    if (gap === 0) return;
    slide.current = reducedMotion || Math.abs(gap) < 1 ? panelPx : slide.current + gap * Math.min(1, delta * PANEL_SLIDE_RATE);
    place();
  });
  return null;
}

/** How fast the scene slides clear of a side panel, per second. */
const PANEL_SLIDE_RATE = 7;

/** Lowest the eye goes, so the ground never turns edge-on. */
const MAX_POLAR = 84 * DEG;
/** Below the library's default of 1, which swings a quarter turn on a short drag. */
const ROTATE_SPEED = 0.6;

const FOLLOW_DISTANCE_M = 30;
const FOLLOW_POLAR = 68 * DEG;
/** Rad/s the camera swings round behind a turning vehicle. */
const FOLLOW_TURN_RATE = 2.4;
/** Seconds to ease onto the vehicle, after which the camera is locked to it at any playback rate. */
const FOLLOW_SETTLE_S = 1.4;
const FOLLOW_EASE_S = 0.32;
const FOLLOW_LOCK_S = 0.012;
/** How long the camera stays on an ended track before going back to the wide view. */
const FOLLOW_HOLD_S = 1.2;
const CLICK_SLOP_PX = 5;

/** Damped orbit and zoom, named views, and the chase for Follow. */
export function Rig({
  rt,
  mode,
  view,
  reducedMotion,
  insets,
  north,
}: {
  rt: Runtime;
  mode: TwinMode;
  view: TwinView;
  reducedMotion: boolean;
  insets: Insets;
  north: Point2 | null;
}) {
  const controls = useRef<CameraControls>(null);
  const size = useThree((state) => state.size);
  const placed = useRef(false);
  const chase = useRef({ active: false, since: 0, lostAt: -1, target: new Vector3() });

  useEffect(() => {
    const rig = controls.current;
    if (!rig) return;
    rig.smoothTime = reducedMotion ? 0 : 0.7;
    rig.draggingSmoothTime = reducedMotion ? 0 : 0.14;
    rig.maxPolarAngle = MAX_POLAR;
    rig.azimuthRotateSpeed = ROTATE_SPEED;
    rig.polarRotateSpeed = ROTATE_SPEED;
    rig.minDistance = 8;
    rig.maxDistance = 900;
    rig.dollyToCursor = true;
  }, [reducedMotion]);

  useEffect(() => {
    const rig = controls.current;
    if (!rig || view === "follow") return;
    const pose = poseFor(view, mode, rt.model, { ...size, insets }, north);
    // Follow leaves the azimuth wound up: unwind it, or the way back spins through whole turns.
    rig.normalizeRotations();
    void rig.setLookAt(...pose, placed.current && !reducedMotion);
    placed.current = true;
    // Resizing shouldn't throw the operator's camera back to a preset, so size is left out.
  }, [view, mode, rt.model, reducedMotion, insets, north]);

  // Before CameraControls' own update, which runs at -1, so this frame's move is drawn this frame.
  useFrame((state, delta) => {
    const rig = controls.current;
    const follow = chase.current;
    if (!rig || view !== "follow") {
      follow.active = false;
      follow.lostAt = -1;
      return;
    }
    const { sample } = rt;
    const now = state.clock.elapsedTime;
    let at = -1;
    if (rt.showVehicles && rt.selected >= 0) {
      for (let i = 0; i < sample.count; i++) {
        if (sample.track[i] === rt.selected) {
          at = i;
          break;
        }
      }
    }
    if (at < 0) {
      if (follow.lostAt < 0) follow.lostAt = now;
      else if (now - follow.lostAt >= FOLLOW_HOLD_S) {
        display.update((current) => (current.view === "follow" ? { ...current, view: "oblique" } : current));
      }
      return;
    }
    follow.lostAt = -1;
    if (!follow.active) {
      follow.active = true;
      follow.since = now;
      rig.getTarget(follow.target);
      rig.normalizeRotations();
      void rig.dollyTo(FOLLOW_DISTANCE_M, !reducedMotion);
      void rig.rotatePolarTo(FOLLOW_POLAR, !reducedMotion);
    }
    const settled = Math.min(1, (now - follow.since) / FOLLOW_SETTLE_S);
    const ease = FOLLOW_LOCK_S + (FOLLOW_EASE_S - FOLLOW_LOCK_S) * (1 - settled);
    const pull = reducedMotion ? 1 : 1 - Math.exp(-delta / ease);
    follow.target.x += (sample.bodyU[at] - follow.target.x) * pull;
    follow.target.y += (1 - follow.target.y) * pull;
    follow.target.z += (toWorldZ(sample.bodyV[at]) - follow.target.z) * pull;
    void rig.moveTo(follow.target.x, follow.target.y, follow.target.z, false);
    // Azimuth -heading puts the eye behind the vehicle. Eased, so a lane change isn't a whip pan.
    const swing = reducedMotion ? 1 : 1 - Math.exp(-delta * FOLLOW_TURN_RATE);
    void rig.rotateAzimuthTo(lerpAngle(rig.azimuthAngle, -sample.heading[at], swing), false);
  }, -1.5);

  return <CameraControls ref={controls} makeDefault />;
}

/**
 * Picks by distance to each vehicle's real footprint on the ground, not by hitting triangles: a
 * motorcycle 400 m from the camera is two pixels wide, and clicking near it should still work.
 */
export function Picker({ rt }: { rt: Runtime }) {
  const camera = useThree((state) => state.camera);
  const element = useThree((state) => state.gl.domElement);
  const pointer = useRef<Vector2 | null>(null);
  const down = useRef<{ x: number; y: number } | null>(null);
  const tools = useMemo(
    () => ({
      ray: new Raycaster(),
      plane: new Plane(new Vector3(0, 1, 0), -0.9),
      hit: new Vector3(),
    }),
    [],
  );

  const nearest = useRef<() => number>(() => -1);
  nearest.current = () => {
    const at = pointer.current;
    if (!at || !rt.showVehicles) return -1;
    tools.ray.setFromCamera(at, camera);
    if (!tools.ray.ray.intersectPlane(tools.plane, tools.hit)) return -1;
    const u = tools.hit.x;
    const v = -tools.hit.z;
    const reach = Math.max(1.6, tools.hit.distanceTo(camera.position) * 0.012);
    const { sample, model } = rt;
    let best = -1;
    let bestDistance = reach;
    for (let i = 0; i < sample.count; i++) {
      const index = sample.track[i];
      const du = u - sample.bodyU[i];
      const dv = v - sample.bodyV[i];
      const sin = Math.sin(sample.heading[i]);
      const cos = Math.cos(sample.heading[i]);
      const along = Math.abs(du * sin + dv * cos) - model.lengthM[index] / 2;
      const across = Math.abs(du * cos - dv * sin) - model.widthM[index] / 2;
      const distance = Math.hypot(Math.max(along, 0), Math.max(across, 0));
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    }
    return best;
  };

  useEffect(() => {
    const locate = (event: PointerEvent) => {
      const box = element.getBoundingClientRect();
      pointer.current = new Vector2(
        ((event.clientX - box.left) / box.width) * 2 - 1,
        -((event.clientY - box.top) / box.height) * 2 + 1,
      );
    };
    const onDown = (event: PointerEvent) => {
      down.current = { x: event.clientX, y: event.clientY };
    };
    const onUp = (event: PointerEvent) => {
      const start = down.current;
      down.current = null;
      if (!start) return;
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > CLICK_SLOP_PX) return;
      locate(event);
      const index = nearest.current();
      selectTrack(rt.model.id, index >= 0 ? rt.model.tracks[index].track_id : null);
    };
    const onLeave = () => {
      pointer.current = null;
      hoverTrack(null);
    };
    element.addEventListener("pointermove", locate);
    element.addEventListener("pointerdown", onDown);
    element.addEventListener("pointerup", onUp);
    element.addEventListener("pointerleave", onLeave);
    return () => {
      element.removeEventListener("pointermove", locate);
      element.removeEventListener("pointerdown", onDown);
      element.removeEventListener("pointerup", onUp);
      element.removeEventListener("pointerleave", onLeave);
    };
  }, [element, rt]);

  // Vehicles move under a still pointer, so hover is settled per frame, not per pointer event.
  useFrame(() => {
    if (!pointer.current || down.current) return;
    const index = nearest.current();
    const id = index >= 0 ? rt.model.tracks[index].track_id : null;
    hoverTrack(id);
    element.style.cursor = index >= 0 ? "pointer" : "";
  });

  return null;
}
