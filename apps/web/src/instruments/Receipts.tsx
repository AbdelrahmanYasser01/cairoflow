import { Fragment } from "react";
import { Absent, EmptyState, EvidenceBadge, Field, Label, Ledger, Panel, StatusMark, Tooltip, type LedgerColumn } from "../design";
import { analysisSpan, measuredCoverage, type MeasuredCoverage } from "../domain/footage";
import {
  comparatorWord,
  figuresWithheld,
  gateGloss,
  gateLimit,
  gateName,
  gateReading,
  gatesPassed,
  limitWords,
  measuredWords,
  recordWords,
  withheldBy,
} from "../domain/gates";
import type { AnalysisRecord, GateOutcome, StageReceipt } from "../lib/analysis";

const COPYLEFT = /AGPL/i;
const NONE = "None recorded";

const whole = (value: number | null | undefined) =>
  value === null || value === undefined ? <Absent reason={NONE} quiet /> : value.toLocaleString("en-US");

/** Where a run's window falls against the windows a check measured. */
export function OutsideWindow({ coverage }: { coverage: MeasuredCoverage }) {
  return (
    <Tooltip content={coverage.note}>
      <span tabIndex={0} className="mono inline-flex items-baseline gap-1.5 text-amber">
        <span aria-hidden="true" className="inline-block size-1.5 translate-y-[-1px] bg-amber" />
        {coverage.label}
      </span>
    </Tooltip>
  );
}

/** A dotted name that can wrap at its dots, and copies out unchanged. */
function DottedName({ name }: { name: string }) {
  const parts = name.split(".");
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={`${index}-${part}`}>
          {part}
          {index < parts.length - 1 ? (
            <>
              .<wbr />
            </>
          ) : null}
        </Fragment>
      ))}
    </>
  );
}

function licenceCell(stage: StageReceipt) {
  if (!stage.licence) return <Absent reason={NONE} quiet />;
  return COPYLEFT.test(stage.licence) ? (
    <span className="font-medium text-amber">{stage.licence}</span>
  ) : (
    stage.licence
  );
}

const STAGE_COLUMNS: readonly LedgerColumn<StageReceipt>[] = [
  { key: "stage", label: "Stage", span: 2, rowHeader: true, cell: (row) => row.stage },
  {
    key: "tool",
    label: "Tool",
    span: 5,
    cell: (row) => (
      <span className="[overflow-wrap:anywhere]">
        <DottedName name={row.tool} />
        {row.weights_sha256 ? (
          <span className="block text-paint-3">{`Weights ${row.weights_sha256.slice(0, 12)}`}</span>
        ) : null}
      </span>
    ),
  },
  { key: "version", label: "Version", span: 1, cell: (row) => <span className="[overflow-wrap:anywhere]">{row.version}</span> },
  { key: "licence", label: "Licence", span: 2, cell: licenceCell },
  { key: "device", label: "Device", span: 1, cell: (row) => row.device ?? <Absent reason={NONE} quiet /> },
  { key: "frames", label: "Frames in", span: 1, align: "end", cell: (row) => whole(row.frames_in) },
  { key: "items", label: "Items out", span: 1, align: "end", cell: (row) => whole(row.items_out) },
  {
    key: "elapsed",
    label: "Elapsed, s",
    span: 2,
    align: "end",
    cell: (row) => (row.elapsed_s == null ? <Absent reason={NONE} quiet /> : row.elapsed_s.toFixed(2)),
  },
  { key: "cached", label: "Cached", span: 1, cell: (row) => (row.cached ? "Yes" : "No") },
  {
    key: "cost",
    label: "Cost, USD",
    span: 1,
    align: "end",
    cell: (row) => (row.cost_usd == null ? <Absent reason={NONE} quiet /> : row.cost_usd.toFixed(4)),
  },
];

/** Every stage this analysis went through, as the pipeline wrote it down. */
export function PipelineStages({ record }: { record: AnalysisRecord }) {
  const copyleft = record.stages.filter((stage) => stage.licence && COPYLEFT.test(stage.licence));
  return (
    <Panel
      id="pipeline"
      title="Pipeline"
      meta={
        copyleft.length > 0 ? (
          <span className="mono text-amber">{`${copyleft.map((stage) => stage.stage).join(", ")}: ${copyleft[0]?.licence}`}</span>
        ) : (
          <Label>{`${record.stages.length} stages on record`}</Label>
        )
      }
    >
      <Ledger
        className="mt-3"
        caption="Pipeline stages with tool, version, licence, device, frames, items, time, cache and cost"
        columns={STAGE_COLUMNS}
        rows={record.stages}
        rowKey={(row) => row.stage}
      />
    </Panel>
  );
}

const GATE_COLUMNS: readonly LedgerColumn<GateOutcome>[] = [
  {
    key: "metric",
    label: "Check",
    span: 4,
    rowHeader: true,
    cell: (row) => (
      <span className="block">
        <span className="inline-flex items-baseline gap-2">
          {row.passed ? null : <span aria-hidden="true" className="inline-block size-2 shrink-0 bg-stop" />}
          <Tooltip content={`${row.metric}, ${row.comparator} ${limitWords(row.threshold)}, measured ${row.measured == null ? "nothing" : measuredWords(row.measured)}`}>
            <span tabIndex={0}>{gateName(row.metric)}</span>
          </Tooltip>
        </span>
        {gateGloss(row.metric) ? (
          <span className="mt-0.5 block font-text leading-snug font-normal text-paint-3">{gateGloss(row.metric)}</span>
        ) : null}
      </span>
    ),
  },
  { key: "split", label: "Split", span: 1, cell: (row) => (row.split === "held_out" ? "Held out" : "Dev") },
  {
    key: "measured",
    label: "Measured",
    span: 2,
    align: "end",
    cell: (row) => (row.measured == null ? <Absent reason="No value on record" quiet /> : gateReading(row.metric, row.measured)),
  },
  {
    key: "limit",
    label: "Limit",
    span: 2,
    align: "end",
    cell: (row) => `${comparatorWord(row.comparator)} ${gateLimit(row.metric, row.threshold)}`,
  },
  { key: "n", label: "Sample", span: 1, align: "end", cell: (row) => row.n.toLocaleString("en-US") },
  {
    key: "outcome",
    label: "Outcome",
    span: 2,
    align: "end",
    cell: (row) => <StatusMark status={row.passed ? "gate_pass" : "gate_fail"} />,
  },
];

export function GateOutcomes({ record }: { record: AnalysisRecord }) {
  const gates = record.gates ?? [];
  const { passed, total } = gatesPassed(record);
  const withheld = figuresWithheld(record);
  const failed = gates.find((gate) => !gate.passed);
  const standing = record.disclosures.find((line) => line.startsWith(`Gate ${gates[0]?.gate_id}`));
  const limit = failed ? withheldBy(record, { status: "gate_fail", gate_id: failed.gate_id })?.limitNote : null;
  return (
    <Panel
      id="gates"
      title="Gates"
      meta={<Label tone="body">{total > 0 ? `${gates[0]?.gate_id}: ${passed} of ${total} checks passed` : "None on record"}</Label>}
    >
      {gates.length > 0 ? (
        <>
          <Ledger
            className="mt-3"
            caption="Registered gates read for this analysis"
            columns={GATE_COLUMNS}
            rows={gates}
            rowKey={(row) => `${row.gate_id}-${row.metric}-${row.scope}`}
          />
          {withheld.length > 0 ? (
            <p className="mt-3 max-w-[60rem] leading-snug text-paint-1">
              {`Withheld by the failed check: ${withheld.join(", ")}. ${limit ?? ""}`}
            </p>
          ) : null}
          {standing ? <p className="mt-2 max-w-[60rem] leading-snug text-paint-2">{recordWords(standing)}</p> : null}
        </>
      ) : (
        <EmptyState
          className="mt-3"
          title="No gate has been read for this analysis"
          reason="Its figures stay ungated until a registered gate compares them with a reference."
        />
      )}
    </Panel>
  );
}

/** How much of this run a placement check covers, in the record's own words. */
export function PlacementWindow({ record }: { record: AnalysisRecord }) {
  const coverage = measuredCoverage(record);
  const [from, to] = analysisSpan(record);
  const windows = (record.calibration.valid_windows_s ?? []).map(([low, high]) => `${low} to ${high}`).join(", ");
  return (
    <Panel id="placement" title="Placement" meta={coverage ? <OutsideWindow coverage={coverage} /> : undefined}>
      <Field label="Calibration" value={record.calibration.mode === "calibrated" ? "Calibrated, approximate" : "Uncalibrated"} />
      <Field label="Analysed, source s" value={`${from} to ${to}`} quiet />
      <Field label="Measured, source s" value={windows || undefined} reason="No window on record" />
      {coverage ? <Field label="Covered" value={`${coverage.coveredS} of ${to - from} s`} /> : null}
      {coverage ? <p className="mt-2 leading-snug text-paint-2">{coverage.note}</p> : null}
      {record.calibration.mode === "calibrated" ? null : (
        <p className="mt-2 leading-snug text-paint-2">
          {record.calibration.notes?.[0] ?? "No camera calibration: nothing here has a ground position."}
        </p>
      )}
      {coverage ? (
        <div className="mt-2">
          <EvidenceBadge kind="approximated" note="Arms, turns and entering flow rest on this placement" />
        </div>
      ) : null}
    </Panel>
  );
}
