import { motion, useReducedMotion } from "motion/react";
import { EvidenceBadge, SkeletonRows } from "../design";
import { EASE } from "../design/motion";
import { baselineLabel, recommendationAllowed } from "../domain/chain";
import { SIMULATOR, comparedTimeline, measureName, signedSeconds } from "../domain/simulation";
import type { DecisionSummary, EnsembleMemberVerdict, SimulatedComparison } from "../lib/analysis";
import { CheckLamp, Kicker, SectionHead } from "./parts";

/** A member as the record holds it: the per-timeline lists ride beside the typed fields. */
interface MemberCell extends EnsembleMemberVerdict {
  worsens?: string[];
  confirmed?: string[];
  supported?: boolean;
  blocked_by?: string[];
}

interface MemberInfo {
  member: string;
  version: string;
  required: boolean;
  gate_passed: boolean;
  baseline_assessment?: string;
}

const ARMS = ["N", "E", "S", "W"] as const;
const QUEUE = /^(max|mean)_queue_m\.([NESW])$/;

/** Where a measure sits in a cell: delay and throughput in the first column, queues by arm after. */
function slot(metric: string): { row: number; col: number } {
  if (metric === "mean_delay_s") return { row: 1, col: 1 };
  if (metric === "throughput_veh_per_h") return { row: 2, col: 1 };
  const queue = metric.match(QUEUE);
  if (queue) return { row: queue[1] === "max" ? 1 : 2, col: 2 + ARMS.indexOf(queue[2] as (typeof ARMS)[number]) };
  return { row: 3, col: 1 };
}

type Effect = "worse" | "better" | "neither";

const EFFECT_FILL: Record<Effect, string> = {
  worse: "var(--stop)",
  better: "var(--go)",
  neither: "transparent",
};

function effectOf(cell: MemberCell, metric: string): Effect {
  if (cell.worsens?.includes(metric)) return "worse";
  if (cell.confirmed?.includes(metric)) return "better";
  return "neither";
}

function Glyph({ cell, measures, delay }: { cell: MemberCell; measures: readonly string[]; delay: number }) {
  const still = useReducedMotion();
  return (
    <span
      aria-hidden="true"
      className="grid gap-[3px]"
      style={{ gridTemplateColumns: "14px 6px repeat(4, 14px)", gridAutoRows: "14px" }}
    >
      {measures.map((metric, index) => {
        const { row, col } = slot(metric);
        const effect = effectOf(cell, metric);
        return (
          <motion.span
            key={metric}
            title={`${measureName(metric)}: ${effect === "neither" ? "no clear change" : effect} on ${cell.member}`}
            style={{
              gridRow: row,
              gridColumn: col === 1 ? 1 : col + 1,
              background: EFFECT_FILL[effect],
              border: `1px solid ${effect === "neither" ? "var(--paint-4)" : EFFECT_FILL[effect]}`,
              boxShadow: effect === "worse" ? "0 0 8px rgba(240,82,79,0.45)" : "none",
            }}
            initial={still ? false : { opacity: 0, scale: 0.4 }}
            whileInView={{ opacity: effect === "better" ? 0.85 : 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.35, ease: EASE, delay: delay + index * 0.018 }}
          />
        );
      })}
    </span>
  );
}

/** Every candidate on every signal timeline, measure by measure: the evidence behind the verdict. */
export function SimulationMatrix({ decision }: { decision: DecisionSummary }) {
  const measures = [...new Set(decision.metrics.map((row) => row.metric_name))];
  const members = (decision.ensemble.members as MemberInfo[]).filter((member) => member.required);
  const seeds = decision.simulation.seeds.length;
  const candidates = decision.ensemble.candidates;
  return (
    <section id="simulation" className="scroll-mt-24">
      <SectionHead
        index="03"
        title="Simulation"
        meta={
          <EvidenceBadge
            kind={decision.simulation.evidence_kind}
            note={`${SIMULATOR}, ${seeds} seeds on each of ${members.length} signal timelines`}
          />
        }
      />
      <p className="mono mt-3 text-label text-paint-3">
        <span className="text-paint-1">{`${candidates.length} candidates × ${members.length} timelines × ${seeds} seeds`}</span>
        {` · ${SIMULATOR} · same replayed vehicles`}
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[40rem] border-collapse">
          <caption className="sr-only">Each candidate on each signal timeline: which measures got worse or better</caption>
          <thead>
            <tr>
              <th className="w-[9.5rem] pb-2 text-left align-bottom">
                <Kicker>Candidate</Kicker>
              </th>
              {members.map((member) => (
                <th key={member.member} className="pb-2 pl-3 text-left align-bottom font-normal">
                  <span className="mono block text-label text-paint-1">{member.member}</span>
                  <span className="mono block text-micro text-paint-3">
                    {`${member.version}${member.baseline_assessment ? ` · ${baselineLabel(member.baseline_assessment).toLowerCase()}` : ""}`}
                  </span>
                </th>
              ))}
              <th className="pb-2 pl-3 text-right align-bottom">
                <Kicker>On all {members.length}</Kicker>
              </th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((verdict, row) => {
              const cells = verdict.members as MemberCell[];
              return (
                <tr key={verdict.candidate} className="border-t border-rule-1">
                  <th scope="row" className="py-3 pr-3 text-left align-middle font-normal">
                    <span className="block font-display text-[30px] leading-none font-medium text-paint-1">{verdict.candidate}</span>
                    <span className="mono mt-1 block text-micro tracking-[0.08em] text-stop uppercase">
                      {verdict.verdict.replace(/_/g, " ")}
                    </span>
                  </th>
                  {members.map((member, col) => {
                    const cell = cells.find((entry) => entry.member === member.member);
                    if (!cell) {
                      return (
                        <td key={member.member} className="py-3 pl-3 align-middle">
                          <span className="mono text-label text-paint-4">Not run</span>
                        </td>
                      );
                    }
                    const worse = cell.worsens?.length ?? 0;
                    return (
                      <td
                        key={member.member}
                        className="py-3 pl-3 align-middle"
                        aria-label={`${verdict.candidate} on ${member.member}: ${worse} measures worse, ${cell.confirmed?.length ?? 0} better, mean delay ${signedSeconds(cell.mean_delay_difference_s)} s`}
                      >
                        <span className="inline-flex items-center gap-3 border border-rule-1 bg-asphalt-1 px-2.5 py-2">
                          <Glyph cell={cell} measures={measures} delay={0.1 + (row * members.length + col) * 0.05} />
                          <span className="flex flex-col">
                            <span
                              className={`mono text-label leading-none ${cell.mean_delay_difference_s > 0 ? "text-stop" : "text-paint-2"}`}
                              title="Mean delay against baseline, s. Above zero is worse."
                            >
                              {signedSeconds(cell.mean_delay_difference_s)}
                            </span>
                            <span className="mono mt-1 text-micro leading-none text-paint-4">Δ delay s</span>
                          </span>
                        </span>
                      </td>
                    );
                  })}
                  <td className="py-3 pl-3 text-right align-middle">
                    <span className="mono block text-label text-stop" title={verdict.robust_harm.map(measureName).join(", ")}>
                      {`${verdict.robust_harm.length} worse`}
                    </span>
                    <span className="mono block text-label text-paint-3" title={verdict.common_confirmed.map(measureName).join(", ") || "None"}>
                      {`${verdict.common_confirmed.length} better`}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <MatrixKey />
      <BaselineFigures decision={decision} measures={measures} />
    </section>
  );
}

function MatrixKey() {
  const swatch = (effect: Effect) => (
    <span
      aria-hidden="true"
      className="inline-block size-2.5"
      style={{ background: EFFECT_FILL[effect], border: `1px solid ${effect === "neither" ? "var(--paint-4)" : EFFECT_FILL[effect]}` }}
    />
  );
  return (
    <div className="mono mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-micro text-paint-3">
      <span className="inline-flex items-center gap-1.5">{swatch("worse")}Worse</span>
      <span className="inline-flex items-center gap-1.5">{swatch("better")}Better</span>
      <span className="inline-flex items-center gap-1.5">{swatch("neither")}No clear change</span>
      <span className="text-paint-4">|</span>
      <span>Cell: delay, throughput · queue N E S W, longest over mean</span>
      <span className="text-paint-4">|</span>
      <span>Mean of paired seeds · W and E not trusted alone</span>
    </div>
  );
}

function figure(value: number, unit: string): string {
  return unit === "veh/h" ? Math.round(value).toLocaleString("en-US") : value.toFixed(1);
}

const VERDICT_TONE: Record<SimulatedComparison["verdict"], string> = {
  worsens: "text-stop",
  improves: "text-go",
  uninformative: "text-paint-4",
  inconclusive: "text-paint-3",
};

/** The baseline and candidate means on the one timeline the record holds them for. */
function BaselineFigures({ decision, measures }: { decision: DecisionSummary; measures: readonly string[] }) {
  const timeline = comparedTimeline(decision);
  const candidates = decision.ensemble.candidates.map((entry) => entry.candidate);
  if (decision.metrics.length === 0) return null;
  return (
    <details className="group mt-4 border-t border-rule-1 pt-3">
      <summary className="mono cursor-pointer list-none text-label text-paint-3 hover:text-paint-1">
        <span className="mr-2 inline-block transition-transform group-open:rotate-90">›</span>
        {`Baseline vs candidate means${timeline ? `, ${timeline}` : ""}`}
      </summary>
      <div className="mt-3 overflow-x-auto">
        <table className="mono w-full min-w-[34rem] border-collapse text-label">
          <thead>
            <tr className="text-micro tracking-[0.08em] text-paint-3 uppercase">
              <th className="py-1.5 text-left font-normal">Measure</th>
              <th className="py-1.5 text-right font-normal">Baseline</th>
              {candidates.map((id) => (
                <th key={id} className="py-1.5 text-right font-normal">
                  {id}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {measures.map((metric) => {
              const rows = decision.metrics.filter((row) => row.metric_name === metric);
              const base = rows[0];
              if (!base) return null;
              return (
                <tr key={metric} className="border-t border-rule-1">
                  <td className="py-1.5 text-paint-2" title={metric}>
                    {measureName(metric)}
                  </td>
                  <td className="py-1.5 text-right text-paint-2">{`${figure(base.baseline_mean, base.unit)} ${base.unit}`}</td>
                  {candidates.map((id) => {
                    const row = rows.find((entry) => entry.candidate === id);
                    return (
                      <td key={id} className={`py-1.5 text-right ${row ? VERDICT_TONE[row.verdict] : "text-paint-4"}`} title={row?.verdict}>
                        {row ? figure(row.candidate_mean, row.unit) : "None"}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/** Refusing is the product working, so the verdict is the largest thing in this section. */
export function DecisionVerdict({
  decision,
  waiting,
  linkedToCamera,
}: {
  decision: DecisionSummary | null;
  waiting: boolean;
  linkedToCamera: boolean;
}) {
  const status = decision?.baseline.status ?? null;
  const allowed = status ? recommendationAllowed(status) : false;
  const recommended = decision?.outcome.recommended_intervention_id ?? null;
  const timelines = decision?.ensemble.members.filter((member) => member.required).length ?? 0;
  const supported = decision?.ensemble.candidates.filter((entry) => entry.verdict !== "no_support").length ?? 0;
  const total = decision?.ensemble.candidates.length ?? 0;
  const everyCellWorse =
    decision !== null &&
    decision.ensemble.candidates.length > 0 &&
    decision.ensemble.candidates.every((entry) => (entry.members as MemberCell[]).every((cell) => (cell.worsens?.length ?? 0) > 0));
  const verdict = !decision ? "Not assessed" : allowed && recommended ? `Recommend ${recommended}` : "Nothing recommended";
  const reason = !decision
    ? "No decision record loaded."
    : everyCellWorse
      ? `Every candidate worsened a measure on all ${timelines} timelines.`
      : decision.outcome.recommendation;
  return (
    <section id="decision" className="scroll-mt-24">
      <SectionHead
        index="02"
        title="Decision"
        meta={decision ? <EvidenceBadge kind={decision.outcome.evidence_kind} note="Computed under a recorded configuration" /> : null}
      />
      {waiting ? (
        <div className="mt-4">
          <SkeletonRows rows={4} pitch={48} />
        </div>
      ) : (
        <div className="mt-5 grid grid-cols-1 gap-x-10 gap-y-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <div className="relative overflow-hidden border border-rule-2 bg-asphalt-1 bg-[radial-gradient(var(--rule-1)_1px,transparent_1px)] [background-size:16px_16px] px-6 pt-5 pb-6">
            <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[3px] bg-amber" />
            <Kicker tone="amber">Verdict</Kicker>
            <p className="mt-3 font-display text-[clamp(44px,5.2vw,76px)] leading-[0.92] font-medium tracking-[-0.02em] text-paint-1">
              {verdict}
            </p>
            <p className="mt-4 max-w-[34rem] text-body leading-snug text-paint-2">{reason}</p>
            {decision && !linkedToCamera ? (
              <p className="mono mt-4 inline-flex items-center gap-2 border border-rule-2 px-2 py-1 text-micro tracking-[0.06em] text-paint-2 uppercase">
                <span aria-hidden="true" className="size-1.5 bg-ev-observed" />
                From published NGSIM trajectories · not this camera run
              </p>
            ) : null}
          </div>
          <div className="flex flex-col gap-8">
            <div>
              <Kicker>To recommend, both must hold</Kicker>
              <ul className="m-0 mt-3 flex list-none flex-col gap-0 p-0">
                <li className="flex items-baseline justify-between gap-4 border-t border-rule-1 py-3">
                  <CheckLamp ok={allowed}>Baseline validated</CheckLamp>
                  <span className="mono text-label text-paint-2">{status ? baselineLabel(status) : "Not assessed"}</span>
                </li>
                <li className="flex items-baseline justify-between gap-4 border-y border-rule-1 py-3">
                  <CheckLamp ok={supported > 0}>{`Clean on all ${timelines || "required"} timelines`}</CheckLamp>
                  <span className="mono text-label text-paint-2">{decision ? `${supported} of ${total}` : "None"}</span>
                </li>
              </ul>
            </div>
            <Fork lit={!decision ? null : allowed && recommended ? "recommend" : "abstain"} />
          </div>
        </div>
      )}
    </section>
  );
}

/** The branch is fixed in code. A branch lights only when a record takes it. */
function Fork({ lit }: { lit: "recommend" | "abstain" | null }) {
  const on = (branch: "recommend" | "abstain") => lit === branch;
  const stroke = (branch: "recommend" | "abstain") => (on(branch) ? "var(--amber)" : "var(--paint-4)");
  return (
    <div className="flex items-center gap-4" aria-label={lit ? `Branch taken: ${lit === "abstain" ? "Abstain" : "Recommend"}` : "No branch taken"}>
      <svg width="120" height="64" viewBox="0 0 120 64" aria-hidden="true" className="shrink-0">
        <path d="M0 32 H40" stroke={lit ? "var(--amber)" : "var(--paint-4)"} strokeWidth="2" fill="none" />
        <path d="M40 32 C60 32 60 10 82 10 H120" stroke={stroke("recommend")} strokeWidth={on("recommend") ? 2 : 1} strokeDasharray={on("recommend") ? "0" : "3 3"} fill="none" />
        {on("abstain") ? (
          <motion.path
            d="M40 32 C60 32 60 54 82 54 H120"
            stroke="var(--amber)"
            strokeWidth={2}
            fill="none"
            initial={{ pathLength: 0 }}
            whileInView={{ pathLength: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 1.1, ease: EASE, delay: 0.3 }}
          />
        ) : (
          <path d="M40 32 C60 32 60 54 82 54 H120" stroke={stroke("abstain")} strokeWidth={1} strokeDasharray="3 3" fill="none" />
        )}
        <rect x="36" y="28" width="8" height="8" fill="var(--asphalt-0)" stroke={lit ? "var(--amber)" : "var(--paint-4)"} />
      </svg>
      <div className="flex h-16 flex-col justify-between py-px">
        <span className={`font-display text-head leading-[18px] ${on("recommend") ? "text-paint-1" : "text-paint-4"}`}>Recommend</span>
        <span className={`font-display text-head leading-[18px] ${on("abstain") ? "text-amber" : "text-paint-4"}`}>Abstain</span>
      </div>
    </div>
  );
}
