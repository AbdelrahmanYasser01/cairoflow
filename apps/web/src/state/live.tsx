import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  cancelJob,
  isFinished,
  listJobs,
  startJob,
  watchJob,
  type Job,
  type JobProgress,
  type JobRequest,
} from "../lib/analysis";
import { useAnalysis } from "./analysis";

/** The job being followed. Everything in it arrived from the server: nothing advances by itself. */
export interface LiveRun {
  job: Job;
  /** Every field the worker has reported so far, the newest value of each. */
  progress: JobProgress | null;
  /** The frame of the newest report, which is what asks for a fresh preview image. */
  previewFrame: number | null;
  /** False while the browser is reconnecting to the event stream. */
  connected: boolean;
}

export type RecentJobs =
  | { status: "loading" }
  | { status: "ready"; jobs: readonly Job[] }
  | { status: "error"; message: string };

interface LiveContextValue {
  run: LiveRun | null;
  recent: RecentJobs;
  /** Throws the server's refusal, so the form can show it beside the fields that caused it. */
  start: (request: JobRequest) => Promise<void>;
  cancel: () => Promise<void>;
  /** Put a finished run away. */
  clear: () => void;
  refresh: () => void;
}

const LiveContext = createContext<LiveContextValue | null>(null);

function previewFrameOf(job: Job): number | null {
  return job.has_preview ? (job.progress?.frame ?? 0) : null;
}

/**
 * Lives above the routes, so a job keeps being followed, and its analysis is still adopted, whichever
 * page the viewer has moved to. A reload finds a job still running and follows it again.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const { adopt } = useAnalysis();
  const [run, setRun] = useState<LiveRun | null>(null);
  const [recent, setRecent] = useState<RecentJobs>({ status: "loading" });
  const stop = useRef<(() => void) | null>(null);

  const refresh = useCallback(async (): Promise<readonly Job[]> => {
    try {
      const jobs = await listJobs();
      setRecent({ status: "ready", jobs });
      return jobs;
    } catch (error) {
      setRecent({ status: "error", message: error instanceof Error ? error.message : "The job list couldn't be read" });
      return [];
    }
  }, []);

  const follow = useCallback(
    (job: Job) => {
      stop.current?.();
      const mine = (update: (current: LiveRun) => LiveRun) =>
        setRun((current) => (current?.job.id === job.id ? update(current) : current));
      setRun({ job, progress: job.progress, previewFrame: previewFrameOf(job), connected: true });
      stop.current = watchJob(job.id, {
        onStatus: (status) => mine((current) => ({ ...current, job: { ...current.job, status } })),
        onProgress: (line) =>
          mine((current) => ({
            ...current,
            progress: { ...current.progress, ...line },
            previewFrame: line.frame ?? current.previewFrame,
          })),
        onConnection: (connected) => mine((current) => ({ ...current, connected })),
        onEnd: (finished) => {
          mine((current) => ({ ...current, job: finished, connected: true }));
          void refresh();
          if (finished.status === "succeeded" && finished.analysis_id) adopt(finished.analysis_id);
        },
      });
    },
    [adopt, refresh],
  );

  useEffect(() => {
    let alive = true;
    void refresh().then((jobs) => {
      const active = jobs.find((job) => !isFinished(job.status));
      if (alive && active) follow(active);
    });
    return () => {
      alive = false;
      stop.current?.();
      stop.current = null;
    };
  }, [refresh, follow]);

  const value = useMemo<LiveContextValue>(
    () => ({
      run,
      recent,
      start: async (request) => {
        follow(await startJob(request));
        void refresh();
      },
      cancel: async () => {
        if (!run) return;
        const updated = await cancelJob(run.job.id);
        setRun((current) => (current?.job.id === updated.id ? { ...current, job: updated } : current));
      },
      clear: () => {
        stop.current?.();
        stop.current = null;
        setRun(null);
      },
      refresh: () => void refresh(),
    }),
    [run, recent, follow, refresh],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveContextValue {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLive must be used inside LiveProvider");
  return value;
}
