import { Button, EVIDENCE, EmptyState, ErrorState, EvidenceBadge, EvidenceGlyph, Label, Panel, SkeletonRows } from "../design";
import { DEFAULT_ANALYSIS, analysisName } from "../domain/analyses";
import type { CvDiagnosisRecord, CvEvidenceItem, CvHypothesis, CvReplicate } from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { useDiagnosis } from "../state/resources";

const MODEL_COLOUR = EVIDENCE.model_generated.colour;

/**
 * Model text is set in the reading face, in a ruled column, under a diamond. It never takes the
 * instrument's mono or a numeral size: those mean a recorded value on every other screen, and a
 * number inside a sentence here is the model's sentence, not a measurement.
 */
function ModelText({ children }: { children: string }) {
  return (
    <p className="m-0 border-l pl-3 font-text leading-snug text-paint-2 italic" style={{ borderColor: MODEL_COLOUR }}>
      {children}
    </p>
  );
}

function citedItem(items: ReadonlyMap<string, CvEvidenceItem>, id: string) {
  const item = items.get(id);
  if (!item) {
    return (
      <li key={id} className="py-0.5 text-stop">
        <span className="mono">{`${id}: no such evidence item`}</span>
      </li>
    );
  }
  return (
    <li key={id} className="flex items-baseline gap-2 py-0.5">
      <span className="translate-y-[2px]">
        <EvidenceGlyph kind={item.evidence_kind} size={10} />
      </span>
      <span className="mono shrink-0 text-paint-3">{id}</span>
      <span className="mono min-w-0 text-paint-2">
        {item.statement}
        {item.value === null ? "" : ` (${item.value.toLocaleString("en-US")}${item.unit ? ` ${item.unit}` : ""})`}
      </span>
    </li>
  );
}

function Citations({
  label,
  ids,
  items,
}: {
  label: string;
  ids: readonly string[];
  items: ReadonlyMap<string, CvEvidenceItem>;
}) {
  if (ids.length === 0) return null;
  return (
    <div className="mt-2">
      <Label className="block">{label}</Label>
      <ul className="m-0 list-none p-0">{ids.map((id) => citedItem(items, id))}</ul>
    </div>
  );
}

function Hypothesis({
  reply,
  hypothesis,
  items,
}: {
  reply: string;
  hypothesis: CvHypothesis;
  items: ReadonlyMap<string, CvEvidenceItem>;
}) {
  return (
    <li className="border-b border-rule-1 py-3">
      <div className="flex items-baseline justify-between gap-4">
        <Label tone="paint">{`${reply}, ${hypothesis.hypothesis_id}`}</Label>
        <Label>{`Model's own confidence: ${hypothesis.confidence}`}</Label>
      </div>
      <div className="mt-1.5">
        <ModelText>{hypothesis.statement}</ModelText>
      </div>
      <Citations label="Cited in support" ids={hypothesis.supporting_evidence_ids} items={items} />
      <Citations label="Cited against" ids={hypothesis.contradicting_evidence_ids} items={items} />
      <div className="mt-2">
        <Label className="block">What would settle it, in the model's words</Label>
        <div className="mt-1">
          <ModelText>{hypothesis.how_to_check}</ModelText>
        </div>
      </div>
    </li>
  );
}

/** What deterministic code found wrong with one reply. */
function CodeFindings({ replicate }: { replicate: CvReplicate }) {
  const { citation_problems: citations, unsupported_numbers: numbers } = replicate;
  if (citations.length === 0 && numbers.length === 0) {
    return <Label className="mt-2 block">Checked by code: no citation problem, no unsupported number.</Label>;
  }
  return (
    <div className="mt-2 border-l-2 border-stop pl-3">
      <Label tone="paint" className="block">
        Checked by code
      </Label>
      {citations.length > 0 ? (
        <>
          <Label className="mt-1 block">Citation problems</Label>
          <ul className="m-0 list-none p-0">
            {citations.map((line) => (
              <li key={line} className="mono py-0.5 text-paint-2">
                {line}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {numbers.length > 0 ? (
        <>
          <Label className="mt-1 block">Numbers the evidence doesn't contain</Label>
          <ul className="m-0 list-none p-0">
            {numbers.map((line) => (
              <li key={line} className="mono py-0.5 text-paint-2">
                {line}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function Reply({
  replicate,
  items,
  folded,
}: {
  replicate: CvReplicate;
  items: ReadonlyMap<string, CvEvidenceItem>;
  /** The first reply stands open; the rest are one click away, so the page stays readable. */
  folded: boolean;
}) {
  const heading = `Reply ${replicate.replicate}`;
  if (replicate.status === "failed" || !replicate.diagnosis) {
    return (
      <section className="mt-5">
        <Label tone="paint" className="block">{`${heading}: no usable reply`}</Label>
        <p className="mt-1 leading-snug text-paint-2">{replicate.failure ?? "The record gives no reason."}</p>
      </section>
    );
  }
  const { summary, hypotheses, limitations } = replicate.diagnosis;
  const origin = replicate.from_cache ? "Replayed from cache" : "Live model reply";
  const body = (
    <>
      <div className="mt-1.5">
        <ModelText>{summary}</ModelText>
      </div>
      <CodeFindings replicate={replicate} />
      <ul className="m-0 mt-1 list-none p-0">
        {hypotheses.map((hypothesis) => (
          <Hypothesis key={hypothesis.hypothesis_id} reply={heading} hypothesis={hypothesis} items={items} />
        ))}
      </ul>
      {limitations.length > 0 ? (
        <div className="mt-2">
          <Label className="block">Limits the model states</Label>
          <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
            {limitations.map((line) => (
              <li key={line}>
                <ModelText>{line}</ModelText>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
  if (folded) {
    return (
      <details className="mt-5 border-t border-rule-1 pt-3">
        <summary className="mono cursor-pointer text-paint-1 underline decoration-rule-2 underline-offset-4 hover:decoration-amber">
          {`${heading}, ${hypotheses.length} hypotheses. ${origin}`}
        </summary>
        {body}
      </details>
    );
  }
  return (
    <section className="mt-5">
      <div className="flex items-baseline justify-between gap-4">
        <Label tone="paint">{heading}</Label>
        <Label>{origin}</Label>
      </div>
      {body}
    </section>
  );
}

function Replies({ record }: { record: CvDiagnosisRecord }) {
  const items = new Map(record.evidence.map((item) => [item.evidence_id, item]));
  const usable = record.replicates.filter((replicate) => replicate.status === "ok").length;
  const written = record.replicates.reduce((sum, replicate) => sum + (replicate.diagnosis?.hypotheses.length ?? 0), 0);
  return (
    <>
      <p className="mt-3 leading-snug text-paint-2">
        {`Meta's Muse Spark (${record.model}, prompt ${record.prompt_version}) read this camera run's record. ${usable} of ${record.replicates.length} replies usable. `}
        {`Its ${written} hypotheses are for a person to test: they feed no candidate and no decision. `}
        Code checked that its citations exist and flagged any number the evidence doesn't hold.
      </p>
      {record.replicates.map((replicate, index) => (
        <Reply key={replicate.replicate} replicate={replicate} items={items} folded={index > 0} />
      ))}
    </>
  );
}

/** Muse's reading of a camera analysis. Nothing here is a measurement. */
export function ModelReading({ analysisId }: { analysisId: string | null }) {
  const diagnosis = useDiagnosis(analysisId);
  const { catalog, select } = useAnalysis();
  const study = catalog.status === "ready" ? catalog.analyses.find((row) => row.id === DEFAULT_ANALYSIS) : undefined;
  return (
    <Panel id="model-replies" title="Muse's reading of this camera run" meta={<EvidenceBadge kind="model_generated" note="Text, never a measurement" />}>
      {analysisId === null ? (
        <EmptyState className="mt-3" title="No analysis loaded" />
      ) : diagnosis.status === "loading" ? (
        <div className="mt-3">
          <SkeletonRows rows={3} />
        </div>
      ) : diagnosis.status === "error" ? (
        <div className="mt-3">
          <ErrorState what="Couldn't read the model's reply." onRetry={diagnosis.retry} />
          <p className="mt-1 leading-snug break-words text-paint-3">{diagnosis.message}</p>
        </div>
      ) : diagnosis.data.present ? (
        <Replies record={diagnosis.data.record} />
      ) : (
        <div className="mt-3">
          <EmptyState
            title="No model reading for this analysis"
            reason="No model reply is stored for this run, so there are no hypotheses to show. No figure on this page depends on one."
          />
          {study && analysisId !== DEFAULT_ANALYSIS ? (
            <Button tone="text" className="mt-1" onClick={() => select(DEFAULT_ANALYSIS)}>
              {`Muse read the ${analysisName(study)}: load it`}
            </Button>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
