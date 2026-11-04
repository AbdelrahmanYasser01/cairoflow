/** What the operator has asked the twin to show. Filters decide; every layer reads one mask. */
import type { EvidenceKind } from "../design";
import type { TrackEntry } from "../lib/analysis";
import { createStore } from "./store";

/** Stands for a null field in a filter group, so a track with no approach can still be kept. */
export const NOT_RECORDED = "not_recorded";

export type FilterGroup = "classes" | "approaches" | "turns";

export interface TwinFilters {
  /** Values switched off. Empty means everything shows, so new values are never hidden silently. */
  hidden: Readonly<Record<FilterGroup, ReadonlySet<string>>>;
  /** Seconds from the first frame of the analysis, or null for the whole window. */
  timeS: readonly [number, number] | null;
}

export const NO_FILTERS: TwinFilters = {
  hidden: { classes: new Set(), approaches: new Set(), turns: new Set() },
  timeS: null,
};

export const filters = createStore<TwinFilters>(NO_FILTERS);

export function toggleFilter(group: FilterGroup, value: string): void {
  filters.update((current) => {
    const next = new Set(current.hidden[group]);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return { ...current, hidden: { ...current.hidden, [group]: next } };
  });
}

export function setTimeWindow(timeS: readonly [number, number] | null): void {
  filters.update((current) => ({ ...current, timeS }));
}

export function resetFilters(): void {
  filters.set(NO_FILTERS);
}

export function filterValue(track: TrackEntry, group: FilterGroup): string {
  if (group === "classes") return track.vehicle_class;
  if (group === "approaches") return track.approach ?? NOT_RECORDED;
  return track.turn ?? NOT_RECORDED;
}

/** One byte per track: 1 when it passes every filter. */
export function trackMask(
  tracks: readonly TrackEntry[],
  active: TwinFilters,
  fps: number,
): Uint8Array {
  const mask = new Uint8Array(tracks.length);
  const firstFrame = active.timeS ? active.timeS[0] * fps : -Infinity;
  const lastFrame = active.timeS ? active.timeS[1] * fps : Infinity;
  tracks.forEach((track, index) => {
    if (active.hidden.classes.has(filterValue(track, "classes"))) return;
    if (active.hidden.approaches.has(filterValue(track, "approaches"))) return;
    if (active.hidden.turns.has(filterValue(track, "turns"))) return;
    if (track.start_frame + track.n - 1 < firstFrame || track.start_frame > lastFrame) return;
    mask[index] = 1;
  });
  return mask;
}

export type TwinMode = "twin" | "geo";
export type TwinView = "plan" | "oblique" | "along" | "follow";

export interface TwinDisplay {
  mode: TwinMode;
  view: TwinView;
  trails: boolean;
  /** Twin mode's ground heat. Off to start: the road reads best alone, and heat is one click away. */
  density: boolean;
  /** Geo mode's own layers. Columns start off: they stand in front of the moving vehicles. */
  columns: boolean;
  ribbons: boolean;
  /** Evidence kinds switched off. The scene hides whatever layers are of that kind. */
  hiddenKinds: ReadonlySet<EvidenceKind>;
}

/** One side panel at a time: Limits over Filters would hide the chips behind it. */
export type TwinPanel = "filters" | "limits" | null;

export const openPanel = createStore<TwinPanel>(null);

export const display = createStore<TwinDisplay>({
  mode: "twin",
  view: "along",
  trails: true,
  density: false,
  columns: false,
  ribbons: true,
  hiddenKinds: new Set(),
});

export function toggleKind(kind: EvidenceKind): void {
  display.update((current) => {
    const hiddenKinds = new Set(current.hiddenKinds);
    if (hiddenKinds.has(kind)) hiddenKinds.delete(kind);
    else hiddenKinds.add(kind);
    return { ...current, hiddenKinds };
  });
}

/** Counts from the scene, written a few times a second at most. */
export interface LiveCounts {
  onScreen: number;
  unplaced: number;
  outsideValid: number;
  /** The evidence kind of the positions is switched off, so nothing is drawn or counted. */
  hidden: boolean;
}

export const live = createStore<LiveCounts>({ onScreen: 0, unplaced: 0, outsideValid: 0, hidden: false });

/** What the density layer last accumulated, in its own units, for the legend. */
export interface DensitySummary {
  cellM: number;
  peakVehicleSeconds: number;
  totalVehicleSeconds: number;
}

export const densitySummary = createStore<DensitySummary | null>(null);
