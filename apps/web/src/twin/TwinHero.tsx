import { Suspense, lazy, useEffect } from "react";
import { clock } from "../lib/analysis";
import { TwinProvenance } from "./panels";
import type { Insets } from "./scene/runtime";

const TwinScene = lazy(() => import("./TwinScene"));

/** The provenance lines along the foot: two at this size. */
const HERO_INSETS: Insets = { top: 0, right: 0, bottom: 52, left: 0 };

export interface TwinHeroProps {
  /** Defaults to the app's loaded analysis, then the first valid one the server lists. */
  analysisId?: string;
  /** Run the shared clock while this is on screen, and stop it again on leaving. */
  autoplay?: boolean;
  className?: string;
}

/**
 * The compact twin for the Command Center: no pointer input, no shadows or post-processing, and
 * one line saying what it shows. Fills its positioned parent.
 */
export function TwinHero({ analysisId, autoplay = true, className = "" }: TwinHeroProps) {
  useEffect(() => {
    if (!autoplay || clock.playing) return;
    clock.play();
    return () => clock.pause();
  }, [autoplay]);

  return (
    <div className={`absolute inset-0 overflow-hidden bg-asphalt-0 ${className}`}>
      <Suspense fallback={null}>
        <TwinScene
          mode="twin"
          interactive={false}
          quality="light"
          insets={HERO_INSETS}
          {...(analysisId ? { analysisId } : {})}
        />
      </Suspense>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-asphalt-1/85 px-3 py-1.5">
        <TwinProvenance compact />
      </div>
    </div>
  );
}
