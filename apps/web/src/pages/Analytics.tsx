import { motion } from "motion/react";
import { useMemo, useState } from "react";
import {
  Absent,
  ButtonLink,
  DUR,
  EASE,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  Label,
  Ledger,
  Panel,
  Segmented,
  Skeleton,
  SkeletonRows,
  StatusMark,
  useElementSize,
  type LedgerColumn,
} from "../design";
import { Instrument, PageHead } from "../design/Instrument";
import { isCalibrated, measuredCoverage } from "../domain/footage";
import { countsNote, recordWords, withheldBy } from "../domain/gates";
import { enteringRule, entryBins, findMetric, fragmentCount, metricsUnder, movementCell, plainReason } from "../domain/metrics";
import { fitAcross, sitePlan } from "../domain/plan";
import { ARMS, ARM_NAMES, TURNS } from "../domain/vocabulary";
import { FlowChart } from "../instruments/FlowChart";
import { MovementLayer } from "../instruments/MovementLayer";
import { Readouts } from "../instruments/Readouts";
import { OutsideWindow } from "../instruments/Receipts";
import { SitePlan } from "../instruments/SitePlan";
import { WithheldBlock } from "../instruments/Withheld";
import type { LoadedAnalysis, Metric } from "../lib/analysis";
import { analysisLabel, useAnalysis } from "../state/analysis";
import { useSite } from "../state/resources";
import type { SiteGeometry } from "../twin/site";

type ArmFilter = "all" | (typeof ARMS)[number];

const ARM_OPTIONS = [
  { value: "all", label: "All arms" },
  ...ARMS.map((arm) => ({ value: arm, label: ARM_NAMES[arm].from })),
] as const;

const HEADLINE = ["vehicles.total", "flow.entering", "speed.mean_moving", "stopped.share"];
/** Junction metres across the shorter side of the movement plan. */
const MOVEMENT_PLAN_M = 86;

function Classes({ loaded }: { loaded: LoadedAnalysis }) {
  const rows = metricsUnder(loaded.record, "vehicles.class.")
    .slice()
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const total = rows.reduce((sum, row) => sum + (row.value ?? 0), 0);
  const first = rows[0];
  const fragments = fragmentCount(loaded.record);
  return (
    <Panel
      title="Classes"
      meta={first ? <EvidenceBadge kind={first.evidence_kind} note="Count and share" /> : undefined}
    >
      {rows.length === 0 ? (
        <p className="mt-3 text-paint-2">This analysis records no vehicle classes.</p>
      ) : (
        <ul className="m-0 mt-1 list-none p-0">
          {rows.map((row) => {
            const share = total > 0 && row.value != null ? row.value / total : null;
            return (
              <li
                key={row.metric_id}
                className="grid grid-cols-[6rem_minmax(0,1fr)_3.5rem_3.5rem] items-center gap-3 border-b border-rule-1 py-2.5"
              >
                <span className="mono truncate text-paint-1">{row.label}</span>
                <span aria-hidden="true" className="block h-1 bg-rule-1">
                  {share !== null ? (
                    <span className="block h-full bg-paint-2" style={{ width: `${Math.max(share * 100, 0.6)}%` }} />
                  ) : null}
                </span>
                <span className="mono text-right text-paint-1">
                  {row.value == null ? <Absent reason={row.reason ?? "Not recorded"} quiet /> : row.value.toLocaleString("en-US")}
                </span>
                <span className="mono text-right text-paint-3">
                  {share === null ? null : `${(share * 100).toFixed(1)} %`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {fragments ? (
        <p className="mt-2 leading-snug text-paint-3">
          {`Counted tracks only. The twin also draws ${fragments} fragments under 1 s, which the counts leave out.`}
        </p>
      ) : null}
    </Panel>
  );
}

function MovementPlan({ site, loaded, focus }: { site: SiteGeometry; loaded: LoadedAnalysis; focus: ArmFilter }) {
  const [box, size] = useElementSize<HTMLDivElement>();
  const arms = useMemo(() => sitePlan(site), [site]);
  const frame = useMemo(() => fitAcross(size.width, size.height, MOVEMENT_PLAN_M), [size]);
  const movements = loaded.record.movements ?? [];
  return (
    <motion.div layoutId="site-plan" transition={{ duration: DUR.slow, ease: EASE }} className="mt-3">
      <div ref={box} className="relative h-[420px] overflow-hidden">
        <SitePlan
          className="absolute inset-0"
          site={site}
          frame={frame}
          continuation
          overlay={(project) => (
            <MovementLayer
              site={site}
              arms={arms}
              project={project}
              movements={movements}
              focus={focus === "all" ? null : focus}
            />
          )}
        />
      </div>
    </motion.div>
  );
}

function MovementLedger({ loaded, focus }: { loaded: LoadedAnalysis; focus: ArmFilter }) {
  const movements = loaded.record.movements ?? [];
  const turns = TURNS.filter((turn) => movements.some((cell) => cell.turn === turn.id));
  const dim = (arm: string) => focus !== "all" && focus !== arm;

  const columns: readonly LedgerColumn<(typeof ARMS)[number]>[] = [
    {
      key: "arm",
      label: "Approach",
      span: 5 - turns.length,
      rowHeader: true,
      cell: (arm) => <span className={dim(arm) ? "text-paint-3" : "text-paint-1"}>{ARM_NAMES[arm].from}</span>,
    },
    ...turns.map((turn) => ({
      key: turn.id,
      label: turn.label,
      span: 1,
      align: "end" as const,
      cell: (arm: (typeof ARMS)[number]) => {
        const cell = movementCell(movements, arm, turn.id);
        if (!cell || cell.count == null) return <Absent reason={cell?.reason ?? "Not on record"} quiet />;
        return (
          <span className="whitespace-nowrap">
            <span className={dim(arm) ? "text-paint-3" : "text-paint-1"}>{cell.count}</span>
            {cell.reference_count == null ? null : (
              <span className="text-paint-3" aria-label={`NGSIM reference ${cell.reference_count}`}>
                {` / ${cell.reference_count}`}
              </span>
            )}
          </span>
        );
      },
    })),
  ];

  return (
    <Ledger
      className="mt-3"
      caption="Turning movement counts by approach. The turning check is pooled over held-out blocks, not per cell."
      columns={columns}
      rows={ARMS}
      rowKey={(arm) => arm}
    />
  );
}

function Ready({ loaded }: { loaded: LoadedAnalysis }) {
  const [arm, setArm] = useState<ArmFilter>("all");
  const site = useSite();
  const { state } = useAnalysis();
  const { record } = loaded;
  const picked = arm === "all" ? null : arm;
  const bins = useMemo(() => entryBins(record, picked), [record, picked]);
  const series = record.series?.[0];
  const movements = record.movements ?? [];
  const headline = HEADLINE.flatMap((id) => {
    const metric = findMetric(record, id);
    return metric ? [metric] : ([] as Metric[]);
  });
  const movementStatus = movements[0];
  const coverage = measuredCoverage(record);
  const flow = findMetric(record, "flow.entering");
  const countEvidence = flow?.evidence_kind ?? record.provenance.kind;
  const rule = enteringRule(record);
  const withheldFlow = series ? withheldBy(record, series) : null;
  // A series with no values is not a run with no traffic: an empty chart would say that it was.
  const absentFlow = series?.status === "not_available" ? series : null;
  const placeable = isCalibrated(record);
  const compared = movements.some((cell) => cell.reference_count != null);

  return (
    <div className="page pb-12">
      <PageHead
        title="Traffic Analytics"
        sub={<Label>{analysisLabel(state)}</Label>}
      />

      <Instrument className="border-t border-rule-2 pt-4 pb-8">
        <Readouts
          metrics={headline}
          record={record}
          className="grid-12 gap-y-4 [&>div]:col-span-6 lg:[&>div]:col-span-3"
        />
      </Instrument>

      <div className="grid-12 gap-y-10">
        <Instrument className="col-span-12 lg:col-span-7">
          <Panel
            title="Entering flow"
            meta={
              withheldFlow ? (
                <StatusMark status="gate_fail" />
              ) : absentFlow ? (
                <StatusMark status="not_available" />
              ) : series ? (
                <EvidenceBadge kind={series.evidence_kind} note={`Vehicles per ${series.bin_s} s`} />
              ) : undefined
            }
          >
            {withheldFlow ? (
              <WithheldBlock what="Entering flow" withheld={withheldFlow} note={countsNote(record)} />
            ) : absentFlow ? (
              <EmptyState className="mt-3" title="No entering flow for this run" reason={plainReason(absentFlow.reason)} />
            ) : series ? (
              <FlowChart bins={bins} binS={series.bin_s} picked={picked ? ARM_NAMES[picked].from : null} />
            ) : (
              <p className="mt-3 text-paint-2">This analysis records no flow series.</p>
            )}
            {rule && !withheldFlow ? <p className="mt-2 leading-snug text-paint-3">{rule}</p> : null}
            {coverage && !withheldFlow ? (
              <div className="mt-2">
                <OutsideWindow coverage={coverage} />
              </div>
            ) : null}
          </Panel>
        </Instrument>
        <Instrument className="col-span-12 lg:col-span-5">
          <Classes loaded={loaded} />
        </Instrument>

        <Instrument className="col-span-12 lg:col-span-7">
          <Panel
            title="Turning movements"
            meta={
              <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
                <Segmented label="Approach arm" options={ARM_OPTIONS} value={arm} onChange={setArm} />
                {movementStatus ? <StatusMark status={movementStatus.status} /> : null}
              </div>
            }
          >
            {!placeable ? (
              <EmptyState className="mt-3" title="No turning movements for this run" reason={plainReason(flow?.reason)} />
            ) : site.status === "ready" ? (
              <MovementPlan site={site.data} loaded={loaded} focus={arm} />
            ) : site.status === "error" ? (
              <ErrorState what="Couldn't load the site plan." className="mt-3" onRetry={site.retry} />
            ) : (
              <Skeleton height={420} className="mt-3" />
            )}
            {placeable ? (
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
                <EvidenceBadge kind={countEvidence} note="Counts, per movement" />
                <EvidenceBadge kind="approximated" note="Lanes derived from trajectories" />
                <EvidenceBadge kind="assumed" note="Ribbon paths. Width is the count" />
                {coverage ? <OutsideWindow coverage={coverage} /> : null}
              </div>
            ) : null}
          </Panel>
        </Instrument>
        <Instrument className="col-span-12 lg:col-span-5">
          <Panel
            title="Movement counts"
            meta={
              coverage ? (
                <OutsideWindow coverage={coverage} />
              ) : (
                <Label>{compared ? "Camera count / NGSIM reference" : "Vehicles, by approach and turn"}</Label>
              )
            }
          >
            {movements.length > 0 ? (
              <MovementLedger loaded={loaded} focus={arm} />
            ) : (
              <p className="mt-3 text-paint-2">This analysis records no turning movements.</p>
            )}
            {compared ? (
              <p className="mt-2 leading-snug text-paint-3">
                Each cell is this camera run's count, then the published NGSIM count for the same movement.
              </p>
            ) : null}
            {withheldFlow ? (
              <p className="mt-2 leading-snug text-paint-3">Row sums here are not entering counts: see Entering flow.</p>
            ) : null}
          </Panel>
        </Instrument>
        <Instrument className="col-span-12">
          <Panel title="Read with care">
            <ul className="m-0 mt-2 list-none columns-1 gap-x-gutter p-0 md:columns-2">
              {record.disclosures.map((line) => (
                <li key={line} className="break-inside-avoid border-b border-rule-1 py-2 leading-snug text-paint-2">
                  {recordWords(line, { hideIds: true })}
                </li>
              ))}
            </ul>
          </Panel>
        </Instrument>
      </div>
    </div>
  );
}

export function Analytics() {
  const { state, retry } = useAnalysis();
  if (state.status === "ready") return <Ready loaded={state.loaded} />;
  return (
    <div className="page pb-12">
      <PageHead title="Traffic Analytics" sub={<Label>{analysisLabel(state)}</Label>} />
      <Instrument className="border-t border-rule-2 pt-6">
        {state.status === "error" ? (
          <ErrorState size="stage" what="Couldn't load the counts." lastGood={state.lastGood} onRetry={retry} />
        ) : state.status === "loading" ? (
          <SkeletonRows rows={8} />
        ) : (
          <EmptyState
            size="stage"
            title="No analysis loaded"
            reason="Flow, turning movements and classes are counted from the tracks of an analysis. No figure appears here until one supplies it, and each carries its evidence kind."
            action={<ButtonLink to="/intersections">Choose an analysis</ButtonLink>}
          />
        )}
      </Instrument>
    </div>
  );
}
