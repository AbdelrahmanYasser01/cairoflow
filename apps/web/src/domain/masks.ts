/**
 * SAM mask outlines by frame. The file holds every Nth frame only, so a frame between samples either
 * shows the nearest sample, near enough to be the same moment and named as such, or shows nothing.
 * A polygon is never held past that: a vehicle moves between samples and the outline would not.
 */
import type { SamOutline, SamOutlines } from "../lib/analysis";

export interface MaskIndex {
  everyFrames: number;
  /** Half the sampling interval: the most a frame can be from a sample with samples either side. */
  toleranceFrames: number;
  byFrame: ReadonlyMap<number, readonly SamOutline[]>;
}

export interface MaskSample {
  frame: number;
  /** Frames between the frame on screen and this sample. */
  distance: number;
  outlines: readonly SamOutline[];
}

export function indexMasks(file: SamOutlines): MaskIndex {
  const byFrame = new Map<number, SamOutline[]>();
  for (const entry of file.entries) {
    const held = byFrame.get(entry.frame);
    if (held) held.push(entry);
    else byFrame.set(entry.frame, [entry]);
  }
  return { everyFrames: file.every_frames, toleranceFrames: Math.floor(file.every_frames / 2), byFrame };
}

/** The nearest sample within tolerance, the earlier of two equals, or null. */
export function nearestMask(index: MaskIndex, frame: number): MaskSample | null {
  for (let distance = 0; distance <= index.toleranceFrames; distance += 1) {
    for (const at of [frame - distance, frame + distance]) {
      const outlines = index.byFrame.get(at);
      if (outlines) return { frame: at, distance, outlines };
    }
  }
  return null;
}

/** Says which frame the outlines on screen were sampled on. */
export function maskFrameNote(index: MaskIndex, frame: number): string {
  const sample = nearestMask(index, frame);
  if (!sample) return `No mask sampled within ${index.toleranceFrames} frames of this one`;
  if (sample.distance === 0) return `Sampled on this frame, every ${index.everyFrames} frames`;
  return `From frame ${sample.frame}, ${sample.distance} away: sampled every ${index.everyFrames} frames`;
}
