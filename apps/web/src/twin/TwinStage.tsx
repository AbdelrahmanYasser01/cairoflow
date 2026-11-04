import { Suspense, lazy, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { EVIDENCE_KINDS, type EvidenceKind } from "../design";
import { display } from "./controls";
import { useSelection } from "./selection";
import {
  TwinDensityLegend,
  TwinFilters,
  TwinInspector,
  TwinProvenance,
  TwinTransport,
  TwinViewSwitch,
} from "./panels";
import type { Insets } from "./scene/runtime";
import { useStore } from "./store";

const TwinScene = lazy(() => import("./TwinScene"));

/** The width at which the stage's instruments appear: tailwind's lg. */
const WIDE_STAGE = "(min-width: 1024px)";

/** What the instruments cover when they are at rest. Filters and the inspector open over the scene. */
const WIDE_INSETS: Insets = { top: 84, right: 24, bottom: 148, left: 24 };
const NARROW_INSETS: Insets = { top: 0, right: 0, bottom: 84, left: 0 };
const BARE_INSETS: Insets = { top: 0, right: 0, bottom: 64, left: 0 };

/** The inspector's width and its gap: the scene slides this far clear when a vehicle is picked. */
const INSPECTOR_PX = 328;

/** Where the side panels start, under the view switch. */
const PANEL_TOP_PX = 116;
/** Gap kept between the scene and the dock of legend and provenance. */
const DOCK_GAP_PX = 10;
/** Taller than this and Limits is open: that sits over the scene and shouldn't move the camera. */
const DOCK_RESTING_MAX_PX = 96;

/**
 * Pixels the dock takes from the foot of the stage. Measured, because its text wraps differently at
 * every width: a laptop gets two lines where a wall display gets one.
 */
function useDockInset(dock: RefObject<HTMLDivElement | null>): number | null {
  const [inset, setInset] = useState<number | null>(null);
  useLayoutEffect(() => {
    const node = dock.current;
    if (!node) return;
    const measure = () => {
      const height = node.offsetHeight;
      if (height === 0 || height > DOCK_RESTING_MAX_PX) return;
      setInset(Math.round(height + parseFloat(getComputedStyle(node).bottom) + DOCK_GAP_PX));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    measure();
    return () => observer.disconnect();
  }, [dock]);
  return inset;
}

export function useWideStage(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE_STAGE).matches);
  useEffect(() => {
    const query = window.matchMedia(WIDE_STAGE);
    const update = () => setWide(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return wide;
}

export interface TwinStageProps {
  /**
   * Which evidence kinds stay drawn, for a page with its own evidence filter. Leave it out and
   * the stage's filter panel offers the switches instead.
   */
  visibleKinds?: ReadonlySet<EvidenceKind>;
  /** Defaults to the app's loaded analysis, then the first valid one the server lists. */
  analysisId?: string;
  /**
   * The stage's own filters, inspector, view switch and transport. Pass false when the page places
   * those exports itself. The provenance overlay stays on either way.
   */
  chrome?: boolean;
}

/**
 * The /twin stage: fills its positioned parent. three.js loads with the scene, not with this file.
 */
export function TwinStage({ visibleKinds, analysisId, chrome = true }: TwinStageProps) {
  const { mode, hiddenKinds } = useStore(display);
  const wide = useWideStage();
  const picked = useSelection();
  const inspecting = chrome && wide && picked.trackId !== null && (!analysisId || picked.analysisId === analysisId);
  const dock = useRef<HTMLDivElement>(null);
  const dockInset = useDockInset(dock);
  const insets = useMemo(() => {
    const resting = !chrome ? BARE_INSETS : wide ? WIDE_INSETS : NARROW_INSETS;
    return dockInset === null ? resting : { ...resting, bottom: dockInset };
  }, [chrome, wide, dockInset]);
  const kinds = useMemo(
    () => visibleKinds ?? new Set(EVIDENCE_KINDS.filter((kind) => !hiddenKinds.has(kind))),
    [visibleKinds, hiddenKinds],
  );
  // Side panels stop short of the dock, or the foot of an open panel slides under the legend.
  const panelHeight = `calc(100% - ${PANEL_TOP_PX + insets.bottom}px)`;
  return (
    <div className="absolute inset-0 overflow-hidden bg-asphalt-0">
      <Suspense fallback={<p className="mono p-margin text-paint-3">Loading the twin</p>}>
        <TwinScene
          mode={mode}
          visibleKinds={kinds}
          insets={insets}
          panelPx={inspecting ? INSPECTOR_PX : 0}
          {...(analysisId ? { analysisId } : {})}
        />
      </Suspense>

      {chrome ? (
        <>
          <div data-twin-chrome className="absolute top-margin right-margin hidden lg:block">
            <TwinViewSwitch />
          </div>
          <div
            data-twin-chrome
            style={{ maxHeight: panelHeight }}
            className="absolute top-[116px] left-margin hidden w-max max-w-[272px] overflow-y-auto lg:block"
          >
            <TwinFilters collapsible showEvidence={!visibleKinds} />
          </div>
          <div
            data-twin-chrome
            style={{ maxHeight: panelHeight }}
            className="absolute top-[116px] right-margin hidden w-max max-w-[312px] overflow-y-auto lg:block"
          >
            <TwinInspector hideEmpty className="w-full" />
          </div>
          <div
            data-twin-chrome
            className="absolute inset-x-0 bottom-0 border-t border-rule-2 bg-asphalt-1 px-3 lg:inset-x-margin lg:bottom-margin lg:border"
          >
            <TwinTransport />
          </div>
        </>
      ) : null}

      <div
        ref={dock}
        className={`pointer-events-none absolute items-end gap-2 ${
          chrome
            ? "right-margin bottom-[calc(var(--margin)+76px)] left-margin hidden flex-wrap lg:flex"
            : "bottom-margin left-margin flex max-w-[calc(100%-2*var(--margin))] flex-wrap"
        }`}
      >
        <TwinDensityLegend hint={chrome} className="pointer-events-auto" />
        <TwinProvenance compact expandable className="pointer-events-auto max-w-full" />
      </div>

    </div>
  );
}
