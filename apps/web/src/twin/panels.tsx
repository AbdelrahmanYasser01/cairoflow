/**
 * Instruments for the twin. They hold no state of their own: each reads the shared stores and the
 * shared clock, so a page can place them anywhere and they stay in step with the scene.
 */
import { useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import {
  Absent,
  Button,
  EVIDENCE,
  EvidenceBadge,
  EvidenceGlyph,
  Field,
  Label,
  Panel,
  Segmented,
  Transport,
  type EvidenceKind,
  type SegmentedOption,
} from "../design";
import { analysisName } from "../domain/analyses";
import { placementWords, recordWords } from "../domain/gates";
import { findMetric, fragmentCount } from "../domain/metrics";
import { groundFrameName } from "../domain/site";
import { VEHICLE_CLASSES, TURNS } from "../domain/vocabulary";
import { clock, NO_GROUND, NO_SPEED, type AnalysisRecord, type GateOutcome } from "../lib/analysis";
import {
  NOT_RECORDED,
  densitySummary,
  display,
  filterValue,
  filters,
  live,
  openPanel,
  resetFilters,
  setTimeWindow,
  toggleFilter,
  toggleKind,
  trackMask,
  type FilterGroup,
  type TwinMode,
  type TwinView,
} from "./controls";
import { COLUMN_MIN_LEVEL } from "./density";
import type { TwinModel } from "./model";
import { clearSelection, selectTrack, useSelection } from "./selection";
import { useStore } from "./store";
import { activeModel } from "./useTwinModel";

const TRANSPORT_HZ = 12;
const INSPECTOR_HZ = 5;

/** Re-renders at a low rate from the shared clock, and at once on play, pause and seek-to-self. */
function useClockReading(hz: number): number {
  const [, tick] = useReducer((count: number) => count + 1, 0);
  const last = useRef({ at: 0, playing: clock.playing, rate: clock.rate });
  useEffect(
    () =>
      clock.subscribe(() => {
        const now = performance.now();
        const state = last.current;
        const changed = state.playing !== clock.playing || state.rate !== clock.rate;
        if (!changed && clock.playing && now - state.at < 1000 / hz) return;
        last.current = { at: now, playing: clock.playing, rate: clock.rate };
        tick();
      }),
    [hz],
  );
  return clock.get();
}

const RATES = [
  { value: "1", label: "1x" },
  { value: "2", label: "2x" },
  { value: "4", label: "4x" },
  { value: "8", label: "8x" },
] as const;

type Rate = (typeof RATES)[number]["value"];

export function TwinTransport({ className = "" }: { className?: string }) {
  const model = useStore(activeModel);
  const frame = useClockReading(TRANSPORT_HZ);
  if (!model) return <Transport reason="Transport idle: no analysis loaded" className={className} />;
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <Transport
        className="min-w-0 flex-1"
        state={{
          currentS: frame / model.fps,
          durationS: model.durationS,
          frame,
          playing: clock.playing,
        }}
        onToggle={() => clock.toggle()}
        onStep={(frames) => {
          clock.pause();
          clock.seek(Math.round(clock.get()) + frames);
        }}
        onSeek={(seconds) => clock.seek(seconds * model.fps)}
      />
      <Segmented<Rate>
        label="Playback speed"
        options={RATES}
        value={String(clock.rate) as Rate}
        onChange={(rate) => clock.setRate(Number(rate))}
      />
    </div>
  );
}

const MODE_OPTIONS: readonly SegmentedOption<TwinMode>[] = [
  { value: "twin", label: "Twin" },
  { value: "geo", label: "Geo" },
];

export function TwinViewSwitch({ className = "" }: { className?: string }) {
  const shown = useStore(display);
  const { trackId } = useSelection();
  const options: readonly SegmentedOption<TwinView>[] = [
    { value: "plan", label: "Plan" },
    { value: "oblique", label: "Oblique" },
    { value: "along", label: "Along Lankershim" },
    {
      value: "follow",
      label: "Follow selected",
      disabled: trackId === null,
      reason: "Follow needs a selected vehicle: click one first",
    },
  ];
  return (
    <div className={`flex flex-wrap items-center gap-3 ${className}`}>
      <div className="flex gap-1" role="group" aria-label="Layers">
        <LayerSwitch
          label="Trails"
          on={shown.trails}
          onToggle={() => display.update((current) => ({ ...current, trails: !current.trails }))}
        />
        {shown.mode === "geo" ? (
          <>
            <LayerSwitch
              label="Ribbons"
              on={shown.ribbons}
              onToggle={() => display.update((current) => ({ ...current, ribbons: !current.ribbons }))}
            />
            <LayerSwitch
              label="Columns"
              on={shown.columns}
              onToggle={() => display.update((current) => ({ ...current, columns: !current.columns }))}
            />
          </>
        ) : (
          <LayerSwitch
            label="Density"
            on={shown.density}
            onToggle={() => display.update((current) => ({ ...current, density: !current.density }))}
          />
        )}
      </div>
      <Segmented<TwinMode>
        label="Twin or geospatial layer"
        options={MODE_OPTIONS}
        value={shown.mode}
        onChange={(mode) => display.update((current) => ({ ...current, mode }))}
      />
      <Segmented<TwinView>
        label="Camera view"
        options={options}
        value={shown.view}
        onChange={(view) => display.update((current) => ({ ...current, view }))}
      />
    </div>
  );
}

function Chip({
  on,
  label,
  count,
  onToggle,
}: {
  on: boolean;
  label: string;
  count: number;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onToggle}
      className={`mono flex min-h-8 items-center gap-2 border px-2 transition-colors duration-150 ${
        on
          ? "border-rule-2 bg-asphalt-2 text-paint-1 hover:border-paint-4"
          : "border-rule-1 text-paint-3 hover:text-paint-2"
      }`}
    >
      <span>{label}</span>
      <span className={on ? "text-paint-3" : "text-paint-4"}>{count}</span>
    </button>
  );
}

/** Where a class came from, as a reader sees it. Anything else keeps the record's word. */
const CLASS_SOURCE: Readonly<Record<string, string>> = {
  detector: "Detector label",
  size: "Rough placed size",
  sam: "SAM mask length",
  none: "Not resolved",
};

const CLASS_LABEL = new Map<string, string>(VEHICLE_CLASSES.map(({ id, label }) => [id, label]));
const TURN_LABEL = new Map<string, string>(TURNS.map(({ id, label }) => [id, label]));

function valueLabel(group: FilterGroup, value: string): string {
  if (value === NOT_RECORDED) return "Not recorded";
  if (group === "classes") return CLASS_LABEL.get(value) ?? value;
  if (group === "turns") return TURN_LABEL.get(value) ?? value;
  return value;
}

function FilterGroupRow({
  title,
  group,
  model,
}: {
  title: string;
  group: FilterGroup;
  model: TwinModel;
}) {
  const active = useStore(filters);
  const counts = useMemo(() => {
    const seen = new Map<string, number>();
    for (const track of model.tracks) {
      const value = filterValue(track, group);
      seen.set(value, (seen.get(value) ?? 0) + 1);
    }
    return [...seen.entries()].sort(([a], [b]) => {
      if (a === NOT_RECORDED) return 1;
      if (b === NOT_RECORDED) return -1;
      return (seen.get(b) ?? 0) - (seen.get(a) ?? 0);
    });
  }, [model, group]);

  return (
    <div className="border-b border-rule-1 py-2 last:border-b-0">
      <Label className="mb-1.5 block">{title}</Label>
      <div className="flex flex-wrap gap-1">
        {counts.map(([value, count]) => (
          <Chip
            key={value}
            label={valueLabel(group, value)}
            count={count}
            on={!active.hidden[group].has(value)}
            onToggle={() => toggleFilter(group, value)}
          />
        ))}
      </div>
    </div>
  );
}

function clockLabel(seconds: number): string {
  const whole = Math.round(seconds);
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

function TimeFilter({ model }: { model: TwinModel }) {
  const active = useStore(filters);
  const duration = model.durationS;
  const [from, to] = active.timeS ?? [0, duration];
  const peak = Math.max(1, ...model.timeline);
  const set = (nextFrom: number, nextTo: number) => {
    const low = Math.min(nextFrom, nextTo);
    const high = Math.max(nextFrom, nextTo);
    const whole = low <= 0 && high >= duration;
    setTimeWindow(whole ? null : [low, high]);
    if (!whole && (clock.get() < low * model.fps || clock.get() > high * model.fps)) {
      clock.seek(low * model.fps);
    }
  };
  const bins = model.timeline.length;
  return (
    <div className="border-b border-rule-1 py-2.5">
      <div className="mb-1.5 flex items-baseline justify-between">
        <Label>Time window</Label>
        <Label tone="body">
          {clockLabel(from)} to {clockLabel(to)}
        </Label>
      </div>
      <svg
        viewBox={`0 0 ${bins} 22`}
        preserveAspectRatio="none"
        className="block h-5 w-full"
        role="img"
        aria-label="Vehicles present over the analysis"
      >
        {Array.from(model.timeline, (count, bin) => {
          const height = (count / peak) * 22;
          const at = (bin / bins) * duration;
          const inside = at >= from - duration / bins && at <= to;
          return (
            <rect
              key={bin}
              x={bin + 0.1}
              y={22 - height}
              width={0.8}
              height={height}
              fill={inside ? "var(--paint-3)" : "var(--rule-2)"}
            />
          );
        })}
      </svg>
      <input
        type="range"
        aria-label="Window start"
        min={0}
        max={duration}
        step={0.1}
        value={from}
        onChange={(event) => set(Number(event.target.value), to)}
        className="mt-1 block w-full accent-[var(--amber)]"
      />
      <input
        type="range"
        aria-label="Window end"
        min={0}
        max={duration}
        step={0.1}
        value={to}
        onChange={(event) => set(from, Number(event.target.value))}
        className="block w-full accent-[var(--amber)]"
      />
    </div>
  );
}

function LayerSwitch({ label, on, onToggle }: { label: string; on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onToggle}
      className={`mono min-h-8 border border-b-2 px-2 transition-colors duration-150 ${
        on
          ? "border-rule-2 border-b-amber bg-asphalt-3 text-paint-1"
          : "border-rule-2 bg-asphalt-1 text-paint-2 hover:text-paint-1"
      }`}
    >
      {label}
    </button>
  );
}

/** The analysis on screen, by the title in its record. */
export function TwinAnalysisName({ className = "" }: { className?: string }) {
  const model = useStore(activeModel);
  return model ? (
    <Label className={className}>
      {analysisName({ id: model.id, title: model.record.title, mode: model.record.mode })}
    </Label>
  ) : (
    <Label className={className}>Loading the analysis</Label>
  );
}

/** What each evidence kind is in this scene, so the switch says what it will hide. */
function kindLayers(model: TwinModel): readonly { kind: EvidenceKind; label: string }[] {
  const positions = positionsKind(model.record);
  return positions === "observed"
    ? [
        { kind: "observed", label: "Positions" },
        { kind: "approximated", label: "Road" },
        { kind: "assumed", label: "Body shape" },
      ]
    : [
        { kind: "approximated", label: "Positions, road" },
        { kind: "assumed", label: "Body shape" },
      ];
}

function EvidenceRow({ model }: { model: TwinModel }) {
  const { hiddenKinds } = useStore(display);
  return (
    <div className="border-b border-rule-1 py-2">
      <Label className="mb-1.5 block">Evidence drawn</Label>
      <div className="flex flex-wrap gap-1">
        {kindLayers(model).map(({ kind, label }) => {
          const on = !hiddenKinds.has(kind);
          return (
            <button
              key={kind}
              type="button"
              role="switch"
              aria-checked={on}
              aria-label={`${EVIDENCE[kind].label}: ${label}`}
              onClick={() => toggleKind(kind)}
              className={`mono flex min-h-8 items-center gap-1.5 border px-2 transition-colors duration-150 ${
                on
                  ? "border-rule-2 bg-asphalt-2 text-paint-1 hover:border-paint-4"
                  : "border-rule-1 text-paint-3 opacity-60 hover:text-paint-2"
              }`}
            >
              <EvidenceGlyph kind={kind} />
              <span>{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Why these chips aren't the counts on Analytics: the twin holds every track, and an arm here isn't an entering count. */
function TracksNote({ model }: { model: TwinModel }) {
  const fragments = fragmentCount(model.record);
  const counted = findMetric(model.record, "vehicles.total")?.value;
  return (
    <p className="mono border-b border-rule-1 py-2 text-paint-3">
      {fragments && counted != null
        ? `${model.tracks.length} tracks: ${counted} counted, plus ${fragments} fragments under 1 s. An arm here is not an entering count.`
        : "An arm here is not an entering count."}
    </p>
  );
}

export function TwinFilters({
  showEvidence = true,
  collapsible = false,
  className = "",
}: {
  /** False when the page runs its own evidence filter through TwinStage's visibleKinds. */
  showEvidence?: boolean;
  /** Starts as one line with the kept count, so the panel doesn't sit on the road until it's wanted. */
  collapsible?: boolean;
  className?: string;
}) {
  const panel = useStore(openPanel);
  const open = !collapsible || panel === "filters";
  const model = useStore(activeModel);
  const active = useStore(filters);
  const kept = useMemo(
    () => (model ? trackMask(model.tracks, active, model.fps).reduce((sum, on) => sum + on, 0) : 0),
    [model, active],
  );
  const untouched =
    active.timeS === null && Object.values(active.hidden).every((hidden) => hidden.size === 0);
  const keptLabel = model ? `${kept} of ${model.tracks.length} tracks` : "";

  if (!open) {
    return (
      <button
        type="button"
        aria-expanded={false}
        onClick={() => openPanel.set("filters")}
        className={`mono flex min-h-8 items-center gap-3 border border-rule-2 bg-asphalt-1 px-3 text-paint-1 transition-colors duration-150 hover:border-paint-4 ${className}`}
      >
        <span>Filters</span>
        <span className={untouched ? "text-paint-3" : "text-amber"}>{keptLabel}</span>
      </button>
    );
  }

  return (
    <Panel
      overlay
      title="Filters"
      className={className}
      meta={
        <div className="flex items-center gap-3">
          {model ? <Label>{keptLabel}</Label> : null}
          {collapsible ? (
            <Button tone="text" className="min-h-6" aria-expanded onClick={() => openPanel.set(null)}>
              Close
            </Button>
          ) : null}
        </div>
      }
    >
      {model ? (
        <>
          <TracksNote model={model} />
          <TimeFilter model={model} />
          <FilterGroupRow title="Class" group="classes" model={model} />
          <FilterGroupRow title="Approach, all tracks by first arm" group="approaches" model={model} />
          <FilterGroupRow title="Turn" group="turns" model={model} />
          {showEvidence ? <EvidenceRow model={model} /> : null}
          {untouched ? null : (
            <Button tone="text" className="mt-2 min-h-7" onClick={resetFilters}>
              Show everything
            </Button>
          )}
        </>
      ) : (
        <p className="mono mt-3 text-paint-3">Filters appear with the analysis.</p>
      )}
    </Panel>
  );
}

function pointAt(model: TwinModel, trackIndex: number, frame: number): number | null {
  const track = model.tracks[trackIndex];
  const offset = Math.floor(frame) - track.start_frame;
  return offset >= 0 && offset < track.n ? track.first_row + offset : null;
}

function LiveFields({ model, trackIndex }: { model: TwinModel; trackIndex: number }) {
  const frame = useClockReading(INSPECTOR_HZ);
  const row = pointAt(model, trackIndex, frame);
  const t = model.trajectories;
  const notNow = "Not in view at this time";
  const speed = row === null ? null : t.speedCmS[row];
  const grounded = row !== null && t.gx[row] !== NO_GROUND;
  return (
    <>
      <Field
        label="Speed now"
        value={speed !== null && speed !== NO_SPEED ? `${(speed / 100).toFixed(1)} m/s` : undefined}
        reason={row === null ? notNow : "Not recorded"}
      />
      <Field
        label="Heading now"
        value={row !== null ? `${((t.heading[row] / 256) * 360).toFixed(0)} deg, 0 is up Lankershim` : undefined}
        reason={notNow}
      />
      <Field
        label={model.positionAnchor === "front_centre" ? "Front centre now" : "Position now"}
        value={
          grounded && row !== null
            ? `${(t.gy[row] * model.groundScale).toFixed(1)} m along Lankershim, ${(t.gx[row] * model.groundScale).toFixed(1)} m across`
            : undefined
        }
        reason={row === null ? notNow : "No ground position"}
      />
    </>
  );
}

export function TwinInspector({
  className = "",
  hideEmpty = false,
}: {
  className?: string;
  /** With nothing selected, nothing at all: the legend's hint says what to do. */
  hideEmpty?: boolean;
}) {
  const model = useStore(activeModel);
  const active = useStore(filters);
  const { hidden: positionsHidden } = useStore(live);
  const { analysisId, trackId } = useSelection();
  const index = model && analysisId === model.id && trackId !== null ? model.indexOfTrack.get(trackId) : undefined;

  if (!model || index === undefined) {
    if (hideEmpty) return null;
    return (
      <Panel overlay title="Inspector" className={className}>
        <p className="mono mt-3 text-paint-2">Click a vehicle to read its record.</p>
      </Panel>
    );
  }

  const track = model.tracks[index];
  const from = track.start_frame / model.fps;
  const to = (track.start_frame + track.n - 1) / model.fps;
  const size = (value: number | null | undefined) => (value == null ? undefined : `${value.toFixed(2)} m`);
  const positions = positionsKind(model.record);
  const published = model.record.data_origin === "reference_dataset";
  const filteredOut = trackMask([track], active, model.fps)[0] === 0;
  const notice = positionsHidden
    ? "Positions hidden: this track isn't drawn."
    : filteredOut
      ? "Filtered out: the current filters drop this track. Its path is dimmed."
      : null;
  return (
    <Panel
      overlay
      title={`Track ${track.track_id}`}
      className={className}
      meta={
        <Button tone="text" className="min-h-6" onClick={() => selectTrack(model.id, null)}>
          Clear
        </Button>
      }
    >
      {notice ? <p className="mono mt-2 border border-rule-2 px-2 py-1 text-paint-2">{notice}</p> : null}
      <div className="mt-1">
        <LiveFields model={model} trackIndex={index} />
        <Field
          label="Class"
          value={CLASS_LABEL.get(track.vehicle_class) ?? track.vehicle_class}
          evidence={track.class_source === "none" ? "assumed" : "approximated"}
        />
        <Field label="Class from" value={CLASS_SOURCE[track.class_source] ?? track.class_source} />
        <Field
          label="Class score, 0 to 1"
          value={track.class_confidence == null ? undefined : track.class_confidence.toFixed(2)}
        />
        {published ? (
          <>
            <Field label="Length" value={size(track.length_m)} evidence={track.length_m == null ? undefined : positions} />
            <Field label="Width" value={size(track.width_m)} evidence={track.width_m == null ? undefined : positions} />
          </>
        ) : track.length_m != null && track.width_m != null ? (
          // Not the body in the scene: a camera track is drawn at a nominal size, as Limits says.
          <Field
            label="Rough size, not drawn"
            value={`${track.length_m.toFixed(1)} by ${track.width_m.toFixed(1)} m`}
            evidence="approximated"
          />
        ) : null}
        <Field label="Approach" value={track.approach ?? undefined} />
        <Field label="Exit arm" value={track.exit_arm ?? undefined} />
        <Field label="Turn" value={track.turn ? (TURN_LABEL.get(track.turn) ?? track.turn) : undefined} />
        <Field label="Track status" value={track.status} />
        <Field label="In view" value={`${from.toFixed(1)} to ${to.toFixed(1)} s, ${track.n} points`} />
        <Field
          label="Source record"
          value={track.reference_vehicle_id == null ? undefined : `NGSIM vehicle ${track.reference_vehicle_id}`}
        />
        <Field label="SAM refined" value={track.sam_refined ? "Yes" : "No"} />
      </div>
    </Panel>
  );
}

export function positionsKind(record: AnalysisRecord): EvidenceKind {
  return record.data_origin === "reference_dataset" ? "observed" : "approximated";
}

/** The evidence kinds this scene draws anything of. A switch for any other would control nothing. */
export function drawnKinds(record: AnalysisRecord): readonly EvidenceKind[] {
  return positionsKind(record) === "observed" ? ["observed", "approximated", "assumed"] : ["approximated", "assumed"];
}

/** The gate that measured placement in metres, preferring held-out footage over dev footage. */
export function placementGate(record: AnalysisRecord): GateOutcome | null {
  const metres = (record.gates ?? []).filter((gate) => gate.metric.endsWith("_m") && gate.measured != null);
  return metres.find((gate) => gate.split === "held_out") ?? metres[0] ?? null;
}

function positionsNote(record: AnalysisRecord): string {
  if (record.data_origin === "reference_dataset") return "Published reference data, not camera-derived";
  const gate = placementGate(record);
  if (!gate || gate.measured == null) return "Camera-derived, no measured error on record";
  return placementWords(gate);
}

function Rows({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-1">{children}</div>;
}

/**
 * What every position on screen is. Shown whenever the twin is, on any page. `compact` is one line
 * for an embedded view; the full panel opens onto the record's own disclosures.
 */
export function TwinProvenance({
  compact = false,
  expandable = false,
  className = "",
}: {
  compact?: boolean;
  /** With `compact`: a Limits control that opens the full panel from the one line. */
  expandable?: boolean;
  className?: string;
}) {
  const model = useStore(activeModel);
  const counts = useStore(live);
  const density = useStore(densitySummary);
  const shown = useStore(display);
  const open = useStore(openPanel) === "limits";
  const setOpen = (next: boolean) => openPanel.set(next ? "limits" : null);
  if (!model) return null;
  const { record, site } = model;
  const measured = placementGate(record);
  const geo = shown.mode === "geo";

  const stateLine = geo
    ? "No basemap. Positions in the junction frame."
    : "Not a precise geographic reconstruction.";
  const alert =
    counts.unplaced > 0 || counts.outsideValid > 0 ? (
      <p className="mono m-0 text-amber">
        {counts.unplaced > 0 ? `${counts.unplaced} alive now can't be placed. ` : ""}
        {counts.outsideValid > 0 ? `${counts.outsideValid} outside the measured region.` : ""}
      </p>
    ) : null;

  if (compact && !open) {
    if (!expandable) {
      return (
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-1 ${className}`}>
          <EvidenceBadge kind={positionsKind(record)} note={positionsNote(record)} />
          <EvidenceBadge kind="approximated" note="Road geometry approximate" />
        </div>
      );
    }
    // One quiet line: each status as its glyph (name and meaning on hover), then the detail behind Limits.
    return (
      <section
        data-twin-chrome
        className={`flex flex-wrap items-center gap-x-2 gap-y-0.5 border border-rule-1 bg-asphalt-0/80 px-1.5 py-0.5 backdrop-blur-sm ${className}`}
      >
        <span className="flex items-center" aria-label="Evidence status of this view">
          <EvidenceBadge compact kind={positionsKind(record)} note={positionsNote(record)} />
          <EvidenceBadge compact kind="approximated" note="Road geometry approximate" />
          <EvidenceBadge compact kind="assumed" note="Bodies schematic" />
          {geo ? null : <EvidenceBadge compact kind="assumed" note="Context blocks, not surveyed" />}
        </span>
        <span className="mono text-[12px] text-paint-3">{geo ? "No basemap" : "Approximate reconstruction"}</span>
        {alert}
        <Button tone="text" className="min-h-5 shrink-0 text-[13px]" aria-expanded={false} onClick={() => setOpen(true)}>
          Limits
        </Button>
      </section>
    );
  }

  return (
    <section data-twin-chrome className={`border border-rule-2 bg-asphalt-1 px-3 py-2.5 ${className}`}>
      <Rows>
        <EvidenceBadge kind={positionsKind(record)} note={positionsNote(record)} />
        <EvidenceBadge kind="approximated" note="Road geometry derived from trajectories, approximate" />
        <EvidenceBadge kind="assumed" note="Vehicle shape and height schematic" />
        {geo ? null : <EvidenceBadge kind="assumed" note="Context blocks, not surveyed" />}
      </Rows>
      <div className="mt-1 flex items-baseline justify-between gap-4">
        <p className="mono text-paint-3">
          {geo ? stateLine : `${groundFrameName(site.ground_frame)}. ${stateLine}`}
        </p>
        <Button tone="text" className="min-h-5 shrink-0 text-[14px]" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Limits"}
        </Button>
      </div>
      {alert}
      {open ? (
        <div className="mono mt-1.5 max-h-[38dvh] overflow-y-auto border-t border-rule-1 pt-1.5 text-paint-2">
          <p>{record.title}</p>
          <p className="mt-1">
            {site.site_name ?? <Absent reason="Site name not recorded" />}
            {site.site_name_status ? ` (site identity: ${site.site_name_status.replaceAll("_", " ")})` : ""}
          </p>
          <ul className="mt-1.5 list-none p-0">
            {record.disclosures.map((line) => (
              <li key={line} className="mt-1">
                {recordWords(line)}
              </li>
            ))}
            {measured?.measured != null ? (
              <li className="mt-1">{`Exact check: ${measured.metric} ${measured.measured.toFixed(4)} m, gate ${measured.gate_id}.`}</li>
            ) : null}
            <li className="mt-1">
              Stop lines are placed from queue heads, not the painted limit lines, and nothing is counted
              at them: a count comes from a track&apos;s first and last ground points. Lane lines sit on
              lane polygon edges and their dash pattern is drawn, not surveyed.
            </li>
            <li className="mt-1">
              Turn arrows show observed use per lane. Kerbs follow where vehicles drove in this analysis,
              not a survey. Arms, lanes and sidewalks run on past the data to the scene edge.
            </li>
            <li className="mt-1">
              Between 10 Hz rows vehicles follow a curve through the rows. Drawn headings come from the
              path itself, 2.5 m of travel each side, because the published column is noise at low speed.
              The inspector shows the raw values.
            </li>
            {model.positionAnchor === "front_centre" ? (
              <li className="mt-1">
                Positions are each vehicle&apos;s front centre, as published. Bodies are drawn behind that
                point; trails, heat and columns use it as it stands.
              </li>
            ) : null}
            <li className="mt-1">
              Vehicles grow in and shrink out over 0.4 s where a track starts or ends. That is where
              tracking begins and stops, which can be inside the junction. Positions don&apos;t change.
            </li>
            {model.positionJumps > 0 ? (
              <li className="mt-1">
                {model.positionJumps} row-to-row jumps over 2.5 m in {model.jumpTracks} tracks are tracking
                errors in the source. They are drawn as published, not smoothed.
              </li>
            ) : null}
            {model.nominalSizeTracks > 0 ? (
              <li className="mt-1">
                {model.nominalSizeTracks} tracks have no measured size and are drawn at a nominal one.
              </li>
            ) : null}
            {model.unplacedTracks > 0 ? (
              <li className="mt-1">{model.unplacedTracks} tracks have no ground position at all.</li>
            ) : null}
            {density ? (
              <li className="mt-1">
                {geo ? "Column height" : "Heat"}: vehicle-seconds per cell from these positions.
                {" "}Peak {density.peakVehicleSeconds.toFixed(1)} veh-s in a {density.cellM} m cell.
                {geo ? ` Columns under ${COLUMN_MIN_LEVEL * 100}% of the peak aren't drawn.` : ""}
              </li>
            ) : null}
            {geo ? (
              <li className="mt-1">
                Coordinate ticks come from the calibration origin and bearing, with the grid scale factor
                ignored and NAD83 labelled as WGS84 (about 1 m here). Axis ticks, not survey coordinates.
                Positions are metres along and across Lankershim in the junction frame, u across and v along.
              </li>
            ) : null}
          </ul>
          <p className="mt-1.5 text-paint-3">
            {record.source.licence_name}. {record.source.attribution}
          </p>
        </div>
      ) : null}
    </section>
  );
}

/** Heat or column legend, in the units the layer actually holds. */
export function TwinDensityLegend({
  hint = false,
  className = "",
}: {
  /** With nothing selected, also says how to read a vehicle's record. */
  hint?: boolean;
  className?: string;
}) {
  const { trackId } = useSelection();
  const density = useStore(densitySummary);
  const shown = useStore(display);
  const counts = useStore(live);
  const geo = shown.mode === "geo";
  const showDensity = density && !counts.hidden && (geo ? shown.columns : shown.density);
  return (
    <div
      data-twin-chrome
      className={`mono flex items-center gap-2 border border-rule-1 bg-asphalt-0/80 px-1.5 py-0.5 text-[12px] text-paint-2 backdrop-blur-sm ${className}`}
    >
      <span className="text-paint-1">{counts.hidden ? "Positions hidden" : `${counts.onScreen} on screen`}</span>
      {showDensity ? (
        <>
          <span className="text-paint-4">|</span>
          <span>Occupancy</span>
          <span
            aria-hidden="true"
            className="h-2 w-16"
            style={{ background: "linear-gradient(90deg, var(--rule-2), var(--paint-3) 62%, var(--paint-1))" }}
          />
          <span title={geo ? "Taller column: more vehicle time spent here" : "Brighter: more vehicle time spent here"}>
            {geo ? "Height = vehicle time" : "Brightness = vehicle time"}
          </span>
        </>
      ) : null}
      {hint && trackId === null && counts.onScreen > 0 ? (
        <>
          <span className="text-paint-4">|</span>
          <span className="text-paint-3">Click a vehicle</span>
        </>
      ) : null}
    </div>
  );
}

export { clearSelection };
