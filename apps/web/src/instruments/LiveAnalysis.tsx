import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Absent,
  Button,
  ButtonLink,
  EmptyState,
  ErrorState,
  EvidenceBadge,
  Field,
  Label,
  NumberField,
  Panel,
  ProgressBar,
  Select,
  SkeletonRows,
} from "../design";
import { HELD_OUT_WHY, offeredWindows, plannedFrames, readWindow, windowWords, type Span } from "../domain/heldOut";
import {
  ApiError,
  isAbort,
  isFinished,
  jobPreviewUrl,
  uploadVideo,
  type FootageSource,
  type Job,
  type JobStatus,
  type Upload,
} from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { useLive, type LiveRun } from "../state/live";
import { useHealth, useSources } from "../state/resources";

/** How long a new run is by default. Short, because the GPU is shared. */
const SHORT_RUN_S = 10;
const RECENT_ROWS = 6;

const STAGE_NAMES: Record<string, string> = {
  load: "Loading the detector",
  hash: "Hashing the video",
  detect: "Detecting and tracking",
  relink: "Joining track fragments",
  place: "Placing and classifying",
  compute: "Computing metrics",
  write: "Writing the analysis",
  done: "Finished",
  failed: "Stopped",
};

const STATUS_WORDS: Record<JobStatus, { word: string; mark: string }> = {
  queued: { word: "Queued", mark: "bg-paint-3" },
  running: { word: "Running", mark: "bg-amber" },
  succeeded: { word: "Succeeded", mark: "bg-go" },
  failed: { word: "Failed", mark: "bg-stop" },
  cancelled: { word: "Cancelled", mark: "bg-paint-3" },
  interrupted: { word: "Interrupted", mark: "bg-stop" },
};

export const UNCALIBRATED_NOTE =
  "Uncalibrated run. Boxes and tracks stay in image pixels: no ground positions, arms, turns or flows. " +
  "The Digital Twin can't place these vehicles.";

const seconds = (value: number) => value.toLocaleString("en-US", { maximumFractionDigits: 3 });

function firstSentence(text: string): string {
  return text.split(/(?<=[.:])\s/)[0]?.replace(/:$/, ".") ?? text;
}

/** What happened and what to do about it. The server's own words are always shown beside this. */
export function explainFailure(job: Job): { what: string; todo: string } {
  const reason = job.error ?? "";
  if (job.status === "cancelled") {
    return { what: "Cancelled. No analysis was recorded.", todo: "Start another run when you are ready." };
  }
  if (job.status === "interrupted") {
    return { what: "The server stopped during this job.", todo: "No analysis was recorded. Start the job again." };
  }
  if (reason.includes("HeldOutError")) {
    return { what: "The worker refused this window: it reaches held-out footage.", todo: HELD_OUT_WHY };
  }
  if (/out of memory/i.test(reason)) {
    return {
      what: "The GPU ran out of memory.",
      todo: "Wait for other GPU work to finish, or raise the frame stride, then start again.",
    };
  }
  if (/ModuleNotFoundError|ImportError|No module named/.test(reason)) {
    return {
      what: "The vision environment lacks a package the detector needs.",
      todo: "Install it in the environment CAIROFLOW_VISION_PYTHON points to, then start again.",
    };
  }
  if (/FrameStreamError|cannot open|no frame/i.test(reason)) {
    return { what: "The worker couldn't read frames from this video.", todo: "Check the file, or choose another source." };
  }
  if (reason.includes("failed verification")) {
    return {
      what: "The worker wrote an analysis that failed verification, so it was not loaded.",
      todo: `Read var/jobs/${job.id}/worker.log on the server, fix the cause, then start again.`,
    };
  }
  return {
    what: "The job failed.",
    todo: `Read var/jobs/${job.id}/worker.log on the server, fix the cause, then start again.`,
  };
}

/** Viewfinder corners: the only marks on an empty stage. */
export function FrameCorners() {
  const corner = "absolute size-6 border-paint-4";
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      <span className={`${corner} top-0 left-0 border-t border-l`} />
      <span className={`${corner} top-0 right-0 border-t border-r`} />
      <span className={`${corner} bottom-0 left-0 border-b border-l`} />
      <span className={`${corner} right-0 bottom-0 border-r border-b`} />
    </div>
  );
}

/**
 * The newest annotated frame. A frame number only asks for the image; the one on screen changes
 * when the next has fully arrived, so the picture never blanks, and only the worker can move it on.
 */
function useLatestPreview(jobId: string | null, frame: number | null): string | null {
  const [shown, setShown] = useState<string | null>(null);
  const wanted = useRef<string | null>(null);
  const loading = useRef(false);

  useEffect(() => setShown(null), [jobId]);

  useEffect(() => {
    if (jobId === null || frame === null) return;
    wanted.current = jobPreviewUrl(jobId, frame);
    const fetchNext = () => {
      const url = wanted.current;
      if (loading.current || url === null) return;
      loading.current = true;
      const image = new Image();
      image.onload = () => {
        loading.current = false;
        setShown(url);
        if (wanted.current !== url) fetchNext();
      };
      image.onerror = () => {
        loading.current = false;
      };
      image.src = url;
    };
    fetchNext();
  }, [jobId, frame]);

  return shown;
}

/** The stage: real annotated frames as the worker writes them, or a plain word for why there are none. */
export function LiveStage({ run, aspect }: { run: LiveRun | null; aspect: number }) {
  const shown = useLatestPreview(run?.job.id ?? null, run?.previewFrame ?? null);
  const waiting = run
    ? run.job.status === "queued"
      ? "Queued. The worker runs one job at a time."
      : isFinished(run.job.status)
        ? "This job left no annotated frame."
        : "Waiting for the first annotated frame."
    : null;
  return (
    <>
      <div className="relative bg-asphalt-1" style={{ aspectRatio: aspect }}>
        {shown ? (
          <img
            src={shown}
            alt={`Annotated frame ${run?.progress?.frame ?? ""} of the running job`}
            className="absolute inset-0 size-full object-fill"
          />
        ) : (
          <div className="absolute inset-0 flex items-end p-6">
            <FrameCorners />
            {waiting ? (
              <EmptyState title={waiting} />
            ) : (
              <EmptyState
                size="stage"
                title="No live run"
                reason="Start a job and its annotated frames appear here as the worker writes them."
              />
            )}
          </div>
        )}
      </div>
      {/* Below the frame, not on it: the frame carries its source's attribution along its bottom edge. */}
      {shown ? (
        <div className="pt-1.5">
          <EvidenceBadge kind="approximated" note="Boxes from a detector, not ground truth" />
        </div>
      ) : null}
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | undefined }) {
  return (
    <div className="min-w-0">
      <dt>
        <Label>{label}</Label>
      </dt>
      <dd className="mono m-0 mt-1 text-paint-1">
        {value === undefined ? <Absent reason={`${label}: not reported yet`} quiet /> : value}
      </dd>
    </div>
  );
}

/** The worker's last report, as it wrote it. A field it has not sent stays absent. */
export function RunProgress({ run }: { run: LiveRun }) {
  const report = run.progress;
  const total = report?.frames_total;
  const frame = report?.frame;
  const stage = report ? (STAGE_NAMES[report.stage] ?? report.stage) : "No report from the worker yet";
  return (
    <div className="pt-3 pb-2" aria-live="polite">
      <div className="flex items-baseline justify-between gap-4">
        <Label tone="paint">{stage}</Label>
        <Label>
          {frame !== undefined && total !== undefined ? `Frame ${frame.toLocaleString("en-US")} of ${total.toLocaleString("en-US")}` : ""}
        </Label>
      </div>
      <div className="mt-2">
        {frame !== undefined && total !== undefined ? (
          <ProgressBar label="Frames done" value={frame} max={total} />
        ) : (
          <div role="presentation" className="border-t border-dashed border-paint-4" />
        )}
      </div>
      <dl className="m-0 mt-3 grid grid-cols-4 gap-4">
        <Stat label="Frames per second" value={report?.fps?.toLocaleString("en-US")} />
        <Stat label="Detections" value={report?.detections?.toLocaleString("en-US")} />
        <Stat label="Tracks now" value={report?.tracks?.toLocaleString("en-US")} />
        <Stat label="Elapsed" value={report?.elapsed_s === undefined ? undefined : `${report.elapsed_s.toFixed(1)} s`} />
      </dl>
      {run.connected ? null : (
        <p className="mt-2 leading-snug text-paint-2">The event stream dropped. The browser is reconnecting.</p>
      )}
    </div>
  );
}

function Notice({ tone = "note", children }: { tone?: "note" | "stop"; children: ReactNode }) {
  return (
    <div
      role={tone === "stop" ? "alert" : undefined}
      className={`mt-3 border-l-2 pl-3 leading-snug text-paint-2 ${tone === "stop" ? "border-stop" : "border-amber"}`}
    >
      {children}
    </div>
  );
}

function Outcome({ run, uncalibrated, onReview }: { run: LiveRun; uncalibrated: boolean; onReview: () => void }) {
  const { job } = run;
  const { clear } = useLive();
  if (!isFinished(job.status)) return null;
  if (job.status === "succeeded") {
    return (
      <Notice>
        <p className="m-0 text-paint-1">Analysis written and loaded across the app.</p>
        <p className="mono m-0 mt-1 break-all text-paint-3">{job.analysis_id}</p>
        {uncalibrated ? <p className="m-0 mt-1">{UNCALIBRATED_NOTE}</p> : null}
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
          <Button tone="text" onClick={onReview}>
            Review the tracks
          </Button>
          <ButtonLink to="/evidence" tone="text">
            Read the evidence
          </ButtonLink>
          <Button tone="text" onClick={clear}>
            Clear
          </Button>
        </div>
      </Notice>
    );
  }
  const { what, todo } = explainFailure(job);
  return (
    <Notice tone="stop">
      <p className="m-0 text-paint-1">{what}</p>
      <p className="m-0 mt-1">{todo}</p>
      {job.error ? <p className="mono m-0 mt-2 break-words text-paint-3">{`The server said: ${job.error}`}</p> : null}
      <Button tone="text" className="mt-2" onClick={clear}>
        Clear
      </Button>
    </Notice>
  );
}

function UploadControl({ disabled, onUploaded }: { disabled: boolean; onUploaded: (source: FootageSource) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const handle = useRef<Upload | null>(null);
  const [sending, setSending] = useState<{ name: string; sent: number; total: number; checking: boolean } | null>(null);
  const [refused, setRefused] = useState<string | null>(null);

  useEffect(() => () => handle.current?.abort(), []);

  function send(file: File) {
    setRefused(null);
    setSending({ name: file.name, sent: 0, total: file.size, checking: false });
    const upload = uploadVideo(file, {
      onProgress: (sent, total) => setSending((held) => held && { ...held, sent, total }),
      onSent: () => setSending((held) => held && { ...held, checking: true }),
    });
    handle.current = upload;
    upload.done
      .then(onUploaded)
      .catch((error: unknown) => {
        if (!isAbort(error)) setRefused(error instanceof Error ? error.message : "The upload failed.");
      })
      .finally(() => {
        handle.current = null;
        setSending(null);
      });
  }

  const megabytes = (bytes: number) => (bytes / 1e6).toFixed(1);
  return (
    <div className="mt-2">
      <input
        ref={input}
        type="file"
        accept="video/*,.avi,.mkv"
        aria-label="Video file to upload"
        className="sr-only"
        tabIndex={-1}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) send(file);
        }}
      />
      {sending ? (
        <div>
          <div className="flex items-baseline justify-between gap-4">
            <Label tone="body" className="truncate">
              {sending.checking ? `Uploaded. The server is checking ${sending.name}.` : `Uploading ${sending.name}`}
            </Label>
            <Label className="shrink-0">{`${megabytes(sending.sent)} of ${megabytes(sending.total)} MB`}</Label>
          </div>
          <div className="mt-1.5">
            <ProgressBar label="Bytes uploaded" value={sending.sent} max={sending.total} />
          </div>
          <Button tone="text" className="mt-1.5" onClick={() => handle.current?.abort()}>
            Cancel upload
          </Button>
        </div>
      ) : (
        <Button tone="text" disabledReason={disabled ? "Finish the running job first" : undefined} onClick={() => input.current?.click()}>
          Upload a video
        </Button>
      )}
      {refused ? (
        <Notice tone="stop">
          <p className="m-0 text-paint-1">The server refused this upload.</p>
          <p className="m-0 mt-1 break-words">{refused}</p>
        </Notice>
      ) : null}
    </div>
  );
}

/** A window to start from: inside the measured window when this file reaches it, short either way. */
function suggestWindow(source: FootageSource, measured: readonly Span[]): { startS: number; durationS: number } {
  const offset = source.source_start_s ?? 0;
  const offered = offeredWindows(source);
  const wanted = measured.flatMap(([from]) => {
    const fileS = from - offset;
    const window = offered.find(([low, high]) => fileS >= low && fileS < high);
    return window ? [{ startS: fileS, durationS: Math.min(SHORT_RUN_S, window[1] - fileS) }] : [];
  })[0];
  if (wanted) return wanted;
  const first = offered[0];
  if (first) return { startS: first[0], durationS: Math.min(SHORT_RUN_S, first[1] - first[0]) };
  return { startS: 0, durationS: Math.min(SHORT_RUN_S, source.duration_s ?? SHORT_RUN_S) };
}

function SourceFacts({ source }: { source: FootageSource }) {
  return (
    <>
      <Field
        label="Length"
        value={source.duration_s === null ? undefined : `${seconds(source.duration_s)} s`}
        reason="Not known"
      />
      <Field
        label="Calibration"
        value={source.calibrated ? "Calibrated, approximate" : "Uncalibrated, image pixels only"}
        {...(source.calibrated ? { evidence: "approximated" as const } : {})}
      />
      {source.calibration_note ? <Notice>{source.calibration_note}</Notice> : null}
      {source.calibrated ? null : <Notice>{UNCALIBRATED_NOTE}</Notice>}
      {source.problem ? (
        <Notice tone="stop">
          <p className="m-0 text-paint-1">This source can't be analysed.</p>
          <p className="m-0 mt-1">{source.problem}</p>
        </Notice>
      ) : null}
    </>
  );
}

/** The job form: a source, a window and a stride, and everything that stops them running. */
function RunForm({ sources, onUploaded }: { sources: readonly FootageSource[]; onUploaded: (source: FootageSource) => void }) {
  const { state } = useAnalysis();
  const { run, start, cancel } = useLive();
  const health = useHealth();
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [startText, setStartText] = useState("");
  const [durationText, setDurationText] = useState("");
  const [strideText, setStrideText] = useState("1");
  const [refused, setRefused] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const source = sources.find((item) => item.source_id === sourceId) ?? sources[0] ?? null;
  const measured = useMemo<readonly Span[]>(() => {
    if (state.status !== "ready") return [];
    return (state.loaded.record.calibration.valid_windows_s ?? []).map(([from, to]): Span => [Number(from), Number(to)]);
  }, [state]);

  const shownSource = source?.source_id ?? null;
  /** The source whose window the operator has set: nothing may undo their typing. */
  const edited = useRef<string | null>(null);
  useEffect(() => {
    const picked = sources.find((item) => item.source_id === shownSource);
    if (!picked || edited.current === shownSource) return;
    const { startS, durationS } = suggestWindow(picked, measured);
    setStartText(String(startS));
    setDurationText(String(durationS));
    setRefused(null);
    // The list refreshes under a running form; only the source and the measured window matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownSource, measured]);

  const typed = (set: (value: string) => void) => (value: string) => {
    edited.current = shownSource;
    set(value);
  };

  if (!source) {
    return (
      <>
        <EmptyState title="No footage on this server" reason="Upload a video to analyse it." />
        <UploadControl disabled={false} onUploaded={onUploaded} />
      </>
    );
  }

  const startS = Number(startText);
  const durationS = Number(durationText);
  const stride = Number(strideText);
  const window = readWindow(source, startS, durationS);
  const offered = offeredWindows(source);
  const active = run !== null && !isFinished(run.job.status);
  const worker = health.status === "ready" ? health.data : null;
  const workerProblem = worker && !worker.vision_worker_available ? worker.vision_worker_problem : null;

  const blocked = active
    ? "A job is running"
    : source.problem
      ? "This source can't be analysed"
      : window.problem
        ? firstSentence(window.problem)
        : !Number.isInteger(stride) || stride < 1
          ? "The frame stride must be a whole number, 1 or more"
          : workerProblem
            ? firstSentence(workerProblem)
            : health.status === "error"
              ? "The server did not report on its vision worker"
              : health.status === "loading"
                ? "Checking the vision worker"
                : starting
                  ? "Starting"
                  : undefined;

  async function begin() {
    if (!source) return;
    setRefused(null);
    setStarting(true);
    try {
      await start({ source_id: source.source_id, start_s: startS, duration_s: durationS, frame_stride: stride });
    } catch (error) {
      setRefused(error instanceof Error ? error.message : "The server refused this job.");
      if (error instanceof ApiError && error.status === 503) health.retry();
    } finally {
      setStarting(false);
    }
  }

  const framesToRead = source.fps && durationS > 0 && stride >= 1 ? plannedFrames(source.fps, durationS, stride) : null;

  return (
    <>
      <Field
        label="Source"
        value={
          <Select
            label="Source video"
            value={source.source_id}
            options={sources.map((item) => ({
              value: item.source_id,
              label: item.problem ? `${item.title} (can't run)` : item.title,
            }))}
            onChange={setSourceId}
            className="w-64 max-w-full"
          />
        }
      />
      <UploadControl
        disabled={active}
        onUploaded={(uploaded) => {
          setSourceId(uploaded.source_id);
          onUploaded(uploaded);
        }}
      />
      <SourceFacts source={source} />

      <NumberField label="Start" unit="s" value={startText} min={0} step="any" disabled={active} onChange={typed(setStartText)} />
      <NumberField label="Duration" unit="s" value={durationText} min={0} step="any" disabled={active} onChange={typed(setDurationText)} />
      <NumberField label="Frame stride" value={strideText} min={1} step={1} disabled={active} onChange={setStrideText} />
      <Field
        label="Source seconds"
        value={window.sourceSpan ? windowWords(window.sourceSpan) : undefined}
        reason="No place in the camera 2 source"
      />
      <Field label="Frames to read" value={framesToRead?.toLocaleString("en-US")} reason="Set a window and a stride" />

      {window.problem ? (
        <Notice tone="stop">
          <p className="m-0 text-paint-1">{window.problem}</p>
          {window.heldOut.length > 0 ? <p className="m-0 mt-1">{HELD_OUT_WHY}</p> : null}
        </Notice>
      ) : null}
      {offered.length > 0 && (window.heldOut.length > 0 || window.problem) ? (
        <div className="mt-2">
          <Label className="block">Windows this file can run</Label>
          <ul className="m-0 mt-1 flex list-none flex-wrap gap-x-4 gap-y-1 p-0">
            {offered.map(([from, to]) => {
              const offset = source.source_start_s ?? 0;
              return (
                <li key={from}>
                  <Button
                    tone="text"
                    onClick={() => {
                      typed(setStartText)(String(from));
                      typed(setDurationText)(String(Math.min(SHORT_RUN_S, to - from)));
                    }}
                  >
                    {`Inside ${windowWords([from + offset, to + offset])}`}
                  </Button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {workerProblem ? (
        <Notice tone="stop">
          <p className="m-0 text-paint-1">The vision worker can't run on this server.</p>
          <p className="mono m-0 mt-1 break-words">{workerProblem}</p>
        </Notice>
      ) : null}
      {refused ? (
        <Notice tone="stop">
          <p className="m-0 text-paint-1">The server refused this job.</p>
          <p className="m-0 mt-1 break-words">{refused}</p>
        </Notice>
      ) : null}

      <div className="mt-4">
        {active ? (
          <Button
            block
            disabledReason={run.job.cancelling ? "Stopping the worker" : undefined}
            onClick={() => {
              cancel().catch((error: unknown) => {
                setRefused(error instanceof Error ? error.message : "The job could not be cancelled.");
              });
            }}
          >
            {run.job.cancelling ? "Cancelling" : "Cancel"}
          </Button>
        ) : (
          <Button tone="primary" block disabledReason={blocked} onClick={() => void begin()}>
            Run live analysis
          </Button>
        )}
      </div>
    </>
  );
}

export function LivePanel({ onReview }: { onReview: () => void }) {
  const { run } = useLive();
  const { state } = useAnalysis();
  const sources = useSources();
  const worker = useHealth();
  const uncalibrated = state.status === "ready" && state.loaded.record.calibration.mode === "uncalibrated";
  return (
    <Panel
      title="Live analysis"
      meta={
        worker.status === "ready" ? (
          <Label>{worker.data.vision_worker_available ? "Vision worker ready" : "Vision worker unavailable"}</Label>
        ) : undefined
      }
    >
      {sources.status === "loading" ? (
        <SkeletonRows rows={5} />
      ) : sources.status === "error" ? (
        <ErrorState what="Couldn't read the footage catalog." onRetry={sources.retry} className="mt-3" />
      ) : (
        <RunForm sources={sources.data} onUploaded={() => sources.retry()} />
      )}
      {run ? <Outcome run={run} uncalibrated={uncalibrated} onReview={onReview} /> : null}
    </Panel>
  );
}

function timeOf(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
}

export function RecentJobs() {
  const { recent, refresh } = useLive();
  const { state, select } = useAnalysis();
  const sources = useSources();
  const active = state.status === "ready" ? state.analysis.id : null;
  const titleOf = (job: Job) =>
    (sources.status === "ready" ? sources.data.find((item) => item.source_id === job.source_id)?.title : null) ?? job.source_id;

  if (recent.status === "loading") return <SkeletonRows rows={2} />;
  if (recent.status === "error") return <ErrorState what="Couldn't read the job list." onRetry={refresh} className="mt-3" />;
  if (recent.jobs.length === 0) return <p className="mt-3 leading-snug text-paint-2">No job has run on this server yet.</p>;
  return (
    <ul className="m-0 list-none p-0">
      {recent.jobs.slice(0, RECENT_ROWS).map((job) => {
        const status = STATUS_WORDS[job.status];
        return (
          <li key={job.id} className="border-b border-rule-1 py-2">
            <div className="flex items-baseline justify-between gap-3">
              <span className="mono min-w-0 truncate text-paint-1">{titleOf(job)}</span>
              <span className="mono inline-flex shrink-0 items-center gap-1.5 text-paint-2">
                <span aria-hidden="true" className={`inline-block size-2 ${status.mark}`} />
                {status.word}
              </span>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <Label>{`${seconds(job.start_s)} s for ${seconds(job.duration_s)} s, stride ${job.frame_stride}, ${timeOf(job.created_at)} UTC`}</Label>
              {job.analysis_id ? (
                job.analysis_id === active ? (
                  <Label tone="body" className="shrink-0">
                    Loaded
                  </Label>
                ) : (
                  <Button tone="text" className="shrink-0" onClick={() => select(job.analysis_id as string)}>
                    Load
                  </Button>
                )
              ) : null}
            </div>
            {job.error ? <p className="mt-1 leading-snug break-words text-paint-3">{firstSentence(job.error)}</p> : null}
          </li>
        );
      })}
    </ul>
  );
}
