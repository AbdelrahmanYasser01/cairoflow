/**
 * Lining footage up with an analysis. Both are placed on one axis, the second of the source video:
 * a clip starts at its `source_start_s`, and analysis frame 0 is the record's `source_start_s`.
 */
import type { AnalysisRecord, FootageSource, TrackEntry } from "../lib/analysis";

type Span = readonly [number, number];

/** Which vehicles are on which frame: a loaded analysis and a twin model both carry this. */
export interface Occupancy {
  record: AnalysisRecord;
  tracks: readonly Pick<TrackEntry, "start_frame" | "n">[];
}

export function analysisSpan(record: AnalysisRecord): Span {
  return [record.source_start_s, record.source_start_s + record.frame_count / record.fps];
}

export function clipSpan(clip: FootageSource): Span {
  const start = clip.source_start_s ?? 0;
  return [start, start + (clip.duration_s ?? 0)];
}

/**
 * The footage that can be laid under this record. Camera clips share the source's own timeline; an
 * upload has no place on it, so it only fits the analysis made from that same file.
 */
export function footageFor(record: AnalysisRecord, clips: readonly FootageSource[]): FootageSource[] {
  if (record.source.uploaded_by_user) return clips.filter((clip) => clip.file_name === record.source.source_file);
  return clips.filter((clip) => clip.source_start_s !== null);
}

function overlap(a: Span, b: Span): number {
  return Math.max(0, Math.min(a[1], b[1]) - Math.max(a[0], b[0]));
}

/** Seconds of this clip the analysis covers. */
export function coveredS(clip: FootageSource, record: AnalysisRecord): number {
  return overlap(clipSpan(clip), analysisSpan(record));
}

/** The analysis frame showing at a time in the clip. It may lie outside the analysis. */
export function frameAt(record: AnalysisRecord, clip: FootageSource, clipTimeS: number): number {
  return ((clip.source_start_s ?? 0) + clipTimeS - record.source_start_s) * record.fps;
}

/** Absorbs float error in a time that sits exactly on a frame boundary, such as 0.1 * 3. */
const FRAME_EDGE = 1e-6;

/**
 * The analysis frame whose image is on screen. A video frame stays up for its whole interval, so
 * this floors: rounding would draw the next frame's boxes for any time in the back half of a frame,
 * which is where a video can stop when it is paused.
 */
export function frameOnScreen(record: AnalysisRecord, clip: FootageSource, clipTimeS: number): number {
  return Math.floor(frameAt(record, clip, clipTimeS) + FRAME_EDGE);
}

/** The windows of the source video where a gate measured placement. */
function measuredWindows(record: AnalysisRecord): Span[] {
  return (record.calibration.valid_windows_s ?? []).map(([from, to]) => [Number(from), Number(to)] as const);
}

export function insideMeasuredWindow(record: AnalysisRecord, sourceS: number): boolean {
  return measuredWindows(record).some(([from, to]) => sourceS >= from && sourceS <= to);
}

export function isCalibrated(record: AnalysisRecord): boolean {
  return record.calibration.mode === "calibrated";
}

/** How many tracks are on each frame, from where each one starts and how long it runs. */
export function tracksPerFrame(tracks: Occupancy["tracks"], frameCount: number): Int32Array {
  const counts = new Int32Array(frameCount + 1);
  const edge = (frame: number) => Math.max(0, Math.min(frame, frameCount));
  for (const track of tracks) {
    counts[edge(track.start_frame)] += 1;
    counts[edge(track.start_frame + track.n)] -= 1;
  }
  for (let frame = 1; frame <= frameCount; frame += 1) counts[frame] += counts[frame - 1];
  return counts.subarray(0, frameCount);
}

/**
 * The frame a run opens on: the one holding the most tracks, inside a measured window if any frame
 * of the span is, and the earliest of equals. Frame 0 is no opening, because a camera run does not
 * read it and its tracks have yet to confirm. Pass a span to keep to the frames a clip can show.
 */
export function openingFrame({ record, tracks }: Occupancy, span: Span = [0, record.frame_count]): number {
  const counts = tracksPerFrame(tracks, record.frame_count);
  const from = Math.max(0, Math.ceil(span[0]));
  const to = Math.min(record.frame_count, Math.floor(span[1]));
  const busiest = (keep: (frame: number) => boolean) => {
    let best = -1;
    let most = 0;
    for (let frame = from; frame < to; frame += 1) {
      if (counts[frame] > most && keep(frame)) {
        best = frame;
        most = counts[frame];
      }
    }
    return best;
  };
  const measured = busiest((frame) => insideMeasuredWindow(record, record.source_start_s + frame / record.fps));
  if (measured >= 0) return measured;
  return Math.max(busiest(() => true), from);
}

/**
 * Where to start a clip so it shows the shared clock's moment: that frame if the clip holds it, else
 * the busiest frame it does hold. Seconds into the clip, half a frame in, so the picture is the frame
 * asked for and not the one before it. A clip that holds none of the analysis starts at its start.
 */
export function seedClipS(analysis: Occupancy, clip: FootageSource, frame: number): number {
  const { record } = analysis;
  const [clipFrom, clipTo] = clipSpan(clip);
  const first = Math.max(0, Math.ceil((clipFrom - record.source_start_s) * record.fps));
  const last = Math.min(record.frame_count, Math.floor((clipTo - record.source_start_s) * record.fps));
  if (last <= first) return 0;
  const held = Math.floor(frame);
  const target = held >= first && held < last ? held : openingFrame(analysis, [first, last]);
  return record.source_start_s + (target + 0.5) / record.fps - clipFrom;
}

/** What a placement check covers of this run, when a camera run is not wholly inside it. */
export interface MeasuredCoverage {
  label: string;
  /** The record's own disclosure, or the same facts when it holds none. */
  note: string;
  analysed: Span;
  measured: readonly Span[];
  coveredS: number;
}

/**
 * Null when nothing needs saying: reference data was not placed by the camera, an uncalibrated run
 * has no placement at all, and a run inside the windows is covered.
 */
export function measuredCoverage(record: AnalysisRecord): MeasuredCoverage | null {
  if (record.data_origin !== "camera_cv" || !isCalibrated(record)) return null;
  const analysed = analysisSpan(record);
  const measured = measuredWindows(record);
  const covered = measured.reduce((sum, window) => sum + overlap(analysed, window), 0);
  const total = analysed[1] - analysed[0];
  if (covered >= total) return null;
  const list = measured.map(([from, to]) => `${from} to ${to}`).join(", ");
  return {
    label: covered > 0 ? `Placement checked on ${covered} of ${total} s` : "Placement not checked on this run",
    note:
      record.disclosures.find((line) => line.includes("validated window")) ??
      `This run covers source seconds ${analysed[0]} to ${analysed[1]}. Placement was measured on ${list || "no window"}.`,
    analysed,
    measured,
    coveredS: covered,
  };
}

/** Figures that rest on where a vehicle was placed on the ground. */
export function isPlacementMetric(metricId: string): boolean {
  return ["flow.", "vehicles.arm.", "speed.", "stopped."].some((prefix) => metricId.startsWith(prefix));
}

/**
 * The clip that shows the analysis best: most time inside the measured windows, then most time
 * covered by the analysis at all. Null when nothing can be played.
 */
export function pickClip(clips: readonly FootageSource[], record: AnalysisRecord): FootageSource | null {
  const measured = (clip: FootageSource) =>
    measuredWindows(record).reduce((sum, window) => sum + overlap(clipSpan(clip), window), 0);
  const ranked = [...clips].sort(
    (a, b) => measured(b) - measured(a) || coveredS(b, record) - coveredS(a, record),
  );
  return ranked[0] ?? null;
}
