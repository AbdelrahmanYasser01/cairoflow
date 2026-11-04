import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { EVIDENCE, EvidenceGlyph } from "../design";
import { DEFAULT_ANALYSIS, SAM_ANALYSIS } from "../domain/analyses";
import { samFacts, type SamFacts } from "../domain/sam";
import { useAnalysis } from "../state/analysis";
import { useDiagnosis, useSamRecord } from "../state/resources";

const share = (value: number) => `${(value * 100).toFixed(1)}%`;

function samLine({ requests, costUsd, interpolatedShareBefore: before, interpolatedShareAfter: after }: SamFacts): string {
  const spend = costUsd === null ? "" : `, $${costUsd.toFixed(3)}`;
  const rows = before === null || after === null ? "" : `. Interpolated rows ${share(before)} to ${share(after)}`;
  return `${requests ?? "No"} box-prompt requests${spend}${rows}.`;
}

function Row({
  name,
  job,
  facts,
  to,
  analysis,
}: {
  name: string;
  job: string;
  facts: ReactNode;
  to: string;
  /** The run this evidence belongs to, loaded on the way there. */
  analysis: string;
}) {
  const { state, select } = useAnalysis();
  const here = state.status === "ready" && state.analysis.id === analysis;
  return (
    <li className="border-t border-rule-1 py-1.5">
      <p className="mono flex items-baseline gap-2">
        <span className="translate-y-[2px]">
          <EvidenceGlyph kind="model_generated" />
        </span>
        <Link
          to={to}
          onClick={() => {
            if (!here) select(analysis);
          }}
          className="underline decoration-rule-2 underline-offset-4 hover:decoration-amber"
          style={{ color: EVIDENCE.model_generated.colour }}
        >
          {name}
        </Link>
        <span className="text-paint-2">{job}</span>
      </p>
      <p className="mono mt-0.5 text-paint-1">{facts}</p>
    </li>
  );
}

/**
 * What each Meta model did here, in the records' own figures. Both are model output, so both carry the
 * model-generated mark, and each links to the evidence for what it says.
 */
export function MetaModels() {
  const sam = useSamRecord();
  const muse = useDiagnosis(DEFAULT_ANALYSIS);
  const facts = sam.status === "ready" ? samFacts(sam.data) : null;
  const reading = muse.status === "ready" && muse.data.present ? muse.data.record : null;
  const usable = reading?.replicates.filter((reply) => reply.status === "ok").length ?? 0;
  const flagged =
    reading?.replicates.filter((reply) => reply.citation_problems.length + reply.unsupported_numbers.length > 0).length ?? 0;

  return (
    <>
      <h2 className="text-head leading-6 font-medium">Meta models</h2>
      <ul className="m-0 mt-1.5 list-none p-0">
        {facts ? (
          <Row
            name="SAM 3.1"
            job="masks on the 60 s window"
            facts={samLine(facts)}
            to="/evidence#what-sam-changed"
            analysis={SAM_ANALYSIS}
          />
        ) : null}
        {reading ? (
          <Row
            name={reading.model}
            job="reads the records"
            facts={`${usable} of ${reading.replicates.length} replies usable, ${
              flagged === 0 ? "checked by code" : `${flagged} flagged by code`
            }.`}
            to="/evidence#model-replies"
            analysis={DEFAULT_ANALYSIS}
          />
        ) : null}
      </ul>
    </>
  );
}
