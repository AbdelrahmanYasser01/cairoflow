import { Fragment } from "react";
import { Link } from "react-router-dom";
import { EvidenceGlyph, SkeletonRows, STATUS, StatusMark } from "../design";
import type { Status } from "../design";
import { CHAIN_BRANCHES, type ChainReading } from "../domain/chain";
import type { AnalysisRecord, StageReceipt } from "../lib/analysis";
import { Kicker, SectionHead } from "./parts";

const STATUS_FILL: Record<Status, string> = {
  gate_pass: "var(--go)",
  gate_fail: "var(--stop)",
  ungated: "var(--paint-2)",
  not_available: "transparent",
  abstained: "var(--amber)",
};

function Node({ stage }: { stage: ChainReading }) {
  const has = stage.status !== "not_available";
  const fill = STATUS_FILL[stage.status];
  return (
    <li className="relative min-w-0 flex-1">
      <Link
        to={stage.to.startsWith("/evidence#") ? { hash: stage.to.slice("/evidence#".length) } : stage.to}
        title={stage.detail}
        aria-label={`${stage.label}: ${stage.brief}. ${STATUS[stage.status].label}. Read its evidence.`}
        className="group block pr-3"
      >
        <span className="relative flex h-6 items-center">
          <span
            aria-hidden="true"
            className="relative z-10 size-3.5 border"
            style={{
              background: fill,
              borderColor: has ? fill : "var(--paint-4)",
              boxShadow: stage.status === "gate_fail" ? "0 0 10px rgba(240,82,79,0.8)" : stage.status === "abstained" ? "0 0 10px rgba(245,166,35,0.6)" : "none",
            }}
          />
        </span>
        <span className="mt-2 block font-display text-head leading-tight text-paint-1 underline decoration-transparent underline-offset-4 transition-colors duration-150 group-hover:decoration-amber">
          {stage.label}
        </span>
        <span className="mono mt-1 block text-micro text-paint-3">{stage.brief}</span>
        <span className="mt-2 flex items-center gap-2">
          <StatusMark status={stage.status} decorative />
          {stage.evidence ? <EvidenceGlyph kind={stage.evidence} size={10} /> : null}
        </span>
      </Link>
    </li>
  );
}

/** The chain as two lines with a break between them: camera tracks fed no decision. */
export function ChainGraph({ stages, waiting }: { stages: readonly ChainReading[]; waiting: boolean }) {
  return (
    <section id="provenance" className="scroll-mt-24">
      <SectionHead
        index="07"
        title="Provenance"
        meta={<StatusMark status={stages.some((stage) => stage.status !== "not_available") ? "ungated" : "not_available"} bare note="Each stage shows its own record. Links between stages are not verified here." />}
      />
      {waiting ? (
        <div className="mt-4">
          <SkeletonRows rows={3} pitch={48} />
        </div>
      ) : (
        <div className="mt-5 grid grid-cols-1 gap-y-8 lg:grid-cols-[3fr_auto_4fr]">
          {CHAIN_BRANCHES.map(({ id, label }, index) => {
            const members = stages.filter((stage) => stage.branch === id);
            return (
              <Fragment key={id}>
                {index > 0 ? (
                  <div aria-hidden="true" className="hidden w-28 flex-col items-center pt-7 lg:flex">
                    <span className="h-px w-full border-t border-dashed border-stop/70" />
                    <span className="mono mt-2 text-center text-micro leading-tight text-stop">No link</span>
                  </div>
                ) : null}
                <section aria-label={label} className="min-w-0">
                  <Kicker>{label}</Kicker>
                  <div className="relative mt-2">
                    <span aria-hidden="true" className="absolute top-[11px] right-6 left-1.5 h-px bg-paint-4" />
                    <ol className="relative m-0 flex list-none gap-0 p-0">
                      {members.map((stage) => (
                        <Node key={stage.id} stage={stage} />
                      ))}
                    </ol>
                  </div>
                </section>
              </Fragment>
            );
          })}
        </div>
      )}
      <p className="mono mt-4 text-micro text-paint-3">Each stage shows its own record · links between stages not verified</p>
    </section>
  );
}

const COPYLEFT = /AGPL/i;

function short(tool: string): string {
  const parts = tool.split(/[./]/);
  return parts[parts.length - 1] ?? tool;
}

/** Every stage the analysis went through, as a flow with the time each one took. */
export function PipelineFlow({ record }: { record: AnalysisRecord }) {
  const stages = record.stages;
  const longest = Math.max(...stages.map((stage) => stage.elapsed_s ?? 0), 0);
  const copyleft = stages.filter((stage) => stage.licence && COPYLEFT.test(stage.licence));
  return (
    <section id="pipeline" className="scroll-mt-24">
      <SectionHead
        index="08"
        title="Pipeline"
        meta={
          copyleft.length > 0 ? (
            <span className="mono text-label text-amber">{`${copyleft.map((stage) => stage.stage).join(", ")}: ${copyleft[0]?.licence}`}</span>
          ) : (
            <span className="mono text-label text-paint-3">{`${stages.length} stages`}</span>
          )
        }
      />
      <ol className="m-0 mt-5 grid list-none grid-cols-2 gap-px border border-rule-1 bg-rule-1 p-0 sm:grid-cols-4 xl:grid-cols-7">
        {stages.map((stage, index) => (
          <PipelineNode key={stage.stage} stage={stage} index={index} longest={longest} />
        ))}
      </ol>
    </section>
  );
}

function PipelineNode({ stage, index, longest }: { stage: StageReceipt; index: number; longest: number }) {
  const copyleft = stage.licence ? COPYLEFT.test(stage.licence) : false;
  const share = longest > 0 && stage.elapsed_s != null ? Math.max(2, (stage.elapsed_s / longest) * 100) : 0;
  const detail = [
    stage.tool,
    `version ${stage.version}`,
    stage.licence ? `licence ${stage.licence}` : "licence not recorded",
    stage.device ? `device ${stage.device}` : null,
    stage.weights_sha256 ? `weights ${stage.weights_sha256.slice(0, 12)}` : null,
    stage.cached ? "cached" : "not cached",
    stage.cost_usd == null ? null : `cost ${stage.cost_usd.toFixed(4)} USD`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="flex min-w-0 flex-col gap-2 bg-asphalt-0 p-3" title={detail}>
      <span className="mono text-micro text-paint-4">{String(index + 1).padStart(2, "0")}</span>
      <span className="font-display text-head leading-none text-paint-1 capitalize">{stage.stage}</span>
      <span className="mono truncate text-micro text-paint-3">{short(stage.tool)}</span>
      <span className={`mono text-micro ${copyleft ? "text-amber" : "text-paint-3"}`}>{stage.licence ?? "No licence"}</span>
      <span className="mono mt-auto text-label text-paint-1">
        {stage.items_out == null ? "None" : stage.items_out.toLocaleString("en-US")}
        <span className="ml-1 text-micro text-paint-4">out</span>
      </span>
      <span className="relative h-1 bg-asphalt-2" aria-hidden="true">
        <span className="absolute inset-y-0 left-0 bg-paint-3" style={{ width: `${share}%` }} />
      </span>
      <span className="mono text-micro text-paint-3">{stage.elapsed_s == null ? "No time" : `${stage.elapsed_s.toFixed(2)} s`}</span>
    </li>
  );
}
