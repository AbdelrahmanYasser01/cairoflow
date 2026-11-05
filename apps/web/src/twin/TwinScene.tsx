import { PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, ToneMapping, Vignette } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { EVIDENCE_KINDS, EmptyState, ErrorState, type EvidenceKind } from "../design";
import { clock } from "../lib/analysis";
import { useReducedMotion } from "motion/react";
import { display, filters, live, trackMask, type TwinMode, type TwinView } from "./controls";
import { anchorFrom } from "./geo";
import { sampleFrame, type TwinModel } from "./model";
import { selection } from "./selection";
import { useStore } from "./store";
import { useResolvedAnalysis, useTwinModel } from "./useTwinModel";
import { Columns, HeatLayer } from "./scene/Density";
import { Ground } from "./scene/Ground";
import {
  Compass,
  GeoTicks,
  ScaleBar,
  SceneLabels,
  armLabels,
  geoTickLabels,
  gridNorthInJunction,
  scaleBarLabel,
  scaleBarRect,
} from "./scene/Labels";
import { ContextBlocks } from "./scene/Context";
import { FOV_DEG, poseFor } from "./scene/framing";
import { Picker, Rig, ViewFrame } from "./scene/Rig";
import { Reflections } from "./scene/Reflections";
import { Road } from "./scene/Road";
import {
  LEVEL,
  NO_INSETS,
  ORDER,
  createRuntime,
  readPalette,
  type Insets,
  type Runtime,
} from "./scene/runtime";
import { Ribbons, Trails } from "./scene/Trails";
import { Marks, Vehicles } from "./scene/Vehicles";

const ALL_KINDS: ReadonlySet<EvidenceKind> = new Set(EVIDENCE_KINDS);
const FILTER_SETTLE_MS = 110;
const LIVE_COUNT_INTERVAL_S = 0.25;
const HEAT_OPACITY = 0.3;
/** A small embedded view has no zoom control, so it starts closer to the junction. */
const FIXED_EYE_ZOOM = 0.62;

export type TwinQuality = "full" | "light";

function webglAvailable(): boolean {
  try {
    const context = document.createElement("canvas").getContext("webgl2");
    context?.getExtension("WEBGL_lose_context")?.loseContext();
    return context !== null;
  } catch {
    return false;
  }
}

export interface TwinSceneProps {
  mode: TwinMode;
  /** Defaults to the app's loaded analysis, then the first valid one the server lists. */
  analysisId?: string;
  /** False: no orbit, no picking, fixed view. */
  interactive?: boolean;
  /** "light": no shadows or post-processing, for small embedded views. */
  quality?: TwinQuality;
  /** Which evidence kinds stay drawn. Everything by default. */
  visibleKinds?: ReadonlySet<EvidenceKind>;
  /** Fixed view for a non-interactive scene. Interactive scenes follow the `display` store. */
  view?: Exclude<TwinView, "follow">;
  /** Pixels of this scene covered by instruments. The camera frames what stays free. */
  insets?: Insets;
  /** A side panel open over the right of the stage. The scene slides clear; the camera stays put. */
  panelPx?: number;
}

/** Runs first each frame: moves the sample to the clock, and keeps playback in the time window. */
function Runner({ rt }: { rt: Runtime }) {
  const lastLive = useRef(-Infinity);
  const pending = useRef(true);
  useFrame(({ clock: threeClock }) => {
    const window = filters.get().timeS;
    if (window && clock.playing) {
      const start = window[0] * rt.model.fps;
      const end = window[1] * rt.model.fps;
      if (clock.get() < start || clock.get() >= end) clock.seek(start);
    }
    const frame = clock.get();
    if (frame !== rt.frame || rt.dirty) {
      rt.frame = frame;
      rt.dirty = false;
      sampleFrame(rt.model, frame, rt.mask, rt.sample);
      rt.stamp++;
      pending.current = true;
    }
    // The numbers on screen must match what is drawn. Playing, a few updates a second is plenty,
    // and the last change still lands on the next tick. Paused, nothing else would ever refresh it.
    const now = threeClock.elapsedTime;
    if (!pending.current || (clock.playing && now - lastLive.current < LIVE_COUNT_INTERVAL_S)) return;
    pending.current = false;
    lastLive.current = now;
    const hidden = !rt.showVehicles;
    const onScreen = hidden ? 0 : rt.sample.count;
    const unplaced = hidden ? 0 : rt.sample.unplaced;
    const outsideValid = hidden ? 0 : rt.sample.outsideValid;
    const seen = live.get();
    if (
      seen.onScreen !== onScreen ||
      seen.unplaced !== unplaced ||
      seen.outsideValid !== outsideValid ||
      seen.hidden !== hidden
    ) {
      live.set({ onScreen, unplaced, outsideValid, hidden });
    }
  }, -2);
  return null;
}

/** A non-interactive scene's camera, set once. */
function FixedEye({
  rt,
  mode,
  view,
  insets,
}: {
  rt: Runtime;
  mode: TwinMode;
  view: Exclude<TwinView, "follow">;
  insets: Insets;
}) {
  const camera = useThree((state) => state.camera);
  const size = useThree((state) => state.size);
  useLayoutEffect(() => {
    const [x, y, z, tx, ty, tz] = poseFor(view, mode, rt.model, { ...size, insets }, null);
    camera.position.set(
      tx + (x - tx) * FIXED_EYE_ZOOM,
      ty + (y - ty) * FIXED_EYE_ZOOM,
      tz + (z - tz) * FIXED_EYE_ZOOM,
    );
    camera.lookAt(tx, ty, tz);
  }, [camera, size, rt, mode, view, insets]);
  return null;
}

function Lights({ shadows, room }: { shadows: boolean; room: string }) {
  return (
    <>
      <fog attach="fog" args={[room, 520, 1250]} />
      <hemisphereLight args={["#8ea3b8", "#14181c", 0.42]} />
      {/* Warm, low and shadowless from the far side: sodium light off the street, lifting the shade. */}
      <directionalLight position={[110, 40, -90]} intensity={0.32} color="#f2c48a" />
      <directionalLight
        position={[-90, 150, 70]}
        intensity={1.15}
        color="#dfe6ee"
        castShadow={shadows}
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-150}
        shadow-camera-right={150}
        shadow-camera-top={150}
        shadow-camera-bottom={-150}
        shadow-camera-near={20}
        shadow-camera-far={420}
        shadow-bias={-0.0005}
        shadow-radius={5}
      />
      <Reflections />
    </>
  );
}

function World({
  model,
  mode,
  interactive,
  quality,
  visibleKinds,
  fixedView,
  reduced,
  insets,
  panelPx,
}: {
  model: TwinModel;
  mode: TwinMode;
  interactive: boolean;
  quality: TwinQuality;
  visibleKinds: ReadonlySet<EvidenceKind>;
  fixedView: Exclude<TwinView, "follow">;
  reduced: boolean;
  insets: Insets;
  panelPx: number;
}) {
  const palette = useMemo(readPalette, []);
  const rt = useMemo(() => createRuntime(model), [model]);
  const active = useStore(filters);
  const shown = useStore(display);
  const reducedMotion = useReducedMotion() ?? false;
  const full = quality === "full";

  const anchor = useMemo(
    () => anchorFrom(model.record.calibration) ?? anchorFrom(model.site.calibration),
    [model],
  );

  const north = useMemo(
    () => (anchor ? gridNorthInJunction(anchor.bearingDeg) : null),
    [anchor],
  );
  // A ground scale bar reads true only from straight above; in a perspective view it shrinks with
  // depth and runs under the panels, so it is drawn in Plan alone.
  const planView = shown.view === "plan";
  const labels = useMemo(
    () => [
      ...armLabels(model.site),
      ...(planView ? [scaleBarLabel(model.bounds)] : []),
      ...(mode === "geo" && anchor ? geoTickLabels(model.bounds, anchor, model.network) : []),
    ],
    [model, mode, anchor, planView],
  );

  const contextAvoid = useMemo(() => [scaleBarRect(model.bounds)], [model]);

  const positionsKind: EvidenceKind =
    model.record.data_origin === "reference_dataset" ? "observed" : "approximated";
  const showPositions = visibleKinds.has(positionsKind);
  const showRoad = visibleKinds.has("approximated");
  const showTrails = shown.trails && showPositions;
  const showDensity = shown.density && showPositions && model.placeable;

  useLayoutEffect(() => {
    rt.mask = trackMask(model.tracks, active, model.fps);
    rt.dirty = true;
  }, [rt, model, active]);

  useLayoutEffect(() => {
    rt.flat = !visibleKinds.has("assumed");
    rt.showVehicles = showPositions;
    rt.showTrails = showTrails;
    rt.dirty = true;
  }, [rt, visibleKinds, showPositions, showTrails]);

  useEffect(() => {
    const sync = () => {
      const { analysisId, trackId, hoveredTrackId } = selection.get();
      const selected =
        analysisId === model.id && trackId !== null ? (model.indexOfTrack.get(trackId) ?? -1) : -1;
      const hovered = hoveredTrackId !== null ? (model.indexOfTrack.get(hoveredTrackId) ?? -1) : -1;
      if (selected === rt.selected && hovered === rt.hovered) return;
      rt.selected = selected;
      rt.hovered = hovered;
      rt.dirty = true;
      if (selected < 0 && display.get().view === "follow") {
        display.update((current) => ({ ...current, view: "oblique" }));
      }
    };
    sync();
    return selection.subscribe(sync);
  }, [rt, model]);

  // Accumulating density is the one heavy step, so it waits for a filter to stop moving.
  const [settled, setSettled] = useState({ revision: 0, timeS: active.timeS });
  useEffect(() => {
    const timer = setTimeout(
      () => setSettled((last) => ({ revision: last.revision + 1, timeS: active.timeS })),
      FILTER_SETTLE_MS,
    );
    return () => clearTimeout(timer);
  }, [active]);
  const frames = useMemo<readonly [number, number]>(
    () =>
      settled.timeS
        ? [settled.timeS[0] * model.fps, settled.timeS[1] * model.fps]
        : [0, model.frameCount - 1],
    [settled.timeS, model],
  );

  const geo = mode === "geo";
  return (
    <>
      <color attach="background" args={[palette.room.r, palette.room.g, palette.room.b]} />
      <Lights shadows={full} room={`#${palette.room.getHexString()}`} />
      <ViewFrame insets={insets} panelPx={panelPx} reducedMotion={reducedMotion} />
      <Ground
        model={model}
        palette={palette}
        showSurface={showRoad}
        gridStrength={geo ? 1 : full ? 0.5 : 0.28}
      />
      {showRoad ? (
        <Road
          lanes={model.site.lanes}
          gates={model.site.gates}
          network={model.network}
          bounds={model.bounds}
          palette={palette}
        />
      ) : null}
      {interactive && !geo && visibleKinds.has("assumed") ? (
        <ContextBlocks model={model} palette={palette} avoid={contextAvoid} />
      ) : null}
      {full ? (
        <mesh rotation-x={-Math.PI / 2} position-y={LEVEL.shadow} receiveShadow renderOrder={ORDER.shadow}>
          <planeGeometry args={[440, 440]} />
          <shadowMaterial transparent opacity={0.5} depthWrite={false} />
        </mesh>
      ) : null}

      <Runner rt={rt} />
      <Vehicles key={model.id} rt={rt} palette={palette} shadows={full} />
      <Trails rt={rt} palette={palette} />
      <Marks rt={rt} palette={palette} labels={interactive} />

      {showDensity && !geo ? (
        <HeatLayer
          rt={rt}
          palette={palette}
          frames={frames}
          revision={settled.revision}
          opacity={HEAT_OPACITY}
        />
      ) : null}
      {geo && showPositions && model.placeable ? (
        <>
          {shown.columns ? (
            <>
              <HeatLayer
                rt={rt}
                palette={palette}
                frames={frames}
                revision={settled.revision}
                opacity={0.22}
                reports={false}
              />
              <Columns rt={rt} palette={palette} frames={frames} revision={settled.revision} />
            </>
          ) : null}
          {shown.ribbons ? (
            <Ribbons rt={rt} palette={palette} frames={frames} revision={settled.revision} />
          ) : null}
        </>
      ) : null}

      {interactive ? (
        <>
          <SceneLabels labels={labels} insets={insets} />
          {planView ? <ScaleBar bounds={model.bounds} palette={palette} /> : null}
          <Compass anchor={anchor} insets={insets} hidden={panelPx > 0} />
          {geo && anchor ? <GeoTicks bounds={model.bounds} network={model.network} palette={palette} /> : null}
          <Rig
            rt={rt}
            mode={mode}
            view={shown.view}
            reducedMotion={reducedMotion}
            insets={insets}
            north={north}
          />
          <Picker rt={rt} />
        </>
      ) : (
        <FixedEye rt={rt} mode={mode} view={fixedView} insets={insets} />
      )}

      {full ? (
        <EffectComposer multisampling={reduced ? 0 : 4} enableNormalPass={false}>
          <Bloom mipmapBlur intensity={0.9} luminanceThreshold={1.15} luminanceSmoothing={0.1} radius={0.62} />
          <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
          {/* A soft falloff to the stage edge: keeps the eye on the junction, carries no data. */}
          <Vignette offset={0.32} darkness={0.5} />
        </EffectComposer>
      ) : null}
    </>
  );
}

/** What counts as too slow and as comfortable. A 60 Hz display can't read above 60, so 55 will do. */
function qualityBounds(refreshRate: number): [number, number] {
  return refreshRate > 100 ? [60, 100] : [40, 55];
}

/**
 * Steps down if the frame rate can't be held: a laptop GPU should lose sampling and pixel density
 * before it loses smooth motion. Steps back up if that was a passing load, and settles on the
 * lower setting if the two keep trading places.
 */
function QualityGuard({ onChange }: { onChange: (reduced: boolean) => void }) {
  return (
    <PerformanceMonitor
      bounds={qualityBounds}
      flipflops={3}
      onDecline={() => onChange(true)}
      onIncline={() => onChange(false)}
      onFallback={() => onChange(true)}
    />
  );
}

export default function TwinScene({
  mode,
  analysisId,
  interactive = true,
  quality = "full",
  visibleKinds = ALL_KINDS,
  view = "oblique",
  insets = NO_INSETS,
  panelPx = 0,
}: TwinSceneProps) {
  const [webgl] = useState(webglAvailable);
  const [attempt, setAttempt] = useState(0);
  const [reduced, setReduced] = useState(false);
  const resolved = useResolvedAnalysis(analysisId, attempt);
  const state = useTwinModel(resolved.status === "resolved" ? resolved.id : null, attempt);
  const retry = () => setAttempt((count) => count + 1);

  const failure =
    resolved.status === "error" ? resolved.message : state.status === "error" ? state.message : null;
  const model = state.status === "ready" ? state.model : null;

  if (!webgl) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-asphalt-0 p-margin">
        <EmptyState
          size="stage"
          title="The 3D view needs WebGL"
          reason="This browser has WebGL 2 turned off or unavailable, so the junction can't be drawn. The analysis itself is unaffected."
        />
      </div>
    );
  }

  return (
    <div
      className="absolute inset-0 isolate overflow-hidden bg-asphalt-0"
      role="group"
      aria-label="3D view of the junction, drawn from the analysis"
    >
      <Canvas
        dpr={reduced ? 1 : quality === "full" ? [1, 1.75] : [1, 1.25]}
        shadows={quality === "full" ? "percentage" : false}
        camera={{ fov: FOV_DEG, near: 1, far: 2600, position: [120, 90, 130] }}
        gl={{ antialias: quality !== "full", powerPreference: "high-performance", stencil: false }}
        style={{ pointerEvents: interactive ? "auto" : "none" }}
      >
        <QualityGuard onChange={setReduced} />
        {model ? (
          <World
            key={model.id}
            model={model}
            mode={mode}
            interactive={interactive}
            quality={quality}
            visibleKinds={visibleKinds}
            fixedView={view}
            reduced={reduced}
            insets={insets}
            panelPx={panelPx}
          />
        ) : null}
      </Canvas>

      {failure ? (
        <div className="absolute inset-0 flex items-center justify-center p-margin">
          <ErrorState what="The twin couldn't load" lastGood={failure} onRetry={retry} className="max-w-[34rem]" />
        </div>
      ) : null}
      {resolved.status === "none" ? (
        <div className="absolute inset-0 flex items-center justify-center p-margin">
          <EmptyState size="stage" title="No analysis to show" reason="The server lists no valid analysis." />
        </div>
      ) : null}
      {!failure && !model && resolved.status !== "none" ? (
        <p role="status" className="mono absolute inset-0 flex items-center justify-center text-paint-3">
          Loading the analysis
        </p>
      ) : null}
      {model && !model.placeable ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-margin">
          <EmptyState
            size="stage"
            title="Can't place these vehicles"
            reason={`This analysis has no ground positions, so its ${model.tracks.length} tracks stay in image coordinates. The road is drawn; no vehicle is placed on it.`}
            className="bg-asphalt-0/80 p-4"
          />
        </div>
      ) : null}
    </div>
  );
}
