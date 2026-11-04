import { useMemo, useState } from "react";
import {
  ButtonLink,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  Label,
  Panel,
  Segmented,
  SkeletonRows,
  StatusMark,
} from "../design";
import { Instrument, PageHead } from "../design/Instrument";
import { isCalibrated, measuredCoverage } from "../domain/footage";
import { recordWords, withheldBy } from "../domain/gates";
import { enteringRule, entryBins, findMetric, plainReason } from "../domain/metrics";
import { ARMS, ARM_NAMES } from "../domain/vocabulary";
import { AgreementPlot } from "../analytics/AgreementPlot";
import { JunctionFlows, type Pairing } from "../analytics/JunctionFlows";
import { Kpis } from "../analytics/Kpis";
import { MovementRank } from "../analytics/MovementRank";
import { splitNote } from "../analytics/notes";
import { OdMatrix } from "../analytics/OdMatrix";
import { RunStrip } from "../analytics/RunStrip";
import { WithheldBand } from "../analytics/WithheldBand";
import { FlowChart } from "../instruments/FlowChart";
import { OutsideWindow } from "../instruments/Receipts";
import type { LoadedAnalysis, Metric } from "../lib/analysis";
import { analysisLabel, useAnalysis } from "../state/analysis";

type ArmFilter = "all" | (typeof ARMS)[number];

const ARM_OPTIONS = [
  { value: "all", label: "All arms" },
  ...ARMS.map((arm) => ({ value: arm, label: ARM_NAMES[arm].from })),
] as const;

const HEADLINE = ["vehicles.total", "flow.entering", "speed.mean_moving", "stopped.share"];
const POOLED = "Turning check pooled over held-out blocks, not per movement.";
const cameraWords = (label: string) => label.replace(/^Placement\b/, "Camera placement");

function Limits({ lines }: { lines: readonly string[] }) {
  if (lines.length === 0) return null;
  return (
    <details className="group border-t border-rule-2 pt-3">
      <summary className="flex cursor-pointer list-none items-baseline gap-3">
        <span className="mono inline-block w-3 text-paint-3 transition-transform duration-200 group-open:rotate-90">›</span>
        <span className="text-head leading-6 font-medium">Limits</span>
        <Label>{`${lines.length} notes from the record`}</Label>
      </summary>
      <ul className="m-0 mt-3 list-none columns-1 gap-x-gutter p-0 md:columns-2">
        {lines.map((line) => (
          <li key={line} className="break-inside-avoid border-b border-rule-1 py-2 leading-snug text-paint-2">
            {recordWords(line, { hideIds: true })}
          </li>
        ))}
      </ul>
    </details>
  );
}

function Ready({ loaded }: { loaded: LoadedAnalysis }) {
  const [arm, setArm] = useState<ArmFilter>("all");
  const [active, setActive] = useState<string | null>(null);
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
  const placeable = isCalibrated(record) && movements.some((cell) => cell.count != null);
  const camera = record.data_origin === "camera_cv";
  const pairing: Pairing = {
    main: camera ? "Camera" : "NGSIM",
    paired: camera && movements.some((cell) => cell.reference_count != null),
    kind: countEvidence,
  };

  return (
    <div className="page pb-12">
      <PageHead
        title="Traffic Analytics"
        sub={<Label>{analysisLabel(state)}</Label>}
        meta={<RunStrip record={record} />}
      />

      <Instrument>
        <Kpis metrics={headline} record={record} />
      </Instrument>

      <div className="grid-12 mt-10 gap-y-10">
        <Instrument className="col-span-12 lg:col-span-8">
          <Panel
            title="Turning movements"
            meta={
              <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
                <Segmented label="Approach arm" options={ARM_OPTIONS} value={arm} onChange={setArm} />
                {movementStatus ? (
                  <StatusMark
                    status={movementStatus.status}
                    bare
                    note={movementStatus.status === "gate_pass" ? POOLED : movementStatus.reason}
                  />
                ) : null}
              </div>
            }
          >
            {placeable ? (
              <>
                <div className="mt-3">
                  <JunctionFlows
                    movements={movements}
                    pairing={pairing}
                    focus={picked}
                    active={active}
                    onActive={setActive}
                  />
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1">
                  <EvidenceBadge kind={countEvidence} note={`${pairing.main} count`} />
                  {pairing.paired ? (
                    <span className="mono inline-flex items-center gap-2 text-paint-2">
                      <span aria-hidden="true" className="inline-block h-2 w-5 bg-ev-observed/25" />
                      NGSIM published
                    </span>
                  ) : null}
                  <EvidenceBadge kind="assumed" note="Schematic paths" />
                  {coverage ? <OutsideWindow coverage={{ ...coverage, label: cameraWords(coverage.label) }} /> : null}
                </div>
              </>
            ) : (
              <EmptyState className="mt-3" title="No turning movements" reason={plainReason(flow?.reason)} />
            )}
          </Panel>
        </Instrument>

        <Instrument className="col-span-12 lg:col-span-4">
          <Panel
            title="Movement counts"
            meta={<Label>{pairing.paired ? "Camera · NGSIM" : `${pairing.main} count`}</Label>}
          >
            {placeable ? (
              <>
                <OdMatrix movements={movements} pairing={pairing} focus={picked} active={active} onActive={setActive} />
                {pairing.paired ? (
                  <AgreementPlot
                    record={record}
                    movements={movements}
                    pairing={pairing}
                    active={active}
                    onActive={setActive}
                  />
                ) : (
                  <MovementRank
                    movements={movements}
                    pairing={pairing}
                    focus={picked}
                    active={active}
                    onActive={setActive}
                  />
                )}
              </>
            ) : (
              <p className="mt-3 text-paint-2">No movements recorded.</p>
            )}
          </Panel>
        </Instrument>

        <Instrument className="col-span-12">
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
            {withheldFlow && series ? (
              <WithheldBand series={series} withheld={withheldFlow} note={splitNote(record)} />
            ) : absentFlow ? (
              <EmptyState className="mt-3" title="No entering flow" reason={plainReason(absentFlow.reason)} />
            ) : series ? (
              <>
                <FlowChart
                  bins={bins}
                  binS={series.bin_s}
                  picked={picked ? ARM_NAMES[picked].from : null}
                  kind={series.evidence_kind}
                  status={series.status}
                />
                {rule ? (
                  <details className="group mt-3">
                    <summary className="mono cursor-pointer list-none text-paint-3 hover:text-paint-1">
                      <span className="inline-block w-4 transition-transform duration-200 group-open:rotate-90">›</span>
                      How entries are counted
                    </summary>
                    <p className="m-0 mt-2 max-w-[44rem] pl-4 leading-snug text-paint-2">{rule}</p>
                  </details>
                ) : null}
              </>
            ) : (
              <p className="mt-3 text-paint-2">No flow series recorded.</p>
            )}
          </Panel>
        </Instrument>

        <Instrument className="col-span-12">
          <Limits lines={record.disclosures} />
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
            reason="Counts come from an analysis. Each carries its evidence kind."
            action={<ButtonLink to="/intersections">Choose an analysis</ButtonLink>}
          />
        )}
      </Instrument>
    </div>
  );
}
