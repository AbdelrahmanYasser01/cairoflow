import { EvidenceBadge, Field, Label, Panel } from "../design";
import { samChange, type SamChange } from "../domain/sam";
import type { LoadedAnalysis } from "../lib/analysis";

const NONE = "Not on record";

const whole = (value: number) => value.toLocaleString("en-US");
const share = (value: number) => `${(value * 100).toFixed(1)}%`;

/** "9,900 of 10,858", or nothing when either side is missing from the record. */
function outOf(part: number | null, total: number | null): string | undefined {
  return part === null || total === null ? undefined : `${whole(part)} of ${whole(total)}`;
}

function seededBy({ requests, costUsd }: SamChange): string | undefined {
  if (requests === null) return undefined;
  return costUsd === null ? `${requests} requests` : `${requests} requests, ${costUsd.toFixed(3)} USD`;
}

function interpolated({ interpolatedShareBefore: before, interpolatedShareAfter: after }: SamChange): string | undefined {
  return before === null || after === null ? undefined : `${share(before)} to ${share(after)}`;
}

function classes({ classFromMask, busesFromMask }: SamChange): string {
  return busesFromMask > 0 ? `${classFromMask} tracks, ${busesFromMask} a bus` : `${classFromMask} tracks`;
}

/**
 * What the refinement did to this run, shown only for a run that has one. It is a model working from
 * the detector's own boxes, so the block says what it did not do as plainly as what it did.
 */
export function SamChanged({ loaded }: { loaded: LoadedAnalysis }) {
  const change = samChange(loaded.record, loaded.tracks);
  if (!change) return null;
  const { measured } = change;
  const [from, to] = measured?.window_source_s ?? [];
  return (
    <Panel id="what-sam-changed" title="What SAM changed" meta={<EvidenceBadge kind="model_generated" note="Not ground truth" />}>
      <p className="mt-3 max-w-[44rem] leading-snug text-paint-2">
        The detector seeded Meta's SAM 3.1 through box prompts. Known misses remain: SAM follows only what it was seeded with.
      </p>
      <div className="mt-1">
        <Field label="Box prompts" value={seededBy(change)} reason={NONE} />
        <Field label="Observed rows SAM agreed with (seeded from these tracks)" value={outOf(change.rowsConfirmed, change.rowsObserved)} reason={NONE} />
        <Field label="Interpolated rows replaced" value={outOf(change.rowsReplaced, change.rowsInterpolated)} reason={NONE} />
        <Field label="Rows still interpolated" value={interpolated(change)} reason={NONE} />
        {measured ? (
          <Field
            label="Median across-road error, replaced rows"
            value={`${measured.across_road_error_m.interpolated_position} to ${measured.across_road_error_m.sam_position} m`}
          />
        ) : null}
        <Field label="Class set by mask length" value={classes(change)} />
      </div>
      {measured ? (
        <>
          <p className="mt-3 max-w-[44rem] leading-snug font-medium text-paint-1">
            SAM did not raise recall.
            <span className="font-normal text-paint-2">
              {` The control left the same rows at their interpolated positions and scored ${measured.recall.same_rows_interpolated}, against ${measured.recall.with_sam_boxes} with SAM's boxes.`}
            </span>
          </p>
          <p className="mt-2 max-w-[44rem] leading-snug text-paint-2">
            {`Mask length is thin evidence: only ${measured.long_vehicles_in_window} long vehicles are in this window.`}
          </p>
          <Label className="mt-2 block">
            {`Error and recall on source seconds ${from} to ${to}: ${measured.label}.`}
          </Label>
        </>
      ) : null}
    </Panel>
  );
}
