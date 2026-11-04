import { useState } from "react";
import { Button, EmptyState, ErrorState, EvidenceBadge, EvidenceGlyph, SkeletonRows } from "../design";
import { DEFAULT_ANALYSIS, analysisName } from "../domain/analyses";
import { ModelQuote } from "../evidence/Proposals";
import { CheckLamp, ConfidenceMeter, Kicker, ModelTag, SectionHead } from "../evidence/parts";
import type { CvDiagnosisRecord, CvEvidenceItem, CvHypothesis, CvReplicate } from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { useDiagnosis } from "../state/resources";

const tileId = (id: string) => `ev-${id.replace(/[^a-zA-Z0-9-]/g, "-")}`;

function valueWords(item: CvEvidenceItem): string | null {
  if (item.value === null) return null;
  return `${item.value.toLocaleString("en-US")}${item.unit ? ` ${item.unit}` : ""}`;
}

/** One citation: the evidence id, linked to the fact code copied into the model's prompt. */
function CitationChip({
  id,
  item,
  against,
  focus,
  onFocus,
}: {
  id: string;
  item: CvEvidenceItem | undefined;
  against: boolean;
  focus: string | null;
  onFocus: (id: string | null) => void;
}) {
  const [kind, name] = id.includes(":") ? [id.slice(0, id.indexOf(":") + 1), id.slice(id.indexOf(":") + 1)] : ["", id];
  const lit = focus === id;
  if (!item) {
    return (
      <span className="mono inline-flex items-center border border-stop px-1.5 py-0.5 text-micro text-stop" title="No such evidence item">
        {id}
      </span>
    );
  }
  return (
    <button
      type="button"
      onMouseEnter={() => onFocus(id)}
      onMouseLeave={() => onFocus(null)}
      onFocus={() => onFocus(id)}
      onBlur={() => onFocus(null)}
      onClick={() => document.getElementById(tileId(id))?.scrollIntoView({ block: "nearest", behavior: "smooth" })}
      title={`${item.statement}${valueWords(item) ? `: ${valueWords(item)}` : item.status === "gate_fail" ? ": withheld" : ""}`}
      className={`mono inline-flex cursor-pointer items-center gap-1 border px-1.5 py-0.5 text-micro transition-colors duration-150 ${
        lit ? "border-amber text-amber" : against ? "border-dashed border-paint-4 text-paint-2" : "border-rule-2 text-paint-1"
      }`}
    >
      <EvidenceGlyph kind={item.evidence_kind} size={8} />
      <span className="text-paint-4">{kind}</span>
      {name}
    </button>
  );
}

function HypothesisCard({
  hypothesis,
  items,
  focus,
  onFocus,
}: {
  hypothesis: CvHypothesis;
  items: ReadonlyMap<string, CvEvidenceItem>;
  focus: string | null;
  onFocus: (id: string | null) => void;
}) {
  const chips = (ids: readonly string[], against: boolean) =>
    ids.map((id) => <CitationChip key={id} id={id} item={items.get(id)} against={against} focus={focus} onFocus={onFocus} />);
  return (
    <li className="flex min-w-0 flex-col gap-3 border border-rule-1 bg-asphalt-1 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="mono flex min-w-0 items-center gap-2 text-label text-paint-1" title="Model output">
          <EvidenceGlyph kind="model_generated" size={9} />
          <span className="truncate">{hypothesis.hypothesis_id}</span>
        </span>
        <ConfidenceMeter level={hypothesis.confidence} />
      </div>
      <ModelQuote text={hypothesis.statement} lines={4} />
      <div className="flex flex-col gap-2">
        {hypothesis.supporting_evidence_ids.length > 0 ? (
          <div className="grid grid-cols-[3.75rem_minmax(0,1fr)] items-start gap-x-2">
            <span className="mono pt-1 text-micro tracking-[0.08em] text-paint-3 uppercase">For</span>
            <span className="flex flex-wrap gap-1.5">{chips(hypothesis.supporting_evidence_ids, false)}</span>
          </div>
        ) : null}
        {hypothesis.contradicting_evidence_ids.length > 0 ? (
          <div className="grid grid-cols-[3.75rem_minmax(0,1fr)] items-start gap-x-2">
            <span className="mono pt-1 text-micro tracking-[0.08em] text-paint-3 uppercase">Against</span>
            <span className="flex flex-wrap gap-1.5">{chips(hypothesis.contradicting_evidence_ids, true)}</span>
          </div>
        ) : null}
      </div>
      <details className="group mt-auto border-t border-rule-1 pt-2">
        <summary className="mono cursor-pointer list-none text-micro tracking-[0.08em] text-paint-3 uppercase hover:text-paint-1">
          <span className="mr-1.5 inline-block transition-transform group-open:rotate-90">›</span>
          How to settle it
        </summary>
        <div className="mt-2">
          <ModelQuote text={hypothesis.how_to_check} lines={6} />
        </div>
      </details>
    </li>
  );
}

/** What deterministic code found wrong with one reply. */
function CodeFindings({ replicate }: { replicate: CvReplicate }) {
  const { citation_problems: citations, unsupported_numbers: numbers } = replicate;
  if (citations.length === 0 && numbers.length === 0) {
    return <CheckLamp ok>Code check: citations exist, no unsupported number</CheckLamp>;
  }
  return (
    <div className="border-l-2 border-stop pl-3">
      <CheckLamp ok={false}>{`Code check: ${citations.length} citation, ${numbers.length} number problems`}</CheckLamp>
      <ul className="m-0 mt-1 list-none p-0">
        {[...citations, ...numbers].map((line) => (
          <li key={line} className="mono py-0.5 text-label text-paint-2">
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReplyBody({
  replicate,
  items,
  focus,
  onFocus,
}: {
  replicate: CvReplicate;
  items: ReadonlyMap<string, CvEvidenceItem>;
  focus: string | null;
  onFocus: (id: string | null) => void;
}) {
  if (replicate.status === "failed" || !replicate.diagnosis) {
    return (
      <div className="mt-4">
        <Kicker tone="stop">{`Reply ${replicate.replicate}: no usable reply`}</Kicker>
        <p className="mt-1 text-label leading-snug text-paint-2">{replicate.failure ?? "The record gives no reason."}</p>
      </div>
    );
  }
  const { summary, hypotheses, limitations } = replicate.diagnosis;
  return (
    <div className="mt-4 flex flex-col gap-4">
      <div className="grid grid-cols-1 items-start gap-x-8 gap-y-3 lg:grid-cols-[minmax(0,1fr)_auto]">
        <ModelQuote text={summary} lines={2} />
        <CodeFindings replicate={replicate} />
      </div>
      <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 md:grid-cols-2 xl:grid-cols-3">
        {hypotheses.map((hypothesis) => (
          <HypothesisCard key={hypothesis.hypothesis_id} hypothesis={hypothesis} items={items} focus={focus} onFocus={onFocus} />
        ))}
      </ul>
      {limitations.length > 0 ? (
        <details className="group">
          <summary className="mono cursor-pointer list-none text-label text-paint-3 hover:text-paint-1">
            <span className="mr-2 inline-block transition-transform group-open:rotate-90">›</span>
            {`Limits the model states · ${limitations.length}`}
          </summary>
          <ul className="m-0 mt-2 grid list-none grid-cols-1 gap-2 p-0 md:grid-cols-2">
            {limitations.map((line) => (
              <li key={line}>
                <ModelQuote text={line} lines={3} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/** The facts code copied into the prompt. Citations light their fact. */
function EvidenceTiles({ items, focus }: { items: readonly CvEvidenceItem[]; focus: string | null }) {
  return (
    <div className="mt-2">
      <Kicker>{`Evidence given to Muse · copied by code · ${items.length}`}</Kicker>
      <ul className="m-0 mt-2 grid list-none grid-cols-2 gap-px border border-rule-1 bg-rule-1 p-0 sm:grid-cols-4 xl:grid-cols-6">
        {items.map((item) => {
          const lit = focus === item.evidence_id;
          const value = valueWords(item);
          const withheld = value === null && item.status === "gate_fail";
          return (
            <li
              key={item.evidence_id}
              id={tileId(item.evidence_id)}
              className={`min-w-0 px-2.5 py-2 transition-colors duration-150 ${lit ? "bg-asphalt-3 shadow-[inset_0_0_0_1px_var(--amber)]" : "bg-asphalt-0"}`}
              title={item.statement}
            >
              <span className="mono flex items-center gap-1.5 truncate text-micro text-paint-3">
                <EvidenceGlyph kind={item.evidence_kind} size={8} />
                {item.evidence_id}
              </span>
              <span className={`mono mt-1 block truncate text-label ${withheld ? "text-stop" : "text-paint-1"}`}>
                {withheld ? "Withheld" : (value ?? "No value")}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Replies({ record }: { record: CvDiagnosisRecord }) {
  const items = new Map(record.evidence.map((item) => [item.evidence_id, item]));
  const usable = record.replicates.filter((replicate) => replicate.status === "ok").length;
  const written = record.replicates.reduce((sum, replicate) => sum + (replicate.diagnosis?.hypotheses.length ?? 0), 0);
  const clean = record.replicates.every(
    (replicate) => replicate.citation_problems.length === 0 && replicate.unsupported_numbers.length === 0,
  );
  const [chosen, setChosen] = useState(record.replicates[0]?.replicate ?? 1);
  const [focus, setFocus] = useState<string | null>(null);
  const current = record.replicates.find((replicate) => replicate.replicate === chosen) ?? record.replicates[0];
  return (
    <>
      <p className="mono mt-3 text-label text-paint-3">Meta Muse Spark read this camera run · for a person to test</p>
      <div className="mt-4 grid grid-cols-1 gap-px border border-rule-1 bg-rule-1 md:grid-cols-[1.2fr_1fr_1fr_1.4fr]">
        <div className="bg-asphalt-0 px-4 py-3">
          <Kicker>Model</Kicker>
          <span className="mono mt-1 block text-label text-paint-1">{record.model}</span>
          <span className="mono block text-micro text-paint-3">{`prompt ${record.prompt_version}`}</span>
        </div>
        <div className="bg-asphalt-0 px-4 py-3">
          <Kicker>Replies usable</Kicker>
          <span className="mt-1 block font-display text-num-m leading-none text-paint-1">
            {usable}
            <span className="text-paint-4">{` / ${record.replicates.length}`}</span>
          </span>
        </div>
        <div className="bg-asphalt-0 px-4 py-3">
          <Kicker>Hypotheses</Kicker>
          <span className="mt-1 block font-display text-num-m leading-none text-paint-1">{written}</span>
        </div>
        <div className="flex flex-col justify-center gap-1.5 bg-asphalt-0 px-4 py-3">
          <CheckLamp ok={clean}>{clean ? "Citations checked by code" : "Code found problems"}</CheckLamp>
          <span className="mono text-micro text-paint-3">Feeds no candidate, no decision</span>
        </div>
      </div>
      <div role="tablist" aria-label="Replies" className="mt-5 flex flex-wrap gap-px border-b border-rule-2">
        {record.replicates.map((replicate) => {
          const on = replicate.replicate === current?.replicate;
          return (
            <button
              key={replicate.replicate}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => setChosen(replicate.replicate)}
              className={`mono -mb-px cursor-pointer border-b-2 px-4 py-2 text-label transition-colors duration-150 ${
                on ? "border-ev-model-generated text-paint-1" : "border-transparent text-paint-3 hover:text-paint-1"
              }`}
            >
              {`Reply ${replicate.replicate}`}
              <span className="ml-2 text-micro text-paint-4">
                {replicate.status === "ok" ? `${replicate.diagnosis?.hypotheses.length ?? 0} hyp · ${replicate.from_cache ? "cache" : "live"}` : "failed"}
              </span>
            </button>
          );
        })}
      </div>
      {current ? <ReplyBody replicate={current} items={items} focus={focus} onFocus={setFocus} /> : null}
      <div className="mt-6">
        <EvidenceTiles items={record.evidence} focus={focus} />
      </div>
    </>
  );
}

/** Muse's reading of a camera analysis. Nothing here is a measurement. */
export function ModelReading({ analysisId, index }: { analysisId: string | null; index?: string }) {
  const diagnosis = useDiagnosis(analysisId);
  const { catalog, select } = useAnalysis();
  const study = catalog.status === "ready" ? catalog.analyses.find((row) => row.id === DEFAULT_ANALYSIS) : undefined;
  return (
    <section id="model-replies" className="scroll-mt-24">
      <SectionHead
        index={index}
        title="Muse's reading"
        meta={
          <>
            <ModelTag />
            <EvidenceBadge kind="model_generated" note="Text, never a measurement" />
          </>
        }
      />
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
          <EmptyState title="No model reading for this run" reason="No figure on this page depends on one." />
          {study && analysisId !== DEFAULT_ANALYSIS ? (
            <Button tone="text" className="mt-1" onClick={() => select(DEFAULT_ANALYSIS)}>
              {`Muse read the ${analysisName(study)}: load it`}
            </Button>
          ) : null}
        </div>
      )}
    </section>
  );
}
