import { Link } from "react-router-dom";
import {
  Button,
  ButtonLink,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  Field,
  Label,
  Ledger,
  Panel,
  SkeletonRows,
  StatusMark,
  Tooltip,
  type LedgerColumn,
} from "../design";
import { Instrument, PageHead } from "../design/Instrument";
import { baselineLabel, decisionFor, recommendationAllowed, whatWouldChange } from "../domain/chain";
import { recordWords } from "../domain/gates";
import { samFacts, samMeasuredDisclosures } from "../domain/sam";
import {
  SIMULATOR,
  candidateFindings,
  comparedTimeline,
  signedSeconds,
  type CandidateFinding,
  type Measure,
} from "../domain/simulation";
import { BASELINE_STATUSES } from "../domain/vocabulary";
import { ChainList, useChain } from "../instruments/Chain";
import { ModelReading } from "../instruments/ModelReading";
import { GateOutcomes, PipelineStages, PlacementWindow } from "../instruments/Receipts";
import { SamChanged } from "../instruments/SamChanged";
import { DECISION_URL, type AnalysisRecord, type CandidateEntry, type DecisionSummary } from "../lib/analysis";
import { analysisLabel, useAnalysis } from "../state/analysis";
import { useDecision } from "../state/resources";

const EXPORT_BLOCKED = "A report needs a decision to export";
const EXPORT_NAME = "cairoflow-decision-record.json";

/** The page's parts, for its index. Only the ones on screen are listed. */
const SECTIONS = [
  { id: "gates", label: "Gates" },
  { id: "decision", label: "Decision" },
  { id: "simulation", label: "Simulation" },
  { id: "candidates", label: "Candidates" },
  { id: "hypotheses", label: "Hypotheses" },
  { id: "model-replies", label: "Muse's reading" },
  { id: "what-sam-changed", label: "What SAM changed" },
  { id: "limitations", label: "Limitations" },
  { id: "provenance", label: "Provenance" },
  { id: "pipeline", label: "Pipeline" },
] as const;

/** The limitations that change how a figure is read come first, in this order. */
const LEADING: readonly RegExp[] = [
  /failed: /,
  /agreement and not accuracy/,
  /did not raise recall/,
  /Every value is ungated/,
  /not from camera tracking/,
  /No member's baseline/,
  /West and east results/,
  /Part of this run is outside/,
  /Ground positions are approximated/,
];
const LEADING_SHOWN = 5;

/** A record's enum as a reader sees it: no_support becomes No support. */
function asWords(value: string): string {
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function ExportReport({ ready }: { ready: boolean }) {
  return (
    <div className="flex flex-col items-end gap-1.5">
      {ready ? (
        <a
          href={DECISION_URL}
          download={EXPORT_NAME}
          className="inline-flex min-h-10 items-center border border-rule-2 bg-asphalt-2 px-4 font-display text-body leading-none font-medium text-paint-1 transition-colors duration-150 hover:border-paint-4 hover:bg-asphalt-3"
        >
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

/**
 * The branch in the record. The fork is fixed in code, so it is drawn whether or not a report is
 * loaded; a branch lights only when a record takes it. Refusing is the product working, so Abstain
 * is set as the largest thing on the page when that is what the evidence supports.
 */
function DecisionBranch({ decision }: { decision: DecisionSummary | null }) {
  const status = decision?.baseline.status ?? null;
  const allowed = status ? recommendationAllowed(status) : false;
  const recommended = decision?.outcome.recommended_intervention_id ?? null;
  const lit = !decision ? null : allowed && recommended ? "recommend" : "abstain";
  const accepts = BASELINE_STATUSES.filter((entry) => entry.recommends).map((entry) => entry.label).join(", ");
  const refuses = BASELINE_STATUSES.filter((entry) => !entry.recommends).map((entry) => entry.label).join(", ");

  const stem = (on: boolean) => (on ? "border-amber" : "border-paint-4");
  const weight = (on: boolean) => (on ? "border-b-2 border-l-2" : "border-b border-l");

  return (
    <div className="mt-4 grid grid-cols-1 gap-x-gutter gap-y-6 md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div>
        <Label>Baseline assessment</Label>
        <p className="mt-1 font-display text-num-m leading-none font-medium tracking-[-0.01em] text-paint-1">
          {status ? baselineLabel(status) : "Not assessed"}
        </p>
        <p className="mt-3 leading-snug text-paint-2">
          {decision
            ? decision.outcome.selection_reason.split(":")[0] + ". " + decision.ensemble.selection_reason
            : "The branch is fixed in code: only a candidate selected under a validated baseline is recommended. Model text can propose a candidate, but it sets neither the baseline status nor this rule."}
        </p>
      </div>

      <div className="relative">
        <div className="relative pb-5 pl-8">
          <span aria-hidden="true" className={`absolute top-0 left-0 h-[17px] w-6 ${weight(lit === "recommend")} ${stem(lit === "recommend")}`} />
          <span aria-hidden="true" className={`absolute top-[17px] bottom-0 left-0 ${lit === "abstain" ? "border-l-2" : "border-l"} ${stem(lit === "abstain")}`} />
          <p
            className={`font-display text-title leading-[34px] font-medium ${
              lit === "recommend" ? "text-paint-1" : "text-paint-3"
            }`}
          >
            Recommend
          </p>
          <Label className="block">Needs: {accepts}</Label>
        </div>
        <div className="relative pl-8">
          <span aria-hidden="true" className={`absolute top-0 left-0 h-[34px] w-6 ${weight(lit === "abstain")} ${stem(lit === "abstain")}`} />
          <p
            className={`font-display font-medium tracking-[-0.015em] ${
              lit === "abstain"
                ? "text-num-l leading-[68px] text-paint-1"
                : "text-title leading-[68px] text-paint-3"
            }`}
          >
            Abstain
          </p>
          <Label className="block">On: {refuses}</Label>
          {decision ? <p className="mt-3 max-w-[32rem] leading-snug text-paint-1">{decision.outcome.recommendation}</p> : null}
          {decision && whatWouldChange(decision) ? (
            <p className="mt-2 max-w-[32rem] leading-snug text-paint-2">{`${whatWouldChange(decision)} Abstaining stops a change the evidence can't back.`}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function MeasureList({ measures }: { measures: readonly Measure[] }) {
  if (measures.length === 0) return <span className="text-paint-3">None</span>;
  return (
    <ul className="m-0 list-none p-0">
      {measures.map(({ metric, name, change }) => (
        <li key={metric} className="py-0.5">
          <Tooltip content={metric}>
            <span tabIndex={0} className="text-paint-1">
              {name}
            </span>
          </Tooltip>
          {change ? <span className="block text-paint-3">{change}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** What the simulator found for each candidate: the figures behind No support. */
function Simulation({ decision }: { decision: DecisionSummary }) {
  const findings = candidateFindings(decision);
  const timelines = decision.ensemble.members.filter((member) => member.required).length;
  const compared = comparedTimeline(decision);
  const noneSupported = decision.ensemble.candidates.every((entry) => entry.verdict === "no_support");
  const columns: readonly LedgerColumn<CandidateFinding>[] = [
    { key: "id", label: "Candidate", span: 2, rowHeader: true, cell: (row) => row.candidate },
    {
      key: "delay",
      label: "Mean delay against baseline, s",
      span: 3,
      cell: (row) => (
        <span className="grid grid-cols-[auto_auto] justify-start gap-x-3">
          {row.delays.map(({ member, differenceS }) => (
            <span key={member} className="contents">
              <span className="text-paint-3">{member}</span>
              <span className={differenceS > 0 ? "text-paint-1" : "text-paint-2"}>{signedSeconds(differenceS)}</span>
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "worse",
      label: `Worse on all ${timelines} timelines`,
      span: 4,
      cell: (row) => <MeasureList measures={row.worse} />,
    },
    {
      key: "better",
      label: `Better on all ${timelines} timelines`,
      span: 3,
      cell: (row) => <MeasureList measures={row.better} />,
    },
  ];
  return (
    <Panel
      id="simulation"
      title="What the simulator found"
      meta={
        <EvidenceBadge
          kind={decision.simulation.evidence_kind}
          note={`${SIMULATOR}, ${decision.simulation.seeds.length} seeds on each of ${timelines} signal timelines`}
        />
      }
    >
      <p className="mt-3 max-w-[44rem] leading-snug text-paint-2">
        {`Each candidate ran in ${SIMULATOR} against the same replayed vehicles and seeds as the baseline.${noneSupported ? " No candidate passed on any timeline." : ""}`}
      </p>
      <Ledger
        className="mt-3"
        caption="Simulated change for each candidate, by signal timeline and measure"
        columns={columns}
        rows={findings}
        rowKey={(row) => row.candidate}
      />
      <p className="mt-2 max-w-[44rem] leading-snug text-paint-3">
        {`Mean of ${decision.simulation.seeds.length} paired seeds. Above zero is worse. ${compared ? `Baseline to candidate figures are from timeline ${compared}. ` : ""}Mean delay has no observed counterpart, and west and east results are not trusted on their own.`}
      </p>
    </Panel>
  );
}

function Candidates({ decision }: { decision: DecisionSummary }) {
  const verdicts = new Map(decision.ensemble.candidates.map((entry) => [entry.candidate, entry]));
  const columns: readonly LedgerColumn<CandidateEntry>[] = [
    { key: "id", label: "Candidate", span: 2, rowHeader: true, cell: (row) => row.intervention_id },
    {
      key: "proposal",
      label: "Muse's proposal, model text",
      span: 5,
      stack: true,
      cell: (row) => <span className="font-text text-body leading-snug text-paint-2">{row.proposal.rationale}</span>,
    },
    {
      key: "tests",
      label: "Tests",
      span: 1,
      cell: (row) => row.proposal.addresses_hypothesis_ids?.join(", ") ?? "Not on record",
    },
    { key: "validator", label: "Code check", span: 2, cell: (row) => asWords(row.validator.status) },
    {
      key: "verdict",
      label: "Across timelines",
      span: 2,
      cell: (row) => asWords(verdicts.get(row.intervention_id)?.verdict ?? "Not on record"),
    },
  ];
  return (
    <Panel id="candidates" title="Candidates" meta={<EvidenceBadge kind="model_generated" note={`Checked by code, compared in ${SIMULATOR}`} />}>
      <Ledger
        className="mt-3"
        caption="Candidate interventions and how each was judged"
        columns={columns}
        rows={decision.candidates}
        rowKey={(row) => row.intervention_id}
      />
    </Panel>
  );
}

/** The reply's slip sits with the words it corrects, not in a list further down the page. */
const SLIPPED = /northbound left/i;

function Hypotheses({ decision }: { decision: DecisionSummary }) {
  const slip = decision.limitations.find((line) => line.startsWith("Known slip"));
  const led = decision.candidates.map((entry) => entry.intervention_id).join(", ");
  return (
    <Panel id="hypotheses" title="Hypotheses behind the candidates" meta={<EvidenceBadge kind="model_generated" note="Proposed text, no measure" />}>
      <p className="mt-3 max-w-[44rem] leading-snug text-paint-2">
        {`Written by Muse from the published NGSIM trajectories, not from a camera run. These led to candidates ${led}.`}
      </p>
      <ul className="m-0 mt-1 list-none p-0">
        {decision.hypotheses.map(({ hypothesis, grounding }) => (
          <li
            key={hypothesis.hypothesis_id}
            className="grid grid-cols-[3rem_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1 border-b border-rule-1 py-2.5 md:grid-cols-[3rem_minmax(0,1fr)_auto]"
          >
            <Label tone="paint">{hypothesis.hypothesis_id}</Label>
            <p className="leading-snug text-paint-2">{hypothesis.statement}</p>
            <Label className="col-start-2 md:col-start-auto">
              {`Evidence check ${grounding.status}, model's confidence ${hypothesis.confidence_label}`}
            </Label>
            {slip && SLIPPED.test(hypothesis.statement) ? (
              <p className="col-start-2 leading-snug text-paint-3 md:col-span-2">{slip}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Limitations({ decision, record }: { decision: DecisionSummary | null; record: AnalysisRecord }) {
  const lines = [...record.disclosures, ...samMeasuredDisclosures(record), ...(decision?.limitations ?? [])];
  const leading = LEADING.flatMap((pattern) => lines.find((line) => pattern.test(line)) ?? []).slice(0, LEADING_SHOWN);
  const rest = lines.filter((line) => !leading.includes(line));
  const row = (line: string) => (
    <li key={line} className="break-inside-avoid border-b border-rule-1 py-2.5 leading-snug text-paint-2">
      {recordWords(line)}
    </li>
  );
  return (
    <Panel id="limitations" title="Limitations" meta={<Label>{`${lines.length} on record`}</Label>}>
      <ul className="m-0 list-none columns-1 gap-x-gutter p-0 md:columns-2">{leading.map(row)}</ul>
      {rest.length > 0 ? (
        <details className="mt-3">
          <summary className="mono cursor-pointer text-paint-1 underline decoration-rule-2 underline-offset-4 hover:decoration-amber">
            {`The other ${rest.length} limitations`}
          </summary>
          <ul className="m-0 mt-1 list-none columns-1 gap-x-gutter p-0 md:columns-2">{rest.map(row)}</ul>
        </details>
      ) : null}
    </Panel>
  );
}

function Reproducibility({ decision, record }: { decision: DecisionSummary | null; record: AnalysisRecord | null }) {
  const reason = "No report loaded";
  const required = decision?.ensemble.members.filter((member) => member.required).length;
  const detector = record?.stages.find((stage) => stage.stage === "detect");
  return (
    <Panel title="Reproducibility">
      <Field label="Analysis" value={record?.analysis_id} reason={reason} />
      {detector ? <Field label="Detector" value={`${detector.tool}, ${detector.licence ?? "licence not recorded"}`} /> : null}
      <Field label="Simulator" value={decision ? SIMULATOR : undefined} quiet reason={reason} />
      <Field label="Simulation run" value={decision?.simulation.run_id} quiet reason={reason} />
      <Field label="Created, UTC" value={decision?.simulation.created_at_utc} quiet reason={reason} />
      <Field label="Seeds" value={decision?.simulation.seeds.length} quiet reason={reason} />
      <Field label="Signal timelines" value={required} quiet reason={reason} />
      <Field label="Baseline assessed" value={decision?.baseline.assessed_on} quiet reason={reason} />
      <Field label="Record files" value={decision?.sources.length} quiet reason={reason} />
      <Field
        label="Fed by this analysis"
        value={decision ? (record?.decision_ref?.linked_to_cv ? "Yes" : "No, published trajectories") : undefined}
        quiet
        reason={reason}
      />
    </Panel>
  );
}

/** Where everything on this long page is. */
function SectionIndex({ sam }: { sam: boolean }) {
  return (
    <nav aria-label="On this page" className="mb-6 flex flex-wrap gap-x-5 gap-y-1 border-b border-rule-1 pb-3">
      {SECTIONS.filter(({ id }) => sam || id !== "what-sam-changed").map(({ id, label }) => (
        <Link
          key={id}
          to={{ hash: id }}
          className="mono text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1 hover:decoration-amber"
        >
          {label}
        </Link>
      ))}
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

  return (
    <div className="page pb-12">
      <PageHead
        title="Evidence & Reports"
        sub={<Label>{analysisLabel(state)}</Label>}
        meta={<ExportReport ready={paired !== null} />}
      />

      {record ? <SectionIndex sam={samFacts(record) !== null} /> : null}

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
            reason="Every stage below fills from a report: what it holds, how far it was checked and what kind of evidence it is."
            action={<ButtonLink to="/intersections" tone="text">Choose an analysis</ButtonLink>}
          />
        </Instrument>
      ) : null}
      {loaded && decision.status === "ready" && !paired ? (
        <Instrument className="mb-8">
          <EmptyState title="No decision names this analysis" reason="The decision on this server belongs to another run." />
        </Instrument>
      ) : null}

      <div className="grid-12 gap-y-10">
        {record ? (
          <Instrument className="col-span-12">
            <GateOutcomes record={record} />
          </Instrument>
        ) : null}
        <Instrument className="col-span-12 flex flex-col gap-10 lg:col-span-8">
          <Panel
            id="decision"
            title="Decision"
            meta={paired ? <EvidenceBadge kind={paired.outcome.evidence_kind} note="Computed under a recorded configuration" /> : undefined}
          >
            {record && paired && !record.decision_ref?.linked_to_cv ? (
              <p className="mt-3 max-w-[44rem] leading-snug text-paint-1">
                This decision was computed from the published NGSIM trajectories. It is not linked to this camera
                analysis: no figure from this run fed it.
              </p>
            ) : null}
            {waiting ? (
              <div className="mt-3">
                <SkeletonRows rows={4} pitch={48} />
              </div>
            ) : (
              <DecisionBranch decision={paired} />
            )}
          </Panel>
          {paired ? <Simulation decision={paired} /> : null}
          {paired ? <Candidates decision={paired} /> : null}
          {paired ? <Hypotheses decision={paired} /> : null}
          {record ? <ModelReading analysisId={record.analysis_id} /> : null}
          {loaded ? <SamChanged loaded={loaded} /> : null}
          {record ? <Limitations decision={paired} record={record} /> : null}
        </Instrument>

        <Instrument className="col-span-12 flex flex-col gap-10 lg:sticky lg:top-4 lg:col-span-4 lg:max-h-[calc(100dvh-var(--strip)-2rem)] lg:self-start lg:overflow-y-auto lg:pr-2">
          {record ? <PlacementWindow record={record} /> : null}
          <Panel id="provenance" title="Provenance chain" meta={<StatusMark status={paired ? "ungated" : "not_available"} bare note="Each stage shows its own record. Links between stages are not verified here." />}>
            {waiting ? (
              <div className="mt-3">
                <SkeletonRows rows={7} pitch={64} />
              </div>
            ) : (
              <ChainList stages={chain} />
            )}
            <p className="mt-2 border-t border-rule-1 pt-3 leading-snug text-paint-3">
              Each stage shows its own record. Links between stages are not verified here.
            </p>
          </Panel>
          <Reproducibility decision={paired} record={record} />
        </Instrument>
        {record ? (
          <Instrument className="col-span-12">
            <PipelineStages record={record} />
          </Instrument>
        ) : null}
      </div>
    </div>
  );
}
