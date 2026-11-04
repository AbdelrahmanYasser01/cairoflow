/**
 * The held-out rule, so the form can explain a window before the server refuses it. The worker
 * remains the authority: `held_out_frames` in packages/perception/.../local/frames.py refuses any
 * camera 2 window that reaches an odd block, and nothing here can open one. This only reads the
 * rule; domain.test.ts checks these constants against the Python source.
 *
 *     block = floor((source_s - 120) / 60), defined on [120, 1020)
 *
 * Even blocks are development footage. Odd blocks are kept for a pre-registered gate. Seconds
 * outside [120, 1020) have no block and no published trajectories, so nothing is held out there.
 */
import type { FootageSource } from "../lib/analysis";

export const COVERED_START_S = 120;
export const COVERED_END_S = 1020;
export const BLOCK_S = 60;

export type Span = readonly [number, number];

export function blockOf(sourceS: number): number | null {
  if (sourceS < COVERED_START_S || sourceS >= COVERED_END_S) return null;
  return Math.floor((sourceS - COVERED_START_S) / BLOCK_S);
}

function blockSpan(block: number): Span {
  const from = COVERED_START_S + block * BLOCK_S;
  return [from, from + BLOCK_S];
}

function coveredBlocks(fromS: number, toS: number): number[] {
  const first = blockOf(Math.max(fromS, COVERED_START_S));
  const last = blockOf(Math.min(toS, COVERED_END_S) - 1e-6);
  if (first === null || last === null || toS <= fromS) return [];
  return Array.from({ length: last - first + 1 }, (_, index) => first + index);
}

/** The held-out blocks a window of source seconds reaches, `toS` not included. */
export function heldOutReached(fromS: number, toS: number): Span[] {
  return coveredBlocks(fromS, toS)
    .filter((block) => block % 2 === 1)
    .map(blockSpan);
}

/** Whole development blocks inside a stretch of source seconds, clipped to it. */
export function devWindows(fromS: number, toS: number): Span[] {
  return coveredBlocks(fromS, toS)
    .filter((block) => block % 2 === 0)
    .map(blockSpan)
    .map(([from, to]): Span => [Math.max(from, fromS), Math.min(to, toS)]);
}

export interface WindowReading {
  /** The window in source seconds. Null for footage with no place in the camera 2 source. */
  sourceSpan: Span | null;
  /** The held-out blocks it reaches. */
  heldOut: readonly Span[];
  /** Why the window cannot run as asked, in plain words. Null when it can. */
  problem: string | null;
}

const words = (seconds: number) => seconds.toLocaleString("en-US", { maximumFractionDigits: 3 });

function spanWords([from, to]: Span): string {
  return `${words(from)} to ${words(to)} s`;
}

/** Whether this window can be asked for, by the bounds of the file and the held-out rule. */
export function readWindow(source: FootageSource, startS: number, durationS: number): WindowReading {
  const offset = source.source_start_s;
  const sourceSpan: Span | null = offset === null ? null : [offset + startS, offset + startS + durationS];
  const heldOut = sourceSpan ? heldOutReached(sourceSpan[0], sourceSpan[1]) : [];
  const end = startS + durationS;
  let problem: string | null = null;
  if (!Number.isFinite(startS) || startS < 0) problem = "The start must be 0 s or later.";
  else if (!Number.isFinite(durationS) || durationS <= 0) problem = "The duration must be more than 0 s.";
  else if (source.duration_s !== null && end > source.duration_s) {
    problem = `This window ends at ${words(end)} s, and ${source.file_name} lasts ${words(source.duration_s)} s.`;
  } else if (heldOut.length > 0) {
    problem = `This window reaches source seconds ${heldOut.map(spanWords).join(" and ")}, which are held out.`;
  }
  return { sourceSpan, heldOut, problem };
}

/** The development windows this file can offer, in seconds of the file. */
export function offeredWindows(source: FootageSource): Span[] {
  const offset = source.source_start_s;
  if (offset === null || source.duration_s === null) return [];
  return devWindows(offset, offset + source.duration_s).map(([from, to]): Span => [from - offset, to - offset]);
}

/**
 * How many frames a window asks for, as `plan_frames` counts them. The worker reads the frame rate to
 * three places, because the camera 2 AVI reports 10.00001 and would otherwise gain a phantom frame.
 */
export function plannedFrames(fps: number, durationS: number, frameStride: number): number {
  const stepS = frameStride / (Math.round(fps * 1000) / 1000);
  return Math.ceil(Number((durationS / stepS).toFixed(6)));
}

export const HELD_OUT_WHY =
  "Odd 60-second blocks of camera 2 are reserved for a pre-registered test, so a live job can't read them. " +
  "This is by design. Pick a window inside a development block instead.";

export { spanWords as windowWords };
