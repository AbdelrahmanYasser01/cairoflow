/**
 * The provenance chain as a set of readings. Each stage says what record it holds and how far that
 * record was checked. A stage is `gate_pass` only when a deterministic check on record passed, and
 * `ungated` when the record exists and nothing checked it. Links between stages are not verified
 * here, so no reading may claim them.
 *
 * The chain is two branches, not one line. The camera stages describe the run on screen. The decision
 * stages were computed from the published trajectories, and nothing from a camera run fed them.
 */
import type { EvidenceKind, Status } from "../design/grammar";
import type { AnalysisRecord, DecisionSummary } from "../lib/analysis";
import type { SiteGeometry } from "../twin/site";
import { BASELINE_STATUSES, CHAIN_STAGES, type ChainStageId } from "./vocabulary";

export type ChainBranch = "camera" | "published";

export const CHAIN_BRANCHES: readonly { id: ChainBranch; label: string }[] = [
  { id: "camera", label: "Camera analysis" },
  { id: "published", label: "Published NGSIM trajectories" },
];

/** The first branch's name for the analysis on screen: a reference analysis is not camera output. */
export function branchLabel(branch: ChainBranch, record: AnalysisRecord | null): string {
  if (branch === "camera" && record?.data_origin === "reference_dataset") return "This analysis · NGSIM reference";
  return CHAIN_BRANCHES.find((entry) => entry.id === branch)?.label ?? branch;
}

const BRANCH_OF: Record<ChainStageId, ChainBranch> = {
  source_record: "camera",
  observation: "camera",
  world_entity: "camera",
  problem_statement: "published",
  intervention: "published",
  experiment: "published",
  decision_report: "published",
};

/** Where each stage's own evidence sits. */
const EVIDENCE_OF: Record<ChainStageId, string> = {
  source_record: "/evidence#pipeline",
  observation: "/evidence#gates",
  world_entity: "/intersections#site-record",
  problem_statement: "/evidence#hypotheses",
  intervention: "/evidence#candidates",
  experiment: "/evidence#simulation",
  decision_report: "/evidence#decision",
};

export interface ChainReading {
  id: ChainStageId;
  label: string;
  holds: string;
  status: Status;
  evidence: EvidenceKind | null;
  /** A few words for the compact chain. */
  brief: string;
  /** What the record says, or why there is none. */
  detail: string;
  branch: ChainBranch;
  /** Where this stage's evidence can be read. */
  to: string;
}

export interface ChainInputs {
  record: AnalysisRecord | null;
  decision: DecisionSummary | null;
  site: SiteGeometry | null;
}

type Reading = Omit<ChainReading, "label" | "holds" | "branch" | "to">;

const none = (id: ChainStageId, reason: string): Reading => ({
  id,
  status: "not_available",
  evidence: null,
  brief: "No record",
  detail: reason,
});

function gateOf(passed: number, total: number): Status {
  if (total === 0) return "not_available";
  return passed === total ? "gate_pass" : "gate_fail";
}

function count(value: number, noun: string): string {
  return `${value.toLocaleString("en-US")} ${noun}${value === 1 ? "" : "s"}`;
}

/** The checks that gate this record's own figures: none means they are simply ungated. */
function observationStatus(record: AnalysisRecord): Status {
  const gates = record.gates ?? [];
  if (gates.length === 0) return "ungated";
  return gateOf(gates.filter((gate) => gate.passed).length, gates.length);
}

export function baselineLabel(status: string): string {
  return BASELINE_STATUSES.find((entry) => entry.id === status)?.label ?? status;
}

export function recommendationAllowed(status: string): boolean {
  return BASELINE_STATUSES.find((entry) => entry.id === status)?.recommends ?? false;
}

/** What it would take to recommend, from the ensemble's own rule. Null once something is recommended. */
export function whatWouldChange(decision: DecisionSummary): string | null {
  if (decision.outcome.recommended_intervention_id) return null;
  const timelines = decision.ensemble.members.filter((member) => member.required).length;
  const baseline = baselineLabel(decision.baseline.status).toLowerCase();
  return (
    `To recommend: a baseline assessed as validated (this one is ${baseline}), and a candidate that improves a ` +
    `measure and worsens none on all ${timelines} signal timelines.`
  );
}

function reading(id: ChainStageId, inputs: ChainInputs): Reading {
  const { record, decision, site } = inputs;
  switch (id) {
    case "source_record":
      if (!record) return none(id, "No analysis loaded");
      return {
        id,
        status: "ungated",
        evidence: record.provenance.kind,
        brief: record.source.captured ?? (record.source.uploaded_by_user ? "Uploaded video" : "Dataset on record"),
        detail: record.source.title,
      };
    case "observation": {
      if (!record) return none(id, "No analysis loaded");
      const tracks = record.metrics.find((metric) => metric.metric_id === "vehicles.total")?.value;
      const gates = record.gates ?? [];
      const passed = gates.filter((gate) => gate.passed).length;
      const origin = record.data_origin === "reference_dataset" ? "Published reference trajectories" : "Tracks from camera analysis";
      const tally = tracks == null ? null : `${count(tracks, "counted track")}, ungated`;
      // The lamp reports the record's checks, so the brief names the checks, never a figure they did not gate.
      return {
        id,
        status: observationStatus(record),
        evidence: record.provenance.kind,
        brief:
          gates.length > 0
            ? `${passed} of ${gates.length} checks passed`
            : tracks == null
              ? "Tracks on record"
              : count(tracks, "counted track"),
        detail: gates.length > 0 && tally ? `${origin} · ${tally}` : origin,
      };
    }
    case "world_entity":
      if (!site) return none(id, "No site geometry loaded");
      return {
        id,
        status: "ungated",
        evidence: "approximated",
        brief: count(site.lanes.length, "lane"),
        detail: `${count(site.lanes.length, "lane")} and ${count(site.gates.length, "reference line")}, derived from trajectories`,
      };
    case "problem_statement": {
      if (!decision) return none(id, "No decision record loaded");
      const total = decision.hypotheses.length;
      const grounded = decision.hypotheses.filter((entry) => entry.grounding.status === "supported").length;
      return {
        id,
        status: gateOf(grounded, total),
        evidence: "model_generated",
        brief: `${grounded} of ${total} grounded`,
        detail: `${grounded} of ${total} model hypotheses passed the grounding check`,
      };
    }
    case "intervention": {
      if (!decision) return none(id, "No decision record loaded");
      const total = decision.candidates.length;
      const accepted = decision.candidates.filter((entry) => entry.validator.status === "accepted").length;
      return {
        id,
        status: gateOf(accepted, total),
        evidence: "model_generated",
        brief: `${accepted} of ${total} accepted`,
        detail: `${accepted} of ${total} model proposals accepted by the validator`,
      };
    }
    case "experiment": {
      if (!decision) return none(id, "No decision record loaded");
      const members = decision.ensemble.members.filter((member) => member.required);
      const passed = members.filter((member) => member.gate_passed).length;
      return {
        id,
        status: gateOf(passed, members.length),
        evidence: "simulated",
        brief: `SUMO, ${count(decision.simulation.seeds.length, "seed")}`,
        detail: `${passed} of ${members.length} signal timelines passed their gate, ${count(decision.simulation.seeds.length, "seed")} each`,
      };
    }
    case "decision_report": {
      if (!decision) return none(id, "No decision record loaded");
      const allowed = recommendationAllowed(decision.baseline.status);
      return {
        id,
        // Refusing is the designed outcome, so it is never drawn as a failed check.
        status: allowed ? "gate_pass" : "abstained",
        evidence: "simulated",
        brief: allowed ? baselineLabel(decision.baseline.status) : "Abstained",
        detail: `Baseline ${baselineLabel(decision.baseline.status).toLowerCase()}. ${decision.outcome.recommendation}`,
      };
    }
  }
}

export function readChain(inputs: ChainInputs): ChainReading[] {
  return CHAIN_STAGES.map((stage) => ({
    ...reading(stage.id, inputs),
    label: stage.label,
    holds: stage.holds,
    branch: BRANCH_OF[stage.id],
    to: EVIDENCE_OF[stage.id],
  }));
}

/**
 * The decision to show beside an analysis. A record naming a run gets that run or nothing. A record
 * naming none still sits at this site, and the site's decision stands apart from it, so the page shows
 * it and says so. An upload has no site, so it has no decision.
 */
export function decisionFor(record: AnalysisRecord, decision: DecisionSummary): DecisionSummary | null {
  if (record.decision_ref) return record.decision_ref.run_id === decision.simulation.run_id ? decision : null;
  return record.source.uploaded_by_user ? null : decision;
}
