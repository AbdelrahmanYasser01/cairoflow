import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Absent,
  Button,
  ButtonLink,
  EVIDENCE,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  EvidenceGlyph,
  Field,
  Label,
  NO_MEDIA,
  Panel,
  Segmented,
  Select,
  SkeletonRows,
  Transport,
  useElementSize,
  type SegmentedOption,
  type TransportState,
} from "../design";
import { Instrument, PageHead } from "../design/Instrument";
import {
  analysisSpan,
  clipSpan,
  coveredS,
  footageFor,
  insideMeasuredWindow,
  isCalibrated,
  pickClip,
} from "../domain/footage";
import { indexMasks, maskFrameNote, nearestMask } from "../domain/masks";
import { findMetric } from "../domain/metrics";
import { MODE_LABEL } from "../domain/vocabulary";
import { SAM_ANALYSIS } from "../domain/analyses";
import { MetaModels } from "../instruments/MetaModels";
import { ClipStage, TrackStage, type FrameTrack, type OverlayMode } from "../instruments/ClipStage";
import { FrameCorners, LivePanel, LiveStage, RecentJobs, RunProgress, UNCALIBRATED_NOTE } from "../instruments/LiveAnalysis";
import {
  clock,
  hasSamOutlines,
  type AnalysisRecord,
  type ArtifactProbe,
  type FootageSource,
  type LoadedAnalysis,
} from "../lib/analysis";
import { analysisLabel, useAnalysis } from "../state/analysis";
import { useLive } from "../state/live";
import { useAnnotatedVideo, useSamOutlines, useSources, type Resource } from "../state/resources";
import { GeoLayer } from "../twin";
import { selectTrack, useSelection } from "../twin/selection";

/** The overlays on offer. Masks only where the analysis has outlines, and a stage with no video keeps one on. */
function overlayOptions(trackOnly: boolean, outlines: boolean, outlinesReady: boolean): SegmentedOption<OverlayMode>[] {
  return [
    { value: "boxes", label: "Boxes" },
    { value: "tracks", label: "Tracks" },
    ...(outlines ? [{ value: "masks" as const, label: "Masks", disabled: !outlinesReady }] : []),
    ...(trackOnly ? [] : [{ value: "off" as const, label: "Off" }]),
  ];
}

type View = "playback" | "live";

const VIEWS: readonly SegmentedOption<View>[] = [
  { value: "playback", label: "Playback" },
  { value: "live", label: "Live analysis" },
];

/** What fills the stage. The footage stays mounted under either, so one transport moves both. */
type Layer = "footage" | "geo";

const LAYERS: readonly SegmentedOption<Layer>[] = [
  { value: "footage", label: "Footage" },
  { value: "geo", label: "Geospatial" },
];

/** Wide, the layer takes the stage and the footage sits on it. Narrow, they stack: an inset would be a thumbnail. */
const GEO_LAYER = "relative h-[34rem] lg:absolute lg:inset-0 lg:h-auto";
const GEO_FOOTAGE = "relative z-10 mt-2 border border-rule-2 bg-asphalt-1 lg:absolute lg:top-2 lg:left-2 lg:mt-0 lg:w-3/10";

/** What the footage control can ask for, beside a clip's own source id. */
const ANNOTATED = "annotated";
const TRACKS_ONLY = "tracks";

/**
 * The published file and this video share a clock only to within a fraction of a second (sync 120.0 s,
 * DATA_REGISTER.md §15), so a moving reference box runs ahead of its car. It is not corrected here:
 * shifting the boxes would invent a sync nobody measured per vehicle. Camera boxes do not do this.
 */
const REFERENCE_SYNC_NOTE = "Published positions, not frame-synced: moving boxes run ahead of the cars";

/** Room the left column keeps for its heading, transport and attribution, in pixels. */
const HEAD_PX = 132;
const FOOT_PX = 126;
const NOTICE_PX = 56;
const PROGRESS_PX = 160;
const SIDE_MIN_PX = 392;
/** The aspect of the stage before a job has given it one: the camera's. */
const DEFAULT_ASPECT = 4 / 3;

/** Why there is no video under the tracks, and what would bring it back. */
interface TrackNotice {
  title: string;
  why: string;
  hint: string | null;
  licence: string | null;
}

type Footage =
  | { kind: "video"; clip: FootageSource; id: string; burnedIn: boolean }
  | { kind: "tracks"; notice: TrackNotice; id: string };

const CHOSEN: TrackNotice = {
  title: "Tracks only",
  why: "Stored tracks on the shared clock.",
  hint: null,
  licence: null,
};

const PLAY_FAILED: TrackNotice = {
  title: "Video wouldn't play",
  why: "Stored tracks on the shared clock.",
  hint: null,
  licence: null,
};

function noticeFor(probe: Resource<ArtifactProbe>): TrackNotice {
  if (probe.status === "ready" && !probe.data.available && probe.data.rebuildHint) {
    return {
      title: "Tracks only",
      why: "Footage not distributed.",
      hint: probe.data.rebuildHint,
      licence: probe.data.licenceNote,
    };
  }
  return {
    title: "Tracks only",
    why: "No footage on this server.",
    hint: null,
    licence: null,
  };
}

/** The annotated video as a clip: it starts where the analysis starts. */
function annotatedClip(record: AnalysisRecord, url: string): FootageSource {
  return {
    source_id: ANNOTATED,
    kind: "annotated_video",
    title: "Annotated video",
    file_name: "annotated.mp4",
    duration_s: record.frame_count / record.fps,
    width_px: record.width_px,
    height_px: record.height_px,
    fps: record.fps,
    calibrated: isCalibrated(record),
    calibration_note: null,
    playable: true,
    source_start_s: record.source_start_s,
    problem: null,
    video_url: url,
  };
}

/** Which footage to show. Null while it isn't yet known whether the video artifact is here. */
function chooseFootage(
  record: AnalysisRecord,
  clips: readonly FootageSource[],
  asked: string | null,
  probe: Resource<ArtifactProbe>,
): Footage | null {
  const annotated = probe.status === "ready" && probe.data.available ? annotatedClip(record, probe.data.url) : null;
  const best = pickClip(clips, record);
  const video = (clip: FootageSource): Footage => ({
    kind: "video",
    clip,
    id: clip.source_id,
    burnedIn: clip.source_id === ANNOTATED,
  });
  if (asked === TRACKS_ONLY) return { kind: "tracks", notice: CHOSEN, id: TRACKS_ONLY };
  if (asked === ANNOTATED && annotated) return video(annotated);
  const named = clips.find((clip) => clip.source_id === asked);
  if (named) return video(named);
  // Source footage first: the overlay is drawn live on it, where the annotated video has it burned in.
  if (best && coveredS(best, record) > 0) return video(best);
  if (annotated) return video(annotated);
  if (probe.status === "loading") return null;
  return { kind: "tracks", notice: noticeFor(probe), id: TRACKS_ONLY };
}

/**
 * What the frame carries, set on the frame: the claim stays with the pixels it describes, and the
 * heading keeps to one line, which the video's height is computed from.
 */
function FrameNote({
  record,
  mode,
  measured,
  burnedIn,
  inset,
  maskNote,
}: {
  record: AnalysisRecord;
  mode: OverlayMode;
  measured: boolean;
  /** The video carries its source's attribution along its bottom edge, so the note keeps clear. */
  burnedIn: boolean;
  /** The footage is the small view: the note goes under it, not across it. */
  inset: boolean;
  /** Which sampled frame the masks on screen came from. */
  maskNote: string | null;
}) {
  const reference = record.data_origin === "reference_dataset";
  const drawn = mode === "tracks" ? "Tracks" : "Boxes";
  const outside = isCalibrated(record) && !measured && !reference;
  if (mode === "off" && !outside) return null;
  const from = reference
    ? "projected from NGSIM positions"
    : isCalibrated(record)
      ? "from camera tracks"
      : "from camera tracks, image pixels only";
  const place = inset ? "border-t border-rule-2" : `absolute border border-rule-2 ${burnedIn ? "top-6 right-2" : "bottom-2 left-2"}`;
  return (
    <div className={`pointer-events-none flex flex-wrap items-baseline gap-x-4 bg-asphalt-1 px-2.5 py-1 ${place}`}>
      {mode === "masks" ? (
        <Link
          to="/evidence#what-sam-changed"
          className="mono pointer-events-auto inline-flex items-baseline gap-2 underline decoration-rule-2 underline-offset-4 hover:decoration-amber"
          style={{ color: EVIDENCE.model_generated.colour }}
        >
          <span className="translate-y-[2px]">
            <EvidenceGlyph kind="model_generated" />
          </span>
          SAM 3.1 masks, model-generated
        </Link>
      ) : mode === "off" ? null : (
        <EvidenceBadge kind={reference ? "approximated" : record.provenance.kind} note={inset ? drawn : `${drawn} ${from}`} />
      )}
      {maskNote ? <Label tone="body">{maskNote}</Label> : null}
      {outside ? <Label tone="body">Placement not checked at this moment</Label> : null}
      {reference && mode !== "off" ? <Label tone="body">{REFERENCE_SYNC_NOTE}</Label> : null}
    </div>
  );
}

function clipLabel(clip: FootageSource, loaded: LoadedAnalysis): string {
  const [from, to] = clipSpan(clip);
  const window = `Source ${from} to ${to} s`;
  return coveredS(clip, loaded.record) > 0 ? window : `${window}, outside this analysis`;
}

function AnalysisPanel({
  clips,
  footage,
  hasAnnotated,
  loaded,
  onFootage,
}: {
  clips: readonly FootageSource[];
  footage: Footage;
  hasAnnotated: boolean;
  loaded: LoadedAnalysis;
  onFootage: (id: string) => void;
  onLive?: () => void;
}) {
  const { record } = loaded;
  const stage = record.stages.find((item) => item.stage === "detect") ?? record.stages[0];
  const refined = record.stages.find((item) => item.stage === "sam_refine");
  const detector = stage ? `${stage.tool} · ${stage.version}` : undefined;
  const seconds = footage.kind === "video" ? coveredS(footage.clip, record) : analysisSpan(record)[1] - analysisSpan(record)[0];
  const options = [
    // A clip with none of this run in it would open on an empty frame, so it's offered only if already open.
    ...clips
      .filter((item) => coveredS(item, record) > 0 || item.source_id === footage.id)
      .map((item) => ({ value: item.source_id, label: clipLabel(item, loaded) })),
    ...(hasAnnotated ? [{ value: ANNOTATED, label: "Annotated video" }] : []),
    { value: TRACKS_ONLY, label: "Tracks only, no video" },
  ];
  return (
    <Panel title="Analysis" meta={<Label>{MODE_LABEL[record.mode]}</Label>}>
      <Field
        label="Footage"
        value={
          <Select label="Footage" value={footage.id} options={options} onChange={onFootage} className="w-64 max-w-full" />
        }
      />
      <Field
        label="Analysis covers"
        value={seconds > 0 ? `${seconds} s of this footage` : undefined}
        reason="None of this clip"
      />
      <Field label="Positions from" value={detector && refined ? `${detector}, refined by SAM 3.1` : detector} />
      <Field
        label="Calibration"
        value={isCalibrated(record) ? "Calibrated, approximate" : "Uncalibrated, image pixels only"}
        {...(isCalibrated(record) ? { evidence: "approximated" as const } : {})}
      />
      {isCalibrated(record) ? null : <p className="mt-2 leading-snug text-paint-2">{UNCALIBRATED_NOTE}</p>}
    </Panel>
  );
}

const SAM_ROW = "This frame's box is SAM's, model-generated, not the detector's";

function TrackList({
  tracks,
  selected,
  speed,
  onPick,
}: {
  tracks: readonly FrameTrack[];
  selected: number | null;
  /** False drops the column: a run with no stored speed has nothing to put in it. */
  speed: boolean;
  onPick: (trackId: number | null) => void;
}) {
  const row = `grid w-full ${
    speed ? "grid-cols-[3.5rem_minmax(0,1fr)_4rem_4.5rem]" : "grid-cols-[3.5rem_minmax(0,1fr)_4rem]"
  } items-baseline gap-x-3`;
  if (tracks.length === 0) {
    return <p className="mt-3 leading-snug text-paint-2">No track is on this frame.</p>;
  }
  return (
    <div className="mono min-h-0 flex-1 overflow-y-auto">
      {/* Inside the scroller: the header and the rows lose the same width to its scrollbar. */}
      <div className={`${row} sticky top-0 z-10 border-b border-rule-1 bg-asphalt-0 py-2 text-paint-3`}>
        <span>Track</span>
        <span>Class</span>
        <span>Movement</span>
        {speed ? <span className="text-right">Speed, m/s</span> : null}
      </div>
      <ul className="m-0 list-none p-0">
        {tracks.map(({ trackId, entry, speedMS, outsideRegion, samRefined }) => {
          const picked = trackId === selected;
          return (
            <li key={trackId} className="border-b border-rule-1">
              <button
                type="button"
                aria-pressed={picked}
                onClick={() => onPick(picked ? null : trackId)}
                className={`${row} py-1.5 text-left transition-colors duration-150 focus-visible:-outline-offset-2 ${
                  picked ? "text-amber" : "text-paint-2 hover:text-paint-1"
                }`}
              >
                <span className="inline-flex items-baseline gap-1.5">
                  {trackId}
                  {samRefined ? (
                    <span role="img" aria-label={SAM_ROW} title={SAM_ROW} className="translate-y-px">
                      <EvidenceGlyph kind="model_generated" size={9} />
                    </span>
                  ) : null}
                </span>
                <span className="truncate">
                  {entry.vehicle_class}
                  {outsideRegion ? <span className="text-paint-3"> outside region</span> : null}
                </span>
                <span>{entry.approach && entry.exit_arm ? `${entry.approach} to ${entry.exit_arm}` : "Partial"}</span>
                {speed ? (
                  <span className="text-right">{speedMS === null ? <Absent reason="No speed on this frame" quiet /> : speedMS.toFixed(1)}</span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Said beside the player, not over it: the tracks keep the whole frame. */
function FootageNotice({ notice }: { notice: TrackNotice }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-rule-1 py-2">
      <span className="eyebrow eyebrow-dot">{notice.title}</span>
      <span className="mono text-paint-2">{notice.why}</span>
      {notice.hint ? (
        <details className="basis-full">
          <summary className="mono cursor-pointer text-paint-3 underline decoration-rule-2 underline-offset-4 hover:text-paint-1">
            Rebuild it locally
          </summary>
          <code className="mono mt-1.5 block break-all text-paint-1 select-all">{notice.hint}</code>
          {notice.licence ? <p className="m-0 mt-1 text-micro leading-snug text-paint-3">{`That video ${notice.licence}.`}</p> : null}
        </details>
      ) : null}
    </div>
  );
}

/** The masks live on one 60 s window: one press loads it, and the stage opens on masks. */
function SamJump() {
  const { select } = useAnalysis();
  return (
    <div className="flex items-center justify-between gap-3 border-t border-rule-1 pt-2">
      <span className="mono text-paint-3">Masks: 60 s window</span>
      <Button tone="text" className="font-normal" onClick={() => select(SAM_ANALYSIS)}>
        Load SAM 3.1 masks
      </Button>
    </div>
  );
}

function MasksOn() {
  return (
    <p className="mono m-0 flex items-center gap-2 border-t border-rule-1 pt-2 text-paint-2">
      <span aria-hidden="true" className="lamp-live inline-block size-1.5 bg-ev-model-generated" />
      SAM 3.1 masks on this run
    </p>
  );
}

function ViewSwitch({ view, onView }: { view: View; onView: (view: View) => void }) {
  return <Segmented label="Vision Lab mode" options={VIEWS} value={view} onChange={onView} />;
}

function Ready({
  loaded,
  clips,
  footage,
  hasAnnotated,
  layer,
  onFootage,
  onLayer,
  onView,
}: {
  loaded: LoadedAnalysis;
  clips: readonly FootageSource[];
  footage: Footage;
  hasAnnotated: boolean;
  layer: Layer;
  onFootage: (id: string) => void;
  onLayer: (layer: Layer) => void;
  onView: (view: View) => void;
}) {
  const { state } = useAnalysis();
  const [page, size] = useElementSize<HTMLDivElement>();
  const video = useRef<HTMLVideoElement>(null);
  const burnedIn = footage.kind === "video" && footage.burnedIn;
  const [mode, setMode] = useState<OverlayMode>(burnedIn ? "off" : hasSamOutlines(loaded.record) ? "masks" : "boxes");
  const landed = useRef(false);
  const [reading, setReading] = useState<TransportState>(NO_MEDIA);
  const [tracks, setTracks] = useState<FrameTrack[]>([]);
  const [failed, setFailed] = useState(false);
  const { record } = loaded;
  // One selection with the 3D layer: a vehicle picked in either is picked in both.
  const pointedAt = useSelection();
  const selected = pointedAt.analysisId === record.analysis_id ? pointedAt.trackId : null;
  const outlined = hasSamOutlines(record);
  const outlines = useSamOutlines(outlined ? record.analysis_id : null);
  const outlineFile = outlines.status === "ready" ? outlines.data : null;
  const masks = useMemo(() => (outlineFile ? indexMasks(outlineFile) : null), [outlineFile]);
  const aspect = record.width_px / record.height_px;
  const shown: Footage = failed ? { kind: "tracks", notice: PLAY_FAILED, id: footage.id } : footage;
  const trackOnly = shown.kind === "tracks";
  const wanted: OverlayMode = mode === "masks" && !masks ? "boxes" : mode;
  const overlayMode: OverlayMode = trackOnly && wanted === "off" ? "boxes" : wanted;
  // A layer on the ground needs a ground: an uncalibrated run has only image pixels.
  const placeable = isCalibrated(record);
  const geo = layer === "geo" && placeable;
  const maskNote = overlayMode === "masks" && masks && reading.frame !== null ? maskFrameNote(masks, reading.frame) : null;
  const samRows = tracks.filter((track) => track.samRefined).length;
  const speedStored = findMetric(record, "speed.mean_moving")?.value != null;
  const onFrame = samRows > 0 ? `${tracks.length} on frame · ${samRows} SAM boxes` : `${tracks.length} on frame`;

  const videoWidth = Math.max(
    320,
    Math.min(
      (size.height - HEAD_PX - FOOT_PX - (trackOnly ? NOTICE_PX : 0)) * aspect,
      size.width - SIDE_MIN_PX - 88,
    ),
  );

  // Leaving this page leaves the twin on the same moment of the same analysis.
  useEffect(() => {
    clock.configure(record.fps, record.frame_count);
  }, [record]);
  useEffect(() => {
    if (trackOnly) return;
    clock.pause(); // a video is the master here: the clock follows it
  }, [trackOnly]);
  useEffect(() => {
    if (trackOnly) return;
    const frame = reading.frame;
    if (frame !== null && frame >= 0 && frame < record.frame_count) clock.seek(frame);
  }, [reading.frame, record, trackOnly]);

  function pick(trackId: number | null) {
    selectTrack(record.analysis_id, trackId);
  }

  /** Asked for while stopped, masks land on the frame they were sampled on, where they fit the picture. */
  function chooseOverlay(next: OverlayMode) {
    setMode(next);
    const sample = next === "masks" && masks && reading.frame !== null ? nearestMask(masks, reading.frame) : null;
    if (sample && sample.distance > 0 && reading.frame !== null && !reading.playing) {
      transport.onStep(sample.frame - reading.frame);
    }
  }

  const startS = shown.kind === "video" ? (shown.clip.source_start_s ?? 0) : record.source_start_s;
  const measured = insideMeasuredWindow(record, startS + (reading.currentS ?? 0));
  const fps = (shown.kind === "video" ? shown.clip.fps : null) ?? record.fps;

  const transport = trackOnly
    ? {
        onToggle: () => clock.toggle(),
        onStep: (frames: number) => {
          clock.pause();
          clock.seek(Math.round(clock.get()) + frames);
        },
        onSeek: (seconds: number) => clock.seek(seconds * record.fps),
      }
    : {
        onToggle: () => {
          const element = video.current;
          if (!element) return;
          if (element.paused) void element.play();
          else element.pause();
        },
        onStep: (frames: number) => {
          const element = video.current;
          if (!element) return;
          element.pause();
          element.currentTime = Math.max(0, element.currentTime + frames / fps);
        },
        onSeek: (seconds: number) => {
          if (video.current) video.current.currentTime = seconds;
        },
      };

  // Opened on masks, the frame moves to one they were sampled on, once, so the first look is a good one.
  useEffect(() => {
    if (landed.current || overlayMode !== "masks" || !masks || reading.frame === null || reading.playing) return;
    landed.current = true;
    const sample = nearestMask(masks, reading.frame);
    if (sample && sample.distance > 0) transport.onStep(sample.frame - reading.frame);
  });

  return (
    <div
      ref={page}
      className="grid min-h-full grid-cols-1 lg:h-full lg:grid-cols-[calc(var(--video-w)+var(--margin))_minmax(0,1fr)]"
      style={{ ["--video-w" as string]: `${videoWidth}px` }}
    >
      <div className="flex min-h-0 min-w-0 flex-col pl-margin max-lg:pr-margin">
        <PageHead
          title="Vision Lab"
          sub={<Label>{analysisLabel(state)}</Label>}
          meta={
            burnedIn ? (
              <Label tone="body">Boxes are burned into this video</Label>
            ) : (
              <Segmented
                label="Overlay on the frame"
                options={overlayOptions(trackOnly, outlined, masks !== null)}
                value={overlayMode}
                onChange={chooseOverlay}
              />
            )
          }
        />
        <Instrument className="relative w-full">
          <div
            className={`relative ${geo ? "lg:aspect-[var(--stage-aspect)]" : "aspect-[var(--stage-aspect)]"}`}
            style={{ ["--stage-aspect" as string]: aspect }}
          >
            {geo ? (
              <div className={GEO_LAYER}>
                <GeoLayer analysisId={record.analysis_id} />
              </div>
            ) : null}
            <div className={geo ? GEO_FOOTAGE : "absolute inset-0"}>
              <div className="relative aspect-[var(--stage-aspect)]">
                {shown.kind === "video" ? (
                  <ClipStage
                    clip={shown.clip}
                    loaded={loaded}
                    mode={overlayMode}
                    selected={selected}
                    masks={masks}
                    videoRef={video}
                    onReading={setReading}
                    onTracks={setTracks}
                    onPick={pick}
                    onFailed={() => setFailed(true)}
                  />
                ) : (
                  <>
                    <TrackStage
                      loaded={loaded}
                      mode={overlayMode}
                      selected={selected}
                      masks={masks}
                      onReading={setReading}
                      onTracks={setTracks}
                      onPick={pick}
                    />
                    <FrameCorners />
                    <p
                      className="eyebrow pointer-events-none absolute top-2 left-2 m-0 flex items-center gap-2 bg-asphalt-0/80 px-2 py-1"
                      title="Every stored position of every track, drawn faintly. No video pixels."
                    >
                      <span aria-hidden="true" className="lamp-live inline-block size-1.5 bg-ev-approximated" />
                      Long exposure · every stored track · no video
                    </p>
                  </>
                )}
              </div>
              <FrameNote
                record={record}
                mode={overlayMode}
                measured={measured}
                burnedIn={shown.kind === "video" && shown.burnedIn}
                inset={geo}
                maskNote={maskNote}
              />
            </div>
          </div>
        </Instrument>
        <Instrument>
          {shown.kind === "tracks" ? <FootageNotice notice={shown.notice} /> : null}
          <Transport state={reading} reason="Transport idle: footage not ready" {...transport} />
          {/* The record's attribution text, shown wherever its pixels appear. */}
          <p className="truncate pb-3 text-micro leading-snug text-paint-3" title={`${record.source.licence_name}. ${record.source.attribution}`}>
            {reading.frame !== null ? `Source second ${(record.source_start_s + reading.frame / record.fps).toFixed(1)}. ` : ""}
            {record.source.licence_name}. {record.source.attribution}
          </p>
        </Instrument>
      </div>

      <Instrument className="flex min-h-0 min-w-0 flex-col gap-6 px-margin pt-8 pb-5 lg:pl-6">
        <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2">
          <ViewSwitch view="playback" onView={onView} />
          {placeable ? <Segmented label="What fills the stage" options={LAYERS} value={geo ? "geo" : "footage"} onChange={onLayer} /> : null}
        </div>
        <div className="shrink-0">
          <AnalysisPanel
            clips={clips}
            footage={footage}
            hasAnnotated={hasAnnotated}
            loaded={loaded}
            onFootage={onFootage}
            onLive={() => onView("live")}
          />
        </div>
        <div className="relative shrink-0 border border-rule-1 bg-asphalt-1 px-4 pt-3 pb-2">
          <span aria-hidden="true" className="absolute inset-x-0 top-0 h-px bg-ev-model-generated/60" />
          <MetaModels large footer={outlined ? <MasksOn /> : <SamJump />} />
        </div>
        <Panel
          title="Tracks on this frame"
          meta={<Label>{speedStored ? onFrame : `${onFrame} · speed withheld`}</Label>}
          className="flex min-h-48 flex-1 flex-col lg:min-h-0"
        >
          <TrackList tracks={tracks} selected={selected} speed={speedStored} onPick={pick} />
        </Panel>
      </Instrument>
    </div>
  );
}

/** The live job: real annotated frames and the worker's own figures, with the form beside them. */
function Live({ onView }: { onView: (view: View) => void }) {
  const { state } = useAnalysis();
  const { run } = useLive();
  const [page, size] = useElementSize<HTMLDivElement>();
  const videoWidth = Math.max(
    320,
    Math.min((size.height - HEAD_PX - PROGRESS_PX) * DEFAULT_ASPECT, size.width - SIDE_MIN_PX - 88),
  );

  return (
    <div
      ref={page}
      className="grid min-h-full grid-cols-1 lg:h-full lg:grid-cols-[calc(var(--video-w)+var(--margin))_minmax(0,1fr)]"
      style={{ ["--video-w" as string]: `${videoWidth}px` }}
    >
      <div className="flex min-h-0 min-w-0 flex-col pl-margin max-lg:pr-margin">
        <PageHead title="Vision Lab" sub={<Label>{analysisLabel(state)}</Label>} />
        <Instrument className="relative w-full">
          <LiveStage run={run} aspect={DEFAULT_ASPECT} />
        </Instrument>
        <Instrument>{run ? <RunProgress run={run} /> : null}</Instrument>
      </div>

      <Instrument className="flex min-h-0 min-w-0 flex-col gap-6 px-margin pt-8 pb-5 lg:overflow-y-auto lg:pl-6">
        <div className="shrink-0">
          <ViewSwitch view="live" onView={onView} />
        </div>
        <div className="shrink-0">
          <LivePanel onReview={() => onView("playback")} />
        </div>
        <div className="shrink-0">
          <Panel title="Recent jobs">
            <RecentJobs />
          </Panel>
        </div>
      </Instrument>
    </div>
  );
}

/** Every state that has no analysis to play: one state at a time, on the same stage. */
function NoFootage({ children }: { children: React.ReactNode }) {
  const { state } = useAnalysis();
  return (
    <div className="page flex min-h-full flex-col pb-margin lg:h-full">
      <PageHead title="Vision Lab" sub={<Label>{analysisLabel(state)}</Label>} />
      <Instrument className="relative flex min-h-[56dvh] flex-1 items-end p-8">
        <FrameCorners />
        <div className="relative">{children}</div>
      </Instrument>
    </div>
  );
}

export function VisionLab() {
  const { state, retry } = useAnalysis();
  const sources = useSources();
  const [params, setParams] = useSearchParams();
  const asked = params.get("clip");
  const view: View = params.get("view") === "live" ? "live" : "playback";
  const layer: Layer = params.get("layer") === "geo" ? "geo" : "footage";
  const ready = state.status === "ready" ? state.loaded : null;
  const probe = useAnnotatedVideo(ready?.record.analysis_id ?? null);

  const clips = useMemo(() => {
    if (!ready || sources.status !== "ready") return [];
    return footageFor(ready.record, sources.data.filter((item) => item.playable && item.video_url));
  }, [sources, ready]);
  const footage = useMemo(
    () => (ready && sources.status !== "loading" ? chooseFootage(ready.record, clips, asked, probe) : null),
    [ready, sources.status, clips, asked, probe],
  );

  // A clip asked for under the last analysis may hold none of this one: it would open on an empty frame.
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (!ready) return;
    const id = ready.record.analysis_id;
    const changed = seen.current !== null && seen.current !== id;
    seen.current = id;
    const clip = clips.find((item) => item.source_id === asked);
    if (changed && clip && coveredS(clip, ready.record) === 0) setParam("clip", null);
  });

  function setParam(name: string, value: string | null) {
    setParams(
      (current) => {
        if (value === null) current.delete(name);
        else current.set(name, value);
        return current;
      },
      { replace: true },
    );
  }
  const onView = (next: View) => setParam("view", next === "live" ? "live" : null);

  // A job needs no analysis to start from: it is how the first one gets made.
  if (view === "live") return <Live onView={onView} />;

  if (state.status === "error") {
    return (
      <NoFootage>
        <ErrorState size="stage" what="Couldn't load the footage." lastGood={state.lastGood} onRetry={retry} />
      </NoFootage>
    );
  }
  if (state.status === "none") {
    return (
      <NoFootage>
        <EmptyState
          size="stage"
          title="No analysis loaded"
          reason="Every box drawn here traces to an analysis record."
          action={
            <>
              <ButtonLink to="/intersections">Choose an analysis</ButtonLink>
              <Button onClick={() => onView("live")}>Run a live analysis</Button>
            </>
          }
        />
      </NoFootage>
    );
  }
  if (state.status === "loading" || !ready || !footage) {
    return (
      <NoFootage>
        <EmptyState size="stage" title="Loading footage" />
        <div className="mt-4 w-72">
          <SkeletonRows rows={2} />
        </div>
      </NoFootage>
    );
  }

  return (
    <Ready
      key={`${ready.record.analysis_id}-${footage.id}`}
      loaded={ready}
      clips={clips}
      footage={footage}
      hasAnnotated={probe.status === "ready" && probe.data.available}
      layer={layer}
      onFootage={(id) => setParam("clip", id)}
      onLayer={(next) => setParam("layer", next === "geo" ? "geo" : null)}
      onView={onView}
    />
  );
}
