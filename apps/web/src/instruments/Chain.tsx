import { useMemo } from "react";
import { Link } from "react-router-dom";
import { EvidenceBadge, Label, StatusMark, STATUS } from "../design";
import { CHAIN_BRANCHES, decisionFor, readChain, type ChainBranch, type ChainReading } from "../domain/chain";
import type { LoadedAnalysis } from "../lib/analysis";
import { useDecision, useSite } from "../state/resources";

/** The chain for whatever is loaded. With nothing loaded every stage reads as having no record. */
export function useChain(loaded: LoadedAnalysis | null): ChainReading[] {
  const decision = useDecision();
  const site = useSite();
  const record = loaded?.record ?? null;
  const decisionData = decision.status === "ready" ? decision.data : null;
  const siteData = site.status === "ready" ? site.data : null;
  return useMemo(
    () =>
      readChain({
        record,
        site: record ? siteData : null,
        decision: record && decisionData ? decisionFor(record, decisionData) : null,
      }),
    [record, decisionData, siteData],
  );
}

const hasRecord = (stage: ChainReading) => stage.status !== "not_available";

function inBranch(stages: readonly ChainReading[], branch: ChainBranch): ChainReading[] {
  return stages.filter((stage) => stage.branch === branch);
}

function RailStage({ stage }: { stage: ChainReading }) {
  return (
    <li
      className={`relative border-t py-3 pr-3 lg:border-b-0 lg:pb-0 ${
        hasRecord(stage) ? "border-paint-4" : "border-dashed border-paint-4"
      }`}
    >
      <span
        aria-hidden="true"
        className={`absolute -top-[4px] left-0 hidden size-[7px] border lg:block ${
          hasRecord(stage) ? "border-paint-2 bg-paint-2" : "border-paint-4 bg-asphalt-0"
        }`}
      />
      <Link
        to={stage.to}
        title={stage.detail}
        aria-label={`${stage.label}: ${stage.brief}. ${STATUS[stage.status].label}. Read its evidence.`}
        className="group block"
      >
        {/* One column of seven is narrow at 1280: the name steps down rather than break mid-word. */}
        <span className="block font-display text-head leading-tight [overflow-wrap:normal] text-paint-1 underline decoration-rule-2 underline-offset-4 transition-colors duration-150 group-hover:decoration-amber xl:text-body min-[1400px]:text-head">
          {stage.label}
        </span>
        <span className="mt-2 flex items-center gap-2 lg:block">
          <StatusMark status={stage.status} decorative />
          <Label className="block lg:mt-1.5">{stage.brief}</Label>
        </span>
      </Link>
    </li>
  );
}

/**
 * The compact chain: two branches side by side, with a gap where the line stops. The gap is the
 * claim. Camera tracks did not feed the decision, so no line is drawn from one to the other.
 */
export function ChainRail({ stages }: { stages: readonly ChainReading[] }) {
  return (
    <div className="mt-2 grid grid-cols-1 gap-x-8 gap-y-3 lg:grid-cols-[3fr_4fr]">
      {CHAIN_BRANCHES.map(({ id, label }) => {
        const members = inBranch(stages, id);
        return (
          <section key={id} aria-label={label}>
            <Label className="mb-2.5 block">{label}</Label>
            <ol
              className="m-0 grid list-none grid-cols-1 p-0 lg:[grid-template-columns:repeat(var(--stations),minmax(0,1fr))]"
              style={{ ["--stations" as string]: members.length }}
            >
              {members.map((stage) => (
                <RailStage key={stage.id} stage={stage} />
              ))}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

function ListStage({ stage, last }: { stage: ChainReading; last: boolean }) {
  return (
    <li className="grid grid-cols-[20px_minmax(0,1fr)_auto] items-start gap-x-3 py-3">
      <span aria-hidden="true" className="relative flex justify-center self-stretch pt-1.5">
        <span
          className={`relative z-10 size-[9px] border ${
            hasRecord(stage) ? "border-paint-2 bg-paint-2" : "border-paint-4 bg-asphalt-0"
          }`}
        />
        {last ? null : (
          <span
            className={`absolute top-3 bottom-[-30px] left-1/2 border-l border-paint-4 ${
              hasRecord(stage) ? "" : "border-dashed"
            }`}
          />
        )}
      </span>
      <span className="min-w-0">
        <Link
          to={stage.to}
          className="block font-display text-head leading-tight text-paint-1 underline decoration-rule-2 underline-offset-4 hover:decoration-amber"
        >
          {stage.label}
        </Link>
        <span className="mt-0.5 block leading-snug text-paint-2">
          {hasRecord(stage) ? stage.detail : stage.holds}
        </span>
        {stage.evidence ? (
          <span className="mt-1 block">
            <EvidenceBadge kind={stage.evidence} />
          </span>
        ) : null}
      </span>
      <span className="flex flex-col items-end gap-1 pt-1">
        <StatusMark status={stage.status} bare note={stage.detail} />
        <Label>{STATUS[stage.status].label}</Label>
      </span>
    </li>
  );
}

/** The full chain: what each stage holds, how far it was checked and what kind of evidence it is. */
export function ChainList({ stages }: { stages: readonly ChainReading[] }) {
  return (
    <div className="mt-1">
      {CHAIN_BRANCHES.map(({ id, label }) => {
        const members = inBranch(stages, id);
        return (
          <section key={id} aria-label={label} className="mt-3 first:mt-2">
            <Label tone="body" className="block">
              {label}
            </Label>
            <ol className="m-0 list-none p-0">
              {members.map((stage, index) => (
                <ListStage key={stage.id} stage={stage} last={index === members.length - 1} />
              ))}
            </ol>
          </section>
        );
      })}
    </div>
  );
}
