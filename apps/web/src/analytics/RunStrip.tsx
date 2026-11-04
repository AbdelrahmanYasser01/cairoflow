import { Link } from "react-router-dom";
import { Label, StatusMark } from "../design";
import { analysisSpan } from "../domain/footage";
import { gatesPassed } from "../domain/gates";
import type { AnalysisRecord } from "../lib/analysis";

/** The run in one line: which seconds of the source, and what its gate said. */
export function RunStrip({ record }: { record: AnalysisRecord }) {
  const [from, to] = analysisSpan(record);
  const { passed, total } = gatesPassed(record);
  const gateId = record.gates?.[0]?.gate_id;
  return (
    <div className="flex flex-wrap items-center justify-end gap-x-5 gap-y-1">
      <span className="flex flex-col items-end">
        <Label>Source window</Label>
        <span className="mono text-paint-1">{`${Math.round(from)}–${Math.round(to)} s`}</span>
      </span>
      <span aria-hidden="true" className="h-8 w-px bg-rule-2" />
      {total > 0 ? (
        <Link to="/evidence" className="group flex flex-col items-end">
          <Label className="group-hover:text-paint-1">{gateId ? `Gate ${gateId}` : "Gate"}</Label>
          <span className="mono flex items-center gap-2 text-paint-1">
            <StatusMark status={passed === total ? "gate_pass" : "gate_fail"} decorative />
            {`${passed} of ${total} checks passed`}
          </span>
        </Link>
      ) : (
        <span className="flex flex-col items-end">
          <Label>{record.data_origin === "camera_cv" ? "Gate" : "Source"}</Label>
          {record.data_origin === "camera_cv" ? (
            <StatusMark status="ungated" />
          ) : (
            <span className="mono text-paint-1">Published NGSIM file</span>
          )}
        </span>
      )}
    </div>
  );
}
