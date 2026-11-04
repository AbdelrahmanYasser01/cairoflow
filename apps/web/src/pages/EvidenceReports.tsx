import { Link } from "react-router-dom";
import { Button, ButtonLink, EmptyState, ErrorState, Label } from "../design";
import { Instrument, PageHead } from "../design/Instrument";
import { decisionFor } from "../domain/chain";
import { samFacts } from "../domain/sam";
import { GateBoard } from "../evidence/GateBoard";
import { PlacementBar } from "../evidence/Placement";
import { Limitations, RecordStrip } from "../evidence/Limits";
import { useInView } from "../evidence/useInView";
import { Candidates, Hypotheses } from "../evidence/Proposals";
import { ChainGraph, PipelineFlow } from "../evidence/Provenance";
import { DecisionVerdict, SimulationMatrix } from "../evidence/Verdict";
import { useChain } from "../instruments/Chain";
import { ModelReading } from "../instruments/ModelReading";
import { SamChanged } from "../instruments/SamChanged";
import { DECISION_URL } from "../lib/analysis";
import { analysisLabel, useAnalysis } from "../state/analysis";
import { useDecision } from "../state/resources";

const EXPORT_BLOCKED = "A report needs a decision to export";
const EXPORT_NAME = "cairoflow-decision-record.json";

/** The page's parts, for its index, in page order. Only the ones on screen are listed. */
const SECTIONS = [
  { id: "gates", label: "Gates" },
  { id: "decision", label: "Decision" },
  { id: "simulation", label: "Simulation" },
  { id: "candidates", label: "Candidates" },
  { id: "hypotheses", label: "Hypotheses" },
  { id: "model-replies", label: "Muse's reading" },
  { id: "what-sam-changed", label: "What SAM changed" },
  { id: "provenance", label: "Provenance" },
  { id: "pipeline", label: "Pipeline" },
  { id: "limitations", label: "Limitations" },
] as const;

function ExportReport({ ready }: { ready: boolean }) {
  return (
    <div className="flex flex-col items-end gap-1.5">
      {ready ? (
        <a
          href={DECISION_URL}
          download={EXPORT_NAME}
          className="inline-flex min-h-10 items-center gap-2 border border-rule-2 bg-asphalt-2 px-4 font-display text-body leading-none font-medium text-paint-1 transition-colors duration-150 hover:border-amber hover:bg-asphalt-3"
        >
          <span aria-hidden="true" className="mono text-amber">↓</span>
          Export decision record
        </a>
      ) : (
        <>
          <Button disabledReason={EXPORT_BLOCKED}>Export decision record</Button>
          <Label>{EXPORT_BLOCKED}</Label>
        </>
      )}
    </div>
  );
}

type SectionId = (typeof SECTIONS)[number]["id"];

const shownSections = (sam: boolean) => SECTIONS.filter(({ id }) => sam || id !== "what-sam-changed");

/** A section's place in the index, as the index numbers it. */
const sectionNumber = (sam: boolean, id: SectionId) => {
  const at = shownSections(sam).findIndex((entry) => entry.id === id);
  return at < 0 ? undefined : String(at + 1).padStart(2, "0");
};

/** Where everything on this long page is. */
function SectionIndex({ sam }: { sam: boolean }) {
  const shown = shownSections(sam);
  const active = useInView(shown.map(({ id }) => id));
  return (
    <nav
      aria-label="On this page"
      className="sticky top-0 z-20 -mx-2 mb-8 flex gap-1 overflow-x-auto border-y border-rule-1 bg-asphalt-0/90 px-2 py-1.5 backdrop-blur"
    >
      {shown.map(({ id, label }, index) => {
        const on = active === id;
        return (
          <Link
            key={id}
            to={{ hash: id }}
            aria-current={on ? "location" : undefined}
            className={`mono flex shrink-0 items-baseline gap-1.5 border px-2.5 py-1 text-label whitespace-nowrap transition-colors duration-150 ${
              on ? "border-amber/60 bg-asphalt-2 text-paint-1" : "border-transparent text-paint-3 hover:text-paint-1"
            }`}
          >
            <span className={`text-micro ${on ? "text-amber" : "text-paint-4"}`}>{String(index + 1).padStart(2, "0")}</span>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

export function EvidenceReports() {
  const { state, retry } = useAnalysis();
  const decision = useDecision();
  const loaded = state.status === "ready" ? state.loaded : null;
  const record = loaded?.record ?? null;
  const paired = record && decision.status === "ready" ? decisionFor(record, decision.data) : null;
  const chain = useChain(loaded);
  const waiting = state.status === "loading" || (loaded !== null && decision.status === "loading");
  const unlinked =
    record && paired && !record.decision_ref?.linked_to_cv
      ? record.data_origin === "camera_cv"
        ? "not this camera run"
        : "not linked to this run"
      : null;
  const sam = record ? samFacts(record) !== null : false;
  const number = (id: SectionId) => (record ? sectionNumber(sam, id) : undefined);

  return (
    <div className="page pb-16">
      <PageHead
        title="Evidence & Reports"
        sub={<Label>{analysisLabel(state)}</Label>}
        meta={<ExportReport ready={paired !== null} />}
      />

      {record ? <SectionIndex sam={sam} /> : null}

      {state.status === "error" ? (
        <Instrument className="mb-8">
          <ErrorState what="Couldn't load this analysis." lastGood={state.lastGood} onRetry={retry} />
        </Instrument>
      ) : null}
      {loaded && decision.status === "error" ? (
        <Instrument className="mb-8">
          <ErrorState what="Couldn't load the decision record." onRetry={decision.retry} />
        </Instrument>
      ) : null}
      {state.status === "none" ? (
        <Instrument className="mb-8">
          <EmptyState
            title="No report loaded"
            reason="Every section fills from a report."
            action={<ButtonLink to="/intersections" tone="text">Choose an analysis</ButtonLink>}
          />
        </Instrument>
      ) : null}
      {loaded && decision.status === "ready" && !paired ? (
        <Instrument className="mb-8">
          <EmptyState title="No decision names this analysis" reason="The decision on this server belongs to another run." />
        </Instrument>
      ) : null}

      <div className="flex flex-col gap-16">
        {record ? (
          <Instrument>
            <GateBoard record={record} index={number("gates")} />
          </Instrument>
        ) : null}
        <Instrument>
          <DecisionVerdict decision={paired} waiting={waiting} unlinked={unlinked} index={number("decision")} />
        </Instrument>
        {paired ? (
          <Instrument>
            <SimulationMatrix decision={paired} index={number("simulation")} />
          </Instrument>
        ) : null}
        {paired ? (
          <Instrument>
            <Candidates decision={paired} index={number("candidates")} />
          </Instrument>
        ) : null}
        {paired ? (
          <Instrument>
            <Hypotheses decision={paired} index={number("hypotheses")} />
          </Instrument>
        ) : null}
        {record ? (
          <Instrument>
            <ModelReading analysisId={record.analysis_id} index={number("model-replies")} />
          </Instrument>
        ) : null}
        {loaded ? (
          <Instrument>
            <SamChanged loaded={loaded} />
          </Instrument>
        ) : null}
        <Instrument>
          <ChainGraph stages={chain} waiting={waiting} index={number("provenance")} />
        </Instrument>
        {record ? (
          <Instrument>
            <PipelineFlow record={record} index={number("pipeline")} />
          </Instrument>
        ) : null}
        {record ? (
          <Instrument>
            <Limitations decision={paired} record={record} index={number("limitations")} />
          </Instrument>
        ) : null}
        <Instrument className="grid grid-cols-1 gap-x-10 gap-y-10 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <RecordStrip decision={paired} record={record} />
          {record ? <PlacementBar record={record} /> : null}
        </Instrument>
      </div>
    </div>
  );
}
