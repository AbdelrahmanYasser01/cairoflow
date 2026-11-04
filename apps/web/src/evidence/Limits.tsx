import type { ReactNode } from "react";
import { recordWords } from "../domain/gates";
import { samMeasuredDisclosures } from "../domain/sam";
import { SIMULATOR } from "../domain/simulation";
import type { AnalysisRecord, DecisionSummary } from "../lib/analysis";
import { Kicker, SectionHead } from "./parts";

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

export function Limitations({ decision, record }: { decision: DecisionSummary | null; record: AnalysisRecord }) {
  const lines = [...record.disclosures, ...samMeasuredDisclosures(record), ...(decision?.limitations ?? [])];
  const leading = LEADING.flatMap((pattern) => lines.find((line) => pattern.test(line)) ?? []);
  const ordered = [...leading, ...lines.filter((line) => !leading.includes(line))];
  const first = ordered[0];
  return (
    <section id="limitations" className="scroll-mt-24">
      <SectionHead index="09" title="Limitations" meta={<span className="mono text-label text-paint-3">{`${lines.length} on record`}</span>} />
      {first ? (
        <details className="group mt-3">
          <summary className="flex cursor-pointer list-none items-baseline gap-3 py-2">
            <span className="mono inline-block text-label text-amber transition-transform group-open:rotate-90">›</span>
            <span className="min-w-0 flex-1 text-body leading-snug text-paint-1">{recordWords(first, { hideIds: true })}</span>
            <span className="mono shrink-0 text-micro tracking-[0.08em] text-paint-3 uppercase group-open:hidden">{`+${ordered.length - 1} more`}</span>
          </summary>
          <ol className="m-0 mt-1 list-none columns-1 gap-x-10 p-0 md:columns-2">
            {ordered.slice(1).map((line, index) => (
              <li key={line} className="flex break-inside-avoid gap-3 border-t border-rule-1 py-2 text-label leading-snug text-paint-2">
                <span className="mono shrink-0 text-micro text-paint-4">{String(index + 2).padStart(2, "0")}</span>
                <span>{recordWords(line)}</span>
              </li>
            ))}
          </ol>
        </details>
      ) : (
        <p className="mono mt-3 text-label text-paint-3">None on record</p>
      )}
    </section>
  );
}

function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 bg-asphalt-0 px-3 py-2.5">
      <Kicker>{label}</Kicker>
      <span className="mono mt-1 block truncate text-label text-paint-1" title={typeof children === "string" ? children : undefined}>
        {children}
      </span>
    </div>
  );
}

const NONE = "No report loaded";

/** What it takes to run this again, as a dense block of record fields. */
export function RecordStrip({ decision, record }: { decision: DecisionSummary | null; record: AnalysisRecord | null }) {
  const detector = record?.stages.find((stage) => stage.stage === "detect");
  const required = decision?.ensemble.members.filter((member) => member.required).length;
  const fed = decision ? (record?.decision_ref?.linked_to_cv ? "Yes" : "No, published trajectories") : NONE;
  return (
    <section aria-label="Reproducibility" className="min-w-0">
      <SectionHead index="10" title="Reproducibility" />
      <div className="mt-4 grid grid-cols-2 gap-px border border-rule-1 bg-rule-1 md:grid-cols-5">
        <Cell label="Analysis">{record?.analysis_id ?? NONE}</Cell>
        <Cell label="Detector">{detector ? `${detector.tool}, ${detector.licence ?? "licence not recorded"}` : NONE}</Cell>
        <Cell label="Simulator">{decision ? SIMULATOR : NONE}</Cell>
        <Cell label="Simulation run">{decision?.simulation.run_id ?? NONE}</Cell>
        <Cell label="Created, UTC">{decision?.simulation.created_at_utc ?? NONE}</Cell>
        <Cell label="Seeds">{decision ? String(decision.simulation.seeds.length) : NONE}</Cell>
        <Cell label="Signal timelines">{required === undefined ? NONE : String(required)}</Cell>
        <Cell label="Baseline assessed">{decision?.baseline.assessed_on ?? NONE}</Cell>
        <Cell label="Record files">{decision ? String(decision.sources.length) : NONE}</Cell>
        <Cell label="Fed by this analysis">{fed}</Cell>
      </div>
    </section>
  );
}
