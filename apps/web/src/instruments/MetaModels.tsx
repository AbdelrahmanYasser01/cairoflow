import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { EVIDENCE, EvidenceGlyph } from "../design";
import { DEFAULT_ANALYSIS, SAM_ANALYSIS } from "../domain/analyses";
import { samFacts, type SamFacts } from "../domain/sam";
import { useAnalysis } from "../state/analysis";
import { useDiagnosis, useSamRecord } from "../state/resources";

const share = (value: number) => `${(value * 100).toFixed(1)}%`;

/** A figure the record gives, at a size the eye lands on. */
interface Fact {
  value: string;
  label: string;
}

function samFigures({ requests, costUsd, interpolatedShareBefore: before, interpolatedShareAfter: after }: SamFacts): Fact[] {
  const figures: Fact[] = [{ value: requests === null ? "None" : String(requests), label: "box prompts" }];
  if (costUsd !== null) figures.push({ value: `$${costUsd.toFixed(3)}`, label: "spend" });
  if (before !== null && after !== null) figures.push({ value: `${share(before)} → ${share(after)}`, label: "interpolated rows" });
  return figures;
}

function Row({
  name,
  job,
  facts,
  to,
  analysis,
  large,
}: {
  name: string;
  job: string;
  facts: readonly Fact[];
  to: string;
  /** The run this evidence belongs to, loaded on the way there. */
  analysis: string;
  large: boolean;
}) {
  const { state, select } = useAnalysis();
  const here = state.status === "ready" && state.analysis.id === analysis;
  const colour = EVIDENCE.model_generated.colour;
  return (
    <li className={`border-t border-rule-1 ${large ? "py-3" : "py-1.5"}`}>
      <p className="m-0 flex items-baseline justify-between gap-3">
        <Link
          to={to}
          onClick={() => {
            if (!here) select(analysis);
          }}
          className={`inline-flex items-baseline gap-2 underline decoration-rule-2 underline-offset-4 hover:decoration-amber ${
            large ? "font-display text-head font-medium" : "mono"
          }`}
          style={{ color: colour }}
        >
          <span className="translate-y-[1px]">
            <EvidenceGlyph kind="model_generated" />
          </span>
          {name}
        </Link>
        <span className="eyebrow shrink-0">{job}</span>
      </p>
      {large ? (
        <dl className="m-0 mt-2.5 flex flex-wrap gap-x-5 gap-y-1">
          {facts.map((fact) => (
            <div key={fact.label} className="min-w-0">
              <dd className="m-0 font-display text-num-s leading-none font-medium text-paint-1">{fact.value}</dd>
              <dt className="mono mt-0.5 text-paint-3">{fact.label}</dt>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mono m-0 mt-0.5">
          {facts.map((fact, index) => (
            <span key={fact.label}>
              {index > 0 ? <span className="text-paint-4"> · </span> : null}
              <span className="text-paint-1">{fact.value}</span> <span className="text-paint-3">{fact.label}</span>
            </span>
          ))}
        </p>
      )}
    </li>
  );
}

/**
 * What each Meta model did here, in the records' own figures. Both are model output, so both carry the
 * model-generated mark, and each links to the evidence for what it says.
 */
export function MetaModels({ large = false, footer }: { large?: boolean; footer?: ReactNode }) {
  const sam = useSamRecord();
  const muse = useDiagnosis(DEFAULT_ANALYSIS);
  const facts = sam.status === "ready" ? samFacts(sam.data) : null;
  const reading = muse.status === "ready" && muse.data.present ? muse.data.record : null;
  const usable = reading?.replicates.filter((reply) => reply.status === "ok").length ?? 0;
  const flagged =
    reading?.replicates.filter((reply) => reply.citation_problems.length + reply.unsupported_numbers.length > 0).length ?? 0;

  return (
    <>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="eyebrow eyebrow-dot">Meta models</h2>
        <span className="mono" style={{ color: EVIDENCE.model_generated.colour }}>
          Model output
        </span>
      </div>
      <ul className="m-0 mt-1.5 list-none p-0">
        {facts ? (
          <Row
            name="SAM 3.1"
            job="masks · 60 s"
            facts={samFigures(facts)}
            to="/evidence#what-sam-changed"
            analysis={SAM_ANALYSIS}
            large={large}
          />
        ) : null}
        {reading ? (
          <Row
            name={reading.model}
            job="hypotheses"
            facts={[
              { value: `${usable} of ${reading.replicates.length}`, label: "replies usable" },
              { value: flagged === 0 ? "0 flagged" : `${flagged} flagged`, label: "citations checked by code" },
            ]}
            to="/evidence#model-replies"
            analysis={DEFAULT_ANALYSIS}
            large={large}
          />
        ) : null}
      </ul>
      {footer}
    </>
  );
}
