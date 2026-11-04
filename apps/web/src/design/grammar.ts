/**
 * The evidence grammar: the product's signature, used on every screen.
 *
 * Kinds mirror `EvidenceKind` in packages/contracts/cairoflow_contracts/evidence.py, and the
 * meanings are the project's evidence kinds. Each kind has one glyph shape, one colour and one label.
 * The shape differs for every kind, so the grammar still reads without colour.
 */
export const EVIDENCE_KINDS = [
  "observed",
  "approximated",
  "assumed",
  "model_generated",
  "simulated",
] as const;

export type EvidenceKind = (typeof EVIDENCE_KINDS)[number];

export interface EvidenceSpec {
  label: string;
  /** One line that fits a narrow instrument. */
  short: string;
  /** The full meaning, for tooltips and the design sheet. */
  meaning: string;
  /** The name of the glyph shape, used by the design sheet. */
  shape: string;
  colour: string;
}

export const EVIDENCE: Record<EvidenceKind, EvidenceSpec> = {
  observed: {
    label: "Observed",
    short: "Recorded, not inferred",
    meaning: "Recorded directly from footage or a dataset. Nothing was inferred to produce it.",
    shape: "Solid disc",
    colour: "var(--ev-observed)",
  },
  approximated: {
    label: "Approximated",
    short: "Derived from records",
    meaning: "Derived from observations by a stated method, so it carries that method's error.",
    shape: "Half disc",
    colour: "var(--ev-approximated)",
  },
  assumed: {
    label: "Assumed",
    short: "Taken as given",
    meaning: "Taken as given because nothing measured it. It can be wrong without any warning.",
    shape: "Broken ring",
    colour: "var(--ev-assumed)",
  },
  model_generated: {
    label: "Model-generated",
    short: "Proposed text, no measure",
    meaning: "Text proposed by a model. It is a hypothesis and never a measured value.",
    shape: "Diamond",
    colour: "var(--ev-model-generated)",
  },
  simulated: {
    label: "Simulated",
    short: "Computed, not seen",
    meaning: "Computed by the simulator under a recorded configuration. No street produced it.",
    shape: "Four cells",
    colour: "var(--ev-simulated)",
  },
};

/** Status is a separate axis from evidence: whether a value cleared a gate. */
export const STATUSES = ["gate_pass", "gate_fail", "ungated", "not_available", "abstained"] as const;

export type Status = (typeof STATUSES)[number];

export interface StatusSpec {
  label: string;
  meaning: string;
  /** Which lamp of the signal head is lit; null leaves the head dark. */
  lamp: "stop" | "caution" | "go" | null;
}

export const STATUS: Record<Status, StatusSpec> = {
  gate_pass: {
    label: "Gate passed",
    meaning: "A deterministic gate checked this and it passed.",
    lamp: "go",
  },
  gate_fail: {
    label: "Gate failed",
    meaning: "A deterministic gate checked this and it failed.",
    lamp: "stop",
  },
  ungated: {
    label: "Ungated",
    meaning: "This exists, but no gate has checked it.",
    lamp: "caution",
  },
  not_available: {
    label: "Not available",
    meaning: "There is no value to check.",
    lamp: null,
  },
  abstained: {
    label: "Abstained",
    meaning: "The rule held back a recommendation. This is the designed outcome, not a fault.",
    lamp: null,
  },
};
