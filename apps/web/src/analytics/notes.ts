import { gateLimit, gateReading } from "../domain/gates";
import type { AnalysisRecord } from "../lib/analysis";

const MID_VIEW = /(\d[\d,]*) other counted tracks began mid-view/;

/**
 * Why turning counts can pass while entering counts fail, as short lines. Null unless the record
 * has that split. The turning check is pooled over all movements, so no single cell is checked.
 */
export function splitNote(record: AnalysisRecord): string[] | null {
  const gates = record.gates ?? [];
  const entering = gates.find((gate) => gate.metric === "entering_count_error_share");
  const turning = gates.find((gate) => gate.metric === "turning_matrix_agreement");
  if (!entering || entering.passed || !turning?.passed || turning.measured == null) return null;
  const midView = record.disclosures.join(" ").match(MID_VIEW)?.[1];
  return [
    "Turning and entering counts are separate checks.",
    `Entering counts keep tracks first seen at the picture edge${midView ? `; ${midView} that began mid-view drop out` : ""}.`,
    `Turning counts use every counted track, pooled over all movements: ${gateReading(turning.metric, turning.measured)}, limit ${gateLimit(turning.metric, turning.threshold)}.`,
    "Movement rows are not entering counts.",
  ];
}
