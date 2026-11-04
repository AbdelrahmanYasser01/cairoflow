import { describe, expect, it } from "vitest";
import type { AnalysisRecord, FootageSource } from "../lib/analysis";
import { frameOnScreen, openingFrame, seedClipS, tracksPerFrame } from "./footage";

function recordOf(windows: readonly (readonly [number, number])[] = []): AnalysisRecord {
  return {
    fps: 10,
    frame_count: 100,
    source_start_s: 200,
    calibration: { mode: "calibrated", valid_windows_s: windows },
  } as unknown as AnalysisRecord;
}

const clipOf = (source_start_s: number, duration_s: number) => ({ source_start_s, duration_s }) as FootageSource;

/** Two tracks across frames 10 to 19, three across 50 to 59, and one alone at the start. */
const tracks = [
  { start_frame: 0, n: 5 },
  { start_frame: 10, n: 10 },
  { start_frame: 10, n: 10 },
  { start_frame: 50, n: 10 },
  { start_frame: 50, n: 10 },
  { start_frame: 50, n: 10 },
];

describe("tracks on each frame", () => {
  it("counts a track on every frame it runs, and not after", () => {
    const counts = tracksPerFrame(tracks, 100);
    expect(counts[0]).toBe(1);
    expect(counts[5]).toBe(0);
    expect(counts[19]).toBe(2);
    expect(counts[20]).toBe(0);
    expect(counts[59]).toBe(3);
    expect(counts).toHaveLength(100);
  });

  it("keeps a track running past the end of the record inside it", () => {
    expect(tracksPerFrame([{ start_frame: 98, n: 10 }], 100)[99]).toBe(1);
  });
});

describe("the frame a run opens on", () => {
  it("is the busiest, the earliest of equals", () => {
    expect(openingFrame({ record: recordOf(), tracks })).toBe(50);
  });

  it("prefers a measured window over a busier frame outside it", () => {
    expect(openingFrame({ record: recordOf([[201, 202]]), tracks })).toBe(10);
  });

  it("falls back to the whole span when the measured windows hold no track", () => {
    expect(openingFrame({ record: recordOf([[204, 204.5]]), tracks })).toBe(50);
  });

  it("keeps to a span", () => {
    expect(openingFrame({ record: recordOf(), tracks }, [0, 30])).toBe(10);
  });

  it("is the span's start when nothing is in it", () => {
    expect(openingFrame({ record: recordOf(), tracks: [] })).toBe(0);
    expect(openingFrame({ record: recordOf(), tracks }, [30, 40])).toBe(30);
  });
});

describe("where a clip starts", () => {
  const analysis = { record: recordOf(), tracks };

  it("shows the shared clock's frame when the clip holds it", () => {
    const clip = clipOf(200, 10);
    const at = seedClipS(analysis, clip, 37);
    expect(frameOnScreen(analysis.record, clip, at)).toBe(37);
  });

  it("lands on the same frame in a clip that starts before the analysis", () => {
    const clip = clipOf(150, 300);
    const at = seedClipS(analysis, clip, 37);
    expect(frameOnScreen(analysis.record, clip, at)).toBe(37);
  });

  it("moves to the busiest frame it holds when the clock is outside it", () => {
    const clip = clipOf(201, 2);
    expect(frameOnScreen(analysis.record, clip, seedClipS(analysis, clip, 80))).toBe(10);
  });

  it("starts at its own start when it holds none of the analysis", () => {
    expect(seedClipS(analysis, clipOf(100, 10), 37)).toBe(0);
  });
});
