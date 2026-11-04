import { describe, expect, it } from "vitest";
import { hasSamOutlines, parseSamOutlines, type SamOutline, type SamOutlines } from "../lib/analysis";
import { indexMasks, maskFrameNote, nearestMask } from "./masks";

const outline = (track_id: number, frame: number): SamOutline => ({
  track_id,
  frame,
  polygon: [
    [0, 0],
    [4, 0],
    [4, 4],
  ],
});

const file: SamOutlines = {
  format: "sam_outlines/1",
  analysis_id: "run",
  label: "model_generated",
  every_frames: 5,
  entries: [outline(1, 10), outline(2, 10), outline(1, 15), outline(1, 40)],
};

describe("mask outlines by frame", () => {
  const index = indexMasks(file);

  it("holds every outline of a sampled frame together", () => {
    expect(nearestMask(index, 10)?.outlines.map((entry) => entry.track_id)).toEqual([1, 2]);
  });

  it("shows the nearest sample within half the interval, and says how far off it is", () => {
    expect(nearestMask(index, 12)).toMatchObject({ frame: 10, distance: 2 });
    expect(nearestMask(index, 13)).toMatchObject({ frame: 15, distance: 2 });
  });

  it("holds no polygon past the tolerance, however near the file's edge", () => {
    expect(nearestMask(index, 7)).toBeNull();
    expect(nearestMask(index, 18)).toBeNull();
    expect(nearestMask(index, 28)).toBeNull();
  });

  it("names the sample on screen", () => {
    expect(maskFrameNote(index, 10)).toBe("Sampled on this frame, every 5 frames");
    expect(maskFrameNote(index, 12)).toBe("From frame 10, 2 away: sampled every 5 frames");
    expect(maskFrameNote(index, 28)).toBe("No mask sampled within 2 frames of this one");
  });
});

describe("the outlines file", () => {
  it("is found by its artifact, not by the role alone", () => {
    expect(hasSamOutlines({ artifacts: [{ role: "report", path: "sam_outlines.json" }] })).toBe(true);
    expect(hasSamOutlines({ artifacts: [{ role: "report", path: "report.json" }] })).toBe(false);
    expect(hasSamOutlines({ artifacts: [{ role: "tracks", path: "tracks.json" }] })).toBe(false);
  });

  it("is refused by name when it could not be drawn", () => {
    expect(() => parseSamOutlines({ format: "other" })).toThrow("not a SAM outlines file");
    expect(() => parseSamOutlines({ format: "sam_outlines/1", every_frames: 0, entries: [] })).toThrow("sampling interval");
    expect(() => parseSamOutlines({ format: "sam_outlines/1", every_frames: 5 })).toThrow("no entries");
    expect(parseSamOutlines(file)).toBe(file);
  });
});
