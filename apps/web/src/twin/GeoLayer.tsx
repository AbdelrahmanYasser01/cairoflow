import { Suspense, lazy, useEffect } from "react";
import { display } from "./controls";
import { TwinDensityLegend, TwinProvenance } from "./panels";
import { NO_INSETS, type Insets } from "./scene/runtime";
import { useWideStage } from "./TwinStage";

const TwinScene = lazy(() => import("./TwinScene"));

/** Clears the legend and the provenance line at the foot of the layer. */
const LAYER_INSETS: Insets = { top: 0, right: 0, bottom: 72, left: 0 };

export interface GeoLayerProps {
  /** Defaults to the app's loaded analysis, then the first valid one the server lists. */
  analysisId?: string;
  className?: string;
}

/**
 * The analysis as a 3D geospatial layer, for Vision Lab: tilted view, density columns on a metric
 * grid, full-window ribbons and coordinate ticks. Orbit stays live; it follows the shared clock.
 * Fills its positioned parent. There is no basemap and the overlay says so. Wide, the legend and
 * that line sit on the scene; narrow, they would cover it, so they sit under it.
 */
export function GeoLayer({ analysisId, className = "" }: GeoLayerProps) {
  const wide = useWideStage();

  // Geo framing for this view, restored on leaving so the /twin stage comes back as it was.
  useEffect(() => {
    const before = display.get();
    display.set({ ...before, mode: "geo", view: "oblique" });
    return () => display.set(before);
  }, []);

  return (
    <div className={`absolute inset-0 flex flex-col overflow-hidden bg-asphalt-0 ${className}`}>
      <div className="relative min-h-0 flex-1">
        <Suspense fallback={<p className="mono p-margin text-paint-3">Loading the layer</p>}>
          <TwinScene mode="geo" insets={wide ? LAYER_INSETS : NO_INSETS} {...(analysisId ? { analysisId } : {})} />
        </Suspense>
      </div>
      <div className="flex flex-wrap items-end gap-2 pt-2 lg:pointer-events-none lg:absolute lg:right-3 lg:bottom-3 lg:left-3 lg:pt-0">
        <TwinDensityLegend className="pointer-events-auto" />
        <TwinProvenance compact expandable className="pointer-events-auto max-w-full" />
      </div>
    </div>
  );
}
