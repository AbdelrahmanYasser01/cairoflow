import { useState } from "react";
import { Link } from "react-router-dom";
import { EVIDENCE, EvidenceBadge } from "../design";
import { SIMULATOR } from "../domain/simulation";
import type { CandidateEntry, DecisionSummary } from "../lib/analysis";
import { CheckLamp, ConfidenceMeter, Kicker, ModelTag, SectionHead } from "./parts";

const MODEL = EVIDENCE.model_generated.colour;

/** A record's enum as a reader sees it: no_support becomes No support. */
export function asWords(value: string): string {
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Model text: the reading face, a pink rule, clamped until asked for. */
export function ModelQuote({ text, lines = 3 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false);
  const long = text.length > lines * 70;
  return (
    <div className="border-l pl-3" style={{ borderColor: MODEL }}>
      <p
        className="m-0 font-text text-body leading-snug text-paint-2 italic"
        style={open || !long ? undefined : { display: "-webkit-box", WebkitLineClamp: lines, WebkitBoxOrient: "vertical", overflow: "hidden" }}
      >
        {text}
      </p>
      {long ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="mono mt-1 cursor-pointer text-micro tracking-[0.08em] text-paint-3 uppercase hover:text-paint-1"
          aria-expanded={open}
        >
          {open ? "Less" : "More"}
        </button>
      ) : null}
    </div>
  );
}

function Chip({ to, children, title }: { to: string; children: string; title?: string }) {
  return (
    <Link
      to={{ hash: to }}
      title={title}
      className="mono inline-flex items-center border border-rule-2 px-1.5 py-0.5 text-micro text-paint-1 transition-colors duration-150 hover:border-amber hover:text-amber"
    >
      {children}
    </Link>
  );
}

function CandidateCard({ entry, verdict }: { entry: CandidateEntry; verdict: string | undefined }) {
  const accepted = entry.validator.status === "accepted";
  const { cycle_s: cycle, baseline_cycle_s: base } = entry.validator;
  return (
    <li className="flex min-w-0 flex-col gap-3 border border-rule-1 bg-asphalt-1 p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="font-display text-[40px] leading-none font-medium text-paint-1">{entry.intervention_id}</span>
        <span className="mono text-micro tracking-[0.08em] text-paint-3 uppercase">{asWords(entry.proposal.type)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mono text-micro text-paint-3">Tests</span>
        {(entry.proposal.addresses_hypothesis_ids ?? []).map((id) => (
          <Chip key={id} to="hypotheses" title={`Hypothesis ${id}`}>
            {id}
          </Chip>
        ))}
        {(entry.proposal.addresses_hypothesis_ids ?? []).length === 0 ? <span className="mono text-micro text-paint-4">None on record</span> : null}
      </div>
      <ModelQuote text={entry.proposal.rationale} />
      <div className="mt-auto flex flex-col gap-2 border-t border-rule-1 pt-3">
        <div className="flex items-baseline justify-between gap-3">
          <CheckLamp ok={accepted} title={entry.validator.reasons.join(" ")}>{`Code check: ${asWords(entry.validator.status)}`}</CheckLamp>
          {cycle !== null && base !== null ? <span className="mono text-micro text-paint-3">{`cycle ${base} → ${cycle} s`}</span> : null}
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <span className="mono text-label text-paint-2">{SIMULATOR}</span>
          <span className={`mono text-label ${verdict === "no_support" ? "text-stop" : "text-paint-1"}`}>{verdict ? asWords(verdict) : "Not on record"}</span>
        </div>
      </div>
    </li>
  );
}

export function Candidates({ decision, index }: { decision: DecisionSummary; index?: string }) {
  const verdicts = new Map(decision.ensemble.candidates.map((entry) => [entry.candidate, entry.verdict]));
  return (
    <section id="candidates" className="scroll-mt-24">
      <SectionHead index={index} title="Candidates" meta={<EvidenceBadge kind="model_generated" note={`Proposed by Muse, checked by code, run in ${SIMULATOR}`} />} />
      <ul className="m-0 mt-4 grid list-none grid-cols-1 gap-4 p-0 md:grid-cols-3">
        {decision.candidates.map((entry) => (
          <CandidateCard key={entry.intervention_id} entry={entry} verdict={verdicts.get(entry.intervention_id)} />
        ))}
      </ul>
    </section>
  );
}

/** The reply's slip sits with the words it corrects. */
const SLIPPED = /northbound left/i;

export function Hypotheses({ decision, index }: { decision: DecisionSummary; index?: string }) {
  const slip = decision.limitations.find((line) => line.startsWith("Known slip"));
  const ledTo = (id: string) =>
    decision.candidates.filter((entry) => entry.proposal.addresses_hypothesis_ids?.includes(id)).map((entry) => entry.intervention_id);
  return (
    <section id="hypotheses" className="scroll-mt-24">
      <SectionHead
        index={index}
        title="Hypotheses"
        meta={
          <>
            <span className="mono hidden text-micro text-paint-3 sm:inline">From published NGSIM trajectories</span>
            <EvidenceBadge kind="model_generated" note="Proposed text, no measure" />
          </>
        }
      />
      <ul className="m-0 mt-4 grid list-none grid-cols-1 gap-4 p-0 md:grid-cols-2">
        {decision.hypotheses.map(({ hypothesis, grounding }) => {
          const grounded = grounding.status === "supported";
          const led = ledTo(hypothesis.hypothesis_id);
          return (
            <li key={hypothesis.hypothesis_id} className="flex min-w-0 flex-col gap-3 border border-rule-1 bg-asphalt-1 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="flex items-center gap-3">
                  <span className="font-display text-[32px] leading-none font-medium text-paint-1">{hypothesis.hypothesis_id}</span>
                  <ModelTag label="Muse Spark" />
                </span>
                <ConfidenceMeter level={hypothesis.confidence_label} />
              </div>
              <ModelQuote text={hypothesis.statement} lines={4} />
              {slip && SLIPPED.test(hypothesis.statement) ? (
                <p className="m-0 text-label leading-snug text-paint-3">
                  <span className="mono mr-1.5 text-micro tracking-[0.08em] text-amber uppercase">Slip</span>
                  {slip.replace(/^Known slip in the model text:\s*/, "")}
                </p>
              ) : null}
              <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-rule-1 pt-3">
                <CheckLamp ok={grounded} title={grounding.reasons.join(" ") || undefined}>{`Grounding: ${asWords(grounding.status)}`}</CheckLamp>
                <span className="flex items-center gap-1.5">
                  <Kicker>Led to</Kicker>
                  {led.map((id) => (
                    <Chip key={id} to="candidates">
                      {id}
                    </Chip>
                  ))}
                  {led.length === 0 ? <span className="mono text-micro text-paint-4">None</span> : null}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
