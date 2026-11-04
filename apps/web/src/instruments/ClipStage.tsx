import { useEffect, useRef, type RefObject } from "react";
import { useElementSize, type TransportState } from "../design";
import { frameOnScreen, seedClipS } from "../domain/footage";
import { nearestMask, type MaskIndex } from "../domain/masks";
import {
  NO_SPEED,
  POINT_OUTSIDE_VALID_REGION,
  POINT_SAM_REFINED,
  clock,
  type FootageSource,
  type LoadedAnalysis,
  type TrackEntry,
} from "../lib/analysis";

export type OverlayMode = "boxes" | "tracks" | "masks" | "off";

/** A track present on the frame on screen, as the side list shows it. */
export interface FrameTrack {
  trackId: number;
  entry: TrackEntry;
  speedMS: number | null;
  outsideRegion: boolean;
  /** This frame's box is SAM's, not the detector's. */
  samRefined: boolean;
}

/** Clock readings go to React at this rate; the canvas itself runs at the video's frame rate. */
const READING_MS = 80;
const LIST_MS = 250;
/** How many frames of history a track's tail shows. */
const TAIL_FRAMES = 40;
/** A mask's fill: enough to see the shape, not enough to hide the vehicle. */
const MASK_FILL_ALPHA = 0.18;

type FrameVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: (now: number, metadata: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

function tracksOn(loaded: LoadedAnalysis, frame: number): FrameTrack[] {
  const rows: FrameTrack[] = [];
  const { trajectories: t, tracks } = loaded;
  loaded.active.forEach(frame, (index, row) => {
    const entry = tracks[index] as TrackEntry;
    const speed = t.speedCmS[row] as number;
    const flags = t.pointFlags[row] as number;
    rows.push({
      trackId: entry.track_id,
      entry,
      speedMS: speed === NO_SPEED ? null : speed / 100,
      outsideRegion: (flags & POINT_OUTSIDE_VALID_REGION) !== 0,
      samRefined: (flags & POINT_SAM_REFINED) !== 0,
    });
  });
  return rows.sort((a, b) => a.trackId - b.trackId);
}

interface Paint {
  loaded: LoadedAnalysis;
  frame: number;
  mode: OverlayMode;
  selected: number | null;
  masks: MaskIndex | null;
  scaleX: number;
  scaleY: number;
  colour: { box: string; pick: string; model: string };
}

/** The outlines of the nearest sampled frame, or nothing: a sample too far off is not drawn. */
function paintMasks(context: CanvasRenderingContext2D, paint: Paint): void {
  const { frame, masks, selected, scaleX, scaleY, colour } = paint;
  const sample = masks ? nearestMask(masks, frame) : null;
  if (!sample) return;
  for (const outline of sample.outlines) {
    const picked = outline.track_id === selected;
    const tone = picked ? colour.pick : colour.model;
    context.beginPath();
    outline.polygon.forEach(([x, y], index) => {
      if (index === 0) context.moveTo(x * scaleX, y * scaleY);
      else context.lineTo(x * scaleX, y * scaleY);
    });
    context.closePath();
    context.shadowColor = tone; // only drawn where the stage has turned a glow on
    context.globalAlpha = MASK_FILL_ALPHA;
    context.fillStyle = tone;
    context.fill();
    context.globalAlpha = 1;
    context.strokeStyle = tone;
    context.lineWidth = picked ? 2 : 1.25;
    context.stroke();
  }
}

function paintOverlay(context: CanvasRenderingContext2D, paint: Paint): void {
  const { loaded, frame, mode, selected, scaleX, scaleY, colour } = paint;
  if (mode === "off") return;
  if (mode === "masks") paintMasks(context, paint);
  const { trajectories: t, tracks } = loaded;
  const unit = t.header.pxScale;
  loaded.active.forEach(frame, (index, row) => {
    const entry = tracks[index] as TrackEntry;
    const picked = entry.track_id === selected;
    if (mode === "masks" && !picked) return; // the masks are the overlay; only a pick keeps its box
    const outside = ((t.pointFlags[row] as number) & POINT_OUTSIDE_VALID_REGION) !== 0;
    context.globalAlpha = outside ? 0.45 : 1;
    context.strokeStyle = picked ? colour.pick : colour.box;
    context.shadowColor = context.strokeStyle;
    context.lineWidth = picked ? 2 : 1.25;
    context.setLineDash(outside ? [3, 3] : []);

    const x = (t.x[row] as number) * unit * scaleX;
    const y = (t.y[row] as number) * unit * scaleY;
    if (mode === "boxes" || picked) {
      const w = (t.w[row] as number) * unit * scaleX;
      const h = (t.h[row] as number) * unit * scaleY;
      context.strokeRect(x - w / 2, y - h / 2, w, h);
    }
    if (mode === "tracks" || picked) {
      const first = Math.max(entry.first_row, row - TAIL_FRAMES);
      context.beginPath();
      for (let at = first; at <= row; at += 1) {
        const px = (t.x[at] as number) * unit * scaleX;
        const py = (t.y[at] as number) * unit * scaleY;
        if (at === first) context.moveTo(px, py);
        else context.lineTo(px, py);
      }
      context.stroke();
      if (mode === "tracks") {
        context.setLineDash([]);
        context.fillStyle = picked ? colour.pick : colour.box;
        context.fillRect(x - 2, y - 2, 4, 4);
      }
    }
  });
  context.globalAlpha = 1;
  context.setLineDash([]);
}

function trackAtPoint(loaded: LoadedAnalysis, frame: number, x: number, y: number): number | null {
  const { trajectories: t, tracks } = loaded;
  const unit = t.header.pxScale;
  let hit: number | null = null;
  loaded.active.forEach(frame, (index, row) => {
    const halfW = ((t.w[row] as number) * unit) / 2 + 3;
    const halfH = ((t.h[row] as number) * unit) / 2 + 3;
    const dx = Math.abs(x - (t.x[row] as number) * unit);
    const dy = Math.abs(y - (t.y[row] as number) * unit);
    if (dx <= halfW && dy <= halfH) hit = (tracks[index] as TrackEntry).track_id;
  });
  return hit;
}

/** Sizes a canvas for its box and this screen, and reads the colours the overlay is drawn in. */
function prepareCanvas(
  surface: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
  size: { width: number; height: number },
): Paint["colour"] {
  const scale = window.devicePixelRatio || 1;
  surface.width = Math.round(size.width * scale);
  surface.height = Math.round(size.height * scale);
  context.setTransform(scale, 0, 0, scale, 0, 0);
  const style = getComputedStyle(document.documentElement);
  return {
    box: style.getPropertyValue("--ev-approximated").trim(),
    pick: style.getPropertyValue("--amber").trim(),
    model: style.getPropertyValue("--ev-model-generated").trim(),
  };
}

/**
 * The footage with the analysis drawn over it. The video is the master: every frame it presents,
 * the overlay is drawn for the same second of the source. Both sit in one box, so they cannot slip.
 * It opens on the shared clock's moment, and says nothing until it is there: a reading from the
 * video's first frame would pull the clock, and every page on it, back to the start.
 */
export function ClipStage({
  clip,
  loaded,
  mode,
  selected,
  masks,
  videoRef,
  onReading,
  onTracks,
  onPick,
  onFailed,
}: {
  clip: FootageSource;
  loaded: LoadedAnalysis;
  mode: OverlayMode;
  selected: number | null;
  masks: MaskIndex | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  onReading: (reading: TransportState) => void;
  onTracks: (tracks: FrameTrack[]) => void;
  onPick: (trackId: number | null) => void;
  onFailed: () => void;
}) {
  const [box, size] = useElementSize<HTMLDivElement>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const repaint = useRef<() => void>(() => undefined);
  const seeded = useRef(false);
  const live = useRef({ mode, selected, masks, onReading, onTracks });
  live.current = { mode, selected, masks, onReading, onTracks };
  const { record } = loaded;

  useEffect(() => {
    const video = videoRef.current as FrameVideo | null;
    const surface = canvas.current;
    const context = surface?.getContext("2d");
    if (!video || !surface || !context || size.width === 0) return;

    const colour = prepareCanvas(surface, context, size);
    let lastReading = 0;
    let lastList = 0;
    // The presentation time of the frame on screen, which is what to draw for. currentTime differs
    // from it by milliseconds in playback, and by up to a frame once the video stops between frames.
    let presentedS: number | null = null;

    const paint = () => {
      const seconds = presentedS ?? video.currentTime;
      const frame = frameOnScreen(record, clip, seconds);
      const covered = frame >= 0 && frame < record.frame_count;
      context.clearRect(0, 0, size.width, size.height);
      if (covered) {
        paintOverlay(context, {
          loaded,
          frame,
          mode: live.current.mode,
          selected: live.current.selected,
          masks: live.current.masks,
          scaleX: size.width / record.width_px,
          scaleY: size.height / record.height_px,
          colour,
        });
      }
      if (!seeded.current) return;
      const now = performance.now();
      // Stopped, a frame can still land just after the pause: its reading is the one that stays up.
      if (now - lastReading >= READING_MS || video.paused) {
        lastReading = now;
        live.current.onReading({
          currentS: seconds,
          durationS: Number.isFinite(video.duration) ? video.duration : (clip.duration_s ?? null),
          frame,
          playing: !video.paused,
        });
      }
      // Same for the list. A paused video paints nothing more, so a gate here could leave it stale for good.
      if (now - lastList >= LIST_MS || video.paused) {
        lastList = now;
        live.current.onTracks(covered ? tracksOn(loaded, frame) : []);
      }
    };
    repaint.current = () => {
      lastReading = 0;
      lastList = 0;
      paint();
    };

    const seed = () => {
      if (seeded.current || video.readyState < HTMLMediaElement.HAVE_METADATA) return;
      seeded.current = true;
      video.currentTime = seedClipS(loaded, clip, clock.get());
    };
    seed();

    let handle = 0;
    let alive = true;
    const next = (_now?: number, metadata?: { mediaTime: number }) => {
      if (!alive) return;
      presentedS = metadata?.mediaTime ?? null;
      paint();
      handle = video.requestVideoFrameCallback?.(next) ?? requestAnimationFrame(next);
    };
    next();

    const events = ["seeked", "loadeddata", "play", "pause", "ended"] as const;
    const settle = (event: Event) => {
      // Only a seek changes the picture without a presented frame to say so yet. A pause does not.
      if (event.type === "seeked" || event.type === "loadeddata") presentedS = null;
      repaint.current();
    };
    video.addEventListener("loadedmetadata", seed);
    for (const name of events) video.addEventListener(name, settle);
    return () => {
      alive = false;
      if (video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(handle);
      else cancelAnimationFrame(handle);
      video.removeEventListener("loadedmetadata", seed);
      for (const name of events) video.removeEventListener(name, settle);
    };
  }, [clip, loaded, record, size, videoRef]);

  useEffect(() => repaint.current(), [mode, selected, masks]);

  function pick(event: React.MouseEvent<HTMLCanvasElement>) {
    const video = videoRef.current;
    if (!video) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * record.width_px;
    const y = ((event.clientY - bounds.top) / bounds.height) * record.height_px;
    onPick(trackAtPoint(loaded, frameOnScreen(record, clip, video.currentTime), x, y));
  }

  return (
    <div ref={box} className="track-plate relative size-full">
      {clip.video_url ? (
        <video
          ref={videoRef}
          key={clip.source_id}
          src={clip.video_url}
          muted
          playsInline
          preload="auto"
          onError={onFailed}
          aria-label={`Footage, ${clip.title}`}
          className="absolute inset-0 size-full object-fill"
        />
      ) : null}
      <canvas ref={canvas} onClick={pick} aria-hidden="true" className="absolute inset-0 size-full cursor-crosshair" />
    </div>
  );
}

/**
 * Every stored position of every track, drawn once at low alpha: a long exposure of the run. With
 * no video, the lanes the vehicles used appear out of their own tracks. Nothing here is a picture.
 */
function longExposure(
  loaded: LoadedAnalysis,
  size: { width: number; height: number },
  tone: string,
): HTMLCanvasElement | null {
  if (typeof document === "undefined" || size.width === 0 || size.height === 0) return null;
  const surface = document.createElement("canvas");
  const scale = window.devicePixelRatio || 1;
  surface.width = Math.round(size.width * scale);
  surface.height = Math.round(size.height * scale);
  const context = surface.getContext("2d");
  if (!context) return null;
  context.setTransform(scale, 0, 0, scale, 0, 0);
  const { trajectories: t, tracks, record } = loaded;
  const unit = t.header.pxScale;
  const sx = (size.width / record.width_px) * unit;
  const sy = (size.height / record.height_px) * unit;
  context.strokeStyle = tone;
  context.lineWidth = 1;
  // A short run has few tracks, so each is drawn brighter: the lanes should show either way.
  context.globalAlpha = Math.min(0.2, 0.06 + 8 / Math.max(1, tracks.length));
  context.globalCompositeOperation = "lighter";
  for (const entry of tracks) {
    const end = entry.first_row + entry.n;
    context.beginPath();
    for (let row = entry.first_row; row < end; row += 2) {
      const x = (t.x[row] as number) * sx;
      const y = (t.y[row] as number) * sy;
      if (row === entry.first_row) context.moveTo(x, y);
      else context.lineTo(x, y);
    }
    context.stroke();
  }
  return surface;
}

/**
 * The same overlay with no video under it, for footage this machine does not hold. The shared clock
 * is the master here, so the twin and this canvas stay on one moment, and playback carries on.
 */
export function TrackStage({
  loaded,
  mode,
  selected,
  masks,
  onReading,
  onTracks,
  onPick,
}: {
  loaded: LoadedAnalysis;
  mode: OverlayMode;
  selected: number | null;
  masks: MaskIndex | null;
  onReading: (reading: TransportState) => void;
  onTracks: (tracks: FrameTrack[]) => void;
  onPick: (trackId: number | null) => void;
}) {
  const [box, size] = useElementSize<HTMLDivElement>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const repaint = useRef<() => void>(() => undefined);
  const live = useRef({ mode, selected, masks, onReading, onTracks });
  live.current = { mode, selected, masks, onReading, onTracks };
  const { record } = loaded;

  useEffect(() => {
    const surface = canvas.current;
    const context = surface?.getContext("2d");
    if (!surface || !context || size.width === 0) return;

    const colour = prepareCanvas(surface, context, size);
    const exposure = longExposure(loaded, size, colour.box);
    const lastFrame = Math.max(0, record.frame_count - 1);
    let lastReading = 0;
    let lastList = 0;
    let wasPlaying = clock.playing;

    const paint = (value: number) => {
      const frame = Math.min(Math.round(value), lastFrame);
      context.clearRect(0, 0, size.width, size.height);
      if (exposure) context.drawImage(exposure, 0, 0, size.width, size.height);
      context.shadowColor = colour.box;
      context.shadowBlur = 6;
      paintOverlay(context, {
        loaded,
        frame,
        mode: live.current.mode,
        selected: live.current.selected,
        masks: live.current.masks,
        scaleX: size.width / record.width_px,
        scaleY: size.height / record.height_px,
        colour,
      });
      context.shadowBlur = 0;
      // Only playback is throttled. Stopped, each emit is a real change, such as a step's seek
      // straight after its pause, and the reading left on screen has to be the last one.
      const now = performance.now();
      const stopped = !clock.playing;
      if (stopped || now - lastReading >= READING_MS || clock.playing !== wasPlaying) {
        lastReading = now;
        wasPlaying = clock.playing;
        live.current.onReading({
          currentS: value / record.fps,
          durationS: record.frame_count / record.fps,
          frame,
          playing: clock.playing,
        });
      }
      if (stopped || now - lastList >= LIST_MS) {
        lastList = now;
        live.current.onTracks(tracksOn(loaded, frame));
      }
    };
    repaint.current = () => {
      lastReading = 0;
      lastList = 0;
      paint(clock.get());
    };
    return clock.subscribe(paint);
  }, [loaded, record, size]);

  useEffect(() => repaint.current(), [mode, selected, masks]);

  function pick(event: React.MouseEvent<HTMLCanvasElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * record.width_px;
    const y = ((event.clientY - bounds.top) / bounds.height) * record.height_px;
    onPick(trackAtPoint(loaded, Math.round(clock.get()), x, y));
  }

  return (
    <div ref={box} className="track-plate relative size-full">
      <canvas ref={canvas} onClick={pick} aria-hidden="true" className="absolute inset-0 size-full cursor-crosshair" />
    </div>
  );
}
