/**
 * Which vehicle the twin is pointing at, readable from any screen. Ids are `track_id` values of
 * the named analysis, so a selection never leaks from one analysis into another.
 *
 *   const { analysisId, trackId } = useSelection();     // in a component
 *   selection.subscribe(() => selection.get().trackId); // outside React
 */
import { createStore, useStore } from "./store";

export interface TwinSelection {
  analysisId: string | null;
  trackId: number | null;
  hoveredTrackId: number | null;
}

const NOTHING: TwinSelection = { analysisId: null, trackId: null, hoveredTrackId: null };

export const selection = createStore<TwinSelection>(NOTHING);

export function selectTrack(analysisId: string, trackId: number | null): void {
  selection.update((current) => ({ ...current, analysisId, trackId }));
}

export function hoverTrack(trackId: number | null): void {
  selection.update((current) =>
    current.hoveredTrackId === trackId ? current : { ...current, hoveredTrackId: trackId },
  );
}

export function clearSelection(): void {
  selection.set(NOTHING);
}

export function useSelection(): TwinSelection {
  return useStore(selection);
}
