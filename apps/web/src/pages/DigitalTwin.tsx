import { useState } from "react";
import {
  ButtonLink,
  EVIDENCE,
  EVIDENCE_KINDS,
  EmptyState,
  ErrorState,
  EvidenceGlyph,
  Label,
  Tooltip,
  type EvidenceKind,
} from "../design";
import { Instrument } from "../design/Instrument";
import { isCalibrated } from "../domain/footage";
import { analysisLabel, useAnalysis } from "../state/analysis";
import {
  TwinDensityLegend,
  TwinFilters,
  TwinInspector,
  TwinProvenance,
  TwinStage,
  TwinViewSwitch,
  drawnKinds,
} from "../twin";

/** What the scene draws, by evidence kind. The stage's own panels own every other filter. */
function EvidenceSwitches({
  kinds,
  visible,
  onToggle,
}: {
  kinds: readonly EvidenceKind[];
  visible: ReadonlySet<EvidenceKind>;
  onToggle: (kind: EvidenceKind) => void;
}) {
  return (
    <ul className="m-0 flex list-none items-center gap-0.5 p-0">
      {kinds.map((kind) => {
        const on = visible.has(kind);
        const name = EVIDENCE[kind].label;
        return (
          <li key={kind}>
            <Tooltip side="bottom" content={`${name}: ${on ? "shown" : "hidden"}`}>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                aria-label={`Show ${name.toLowerCase()} layers`}
                onClick={() => onToggle(kind)}
                className={`flex size-7 items-center justify-center border-b-2 transition-opacity duration-150 ${
                  on ? "border-paint-3" : "border-transparent opacity-40 hover:opacity-100"
                }`}
              >
                <EvidenceGlyph kind={kind} />
              </button>
            </Tooltip>
          </li>
        );
      })}
    </ul>
  );
}

/** The stage when there is no twin to show: one state at a time, never a state over a live scene. */
function Absent() {
  const { state, retry } = useAnalysis();
  return (
    <div className="absolute inset-x-margin bottom-margin">
      {state.status === "error" ? (
        <ErrorState size="stage" what="Couldn't load the twin." lastGood={state.lastGood} onRetry={retry} />
      ) : state.status === "loading" ? (
        <EmptyState size="stage" title="Loading the twin" />
      ) : state.status === "ready" ? (
        <EmptyState
          size="stage"
          title="The twin can't place this analysis"
          reason="This run is uncalibrated: its tracks are in image pixels, so they have no ground position to draw. Vision Lab shows them on the video."
          action={<ButtonLink to="/vision">Open Vision Lab</ButtonLink>}
        />
      ) : (
        <EmptyState
          size="stage"
          title="No twin loaded"
          reason="The twin is built from the tracks of an analysis."
          action={<ButtonLink to="/intersections">Choose an analysis</ButtonLink>}
        />
      )}
    </div>
  );
}

export function DigitalTwin() {
  const { state } = useAnalysis();
  const [visible, setVisible] = useState<ReadonlySet<EvidenceKind>>(new Set(EVIDENCE_KINDS));
  const placed = state.status === "ready" && isCalibrated(state.loaded.record) ? state.loaded.record : null;
  // Loading the next analysis keeps the stage, and so its WebGL context, on what it was showing.
  const [shown, setShown] = useState(placed);
  if (placed !== shown && state.status !== "loading") setShown(placed);
  const record = placed ?? (state.status === "loading" ? shown : null);
  const ready = record !== null;

  function toggle(kind: EvidenceKind) {
    setVisible((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  return (
    <div className="flex min-h-full flex-col lg:relative lg:block lg:h-full lg:overflow-hidden">
      <div className="relative h-[52dvh] shrink-0 overflow-hidden lg:absolute lg:inset-0 lg:h-auto">
        {record ? <TwinStage analysisId={record.analysis_id} visibleKinds={visible} /> : <Absent />}
      </div>

      <Instrument className="pointer-events-none absolute top-5 left-margin lg:top-8">
        <div className="pointer-events-auto flex items-center gap-5">
          <h1 className="text-title leading-none font-medium">Digital Twin</h1>
          {record ? <EvidenceSwitches kinds={drawnKinds(record)} visible={visible} onToggle={toggle} /> : null}
        </div>
        <Label className="mt-2 block">{analysisLabel(state)}</Label>
      </Instrument>

      {ready ? (
        <div className="flex flex-col gap-6 px-margin pt-6 pb-10 lg:hidden">
          <div className="flex flex-col items-start gap-2">
            <TwinDensityLegend />
            <TwinProvenance />
          </div>
          <div className="overflow-x-auto pb-1">
            <TwinViewSwitch />
          </div>
          <TwinFilters />
          <TwinInspector />
        </div>
      ) : null}
    </div>
  );
}
