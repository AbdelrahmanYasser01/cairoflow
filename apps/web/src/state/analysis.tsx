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
import { DEFAULT_ANALYSIS, analysisName, offeredAnalyses } from "../domain/analyses";
import { openingFrame } from "../domain/footage";
import { clock, listAnalyses, loadAnalysis, type AnalysisSummary, type LoadedAnalysis } from "../lib/analysis";

export interface ActiveAnalysis {
  id: string;
  label: string;
}

export type AnalysisState =
  | { status: "none"; reason: string }
  | { status: "loading"; label?: string }
  | { status: "ready"; analysis: ActiveAnalysis; loaded: LoadedAnalysis }
  | { status: "error"; message: string; lastGood?: string };

export type AnalysisCatalog =
  | { status: "loading" }
  | { status: "ready"; analyses: readonly AnalysisSummary[] }
  | { status: "error"; message: string };

interface AnalysisContextValue {
  state: AnalysisState;
  catalog: AnalysisCatalog;
  /** Load one analysis. The whole app follows it: strip, pages and twin read this one state. */
  select: (id: string) => void;
  /** Read the list again, then load an analysis a job has just written. */
  adopt: (id: string) => void;
  /** Ask the server again, after a failure. */
  retry: () => void;
}

const AnalysisContext = createContext<AnalysisContextValue | null>(null);

const REMEMBERED = "cairoflow.analysis";
const NOTHING_LISTED = "The server lists no valid analysis.";
export { DEFAULT_ANALYSIS };

function rank(row: AnalysisSummary): number {
  if (row.id === DEFAULT_ANALYSIS) return 0;
  if (row.data_origin !== "camera_cv") return 3;
  return row.mode === "live" ? 2 : 1;
}

/** The shipped study first, other camera runs next, then live runs, reference data last. The sort is stable. */
export function orderAnalyses(rows: readonly AnalysisSummary[]): AnalysisSummary[] {
  return [...rows].sort((a, b) => rank(a) - rank(b));
}

/**
 * Dev-only preview of the data states, for reviewing layouts: /vision?state=none, ?state=loading
 * or ?state=error. A production build always reads what the server has.
 */
function previewState(): AnalysisState | null {
  if (!import.meta.env.DEV) return null;
  const preview = new URLSearchParams(window.location.search).get("state");
  if (preview === "none") return { status: "none", reason: NOTHING_LISTED };
  if (preview === "loading") return { status: "loading" };
  if (preview === "error") return { status: "error", message: "Couldn't load this analysis." };
  return null;
}

function failure(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [preview] = useState(previewState);
  const [state, setState] = useState<AnalysisState>(preview ?? { status: "loading" });
  const [catalog, setCatalog] = useState<AnalysisCatalog>({ status: "loading" });
  const latest = useRef(0);
  const lastGood = useRef<string | undefined>(undefined);

  const select = useCallback((id: string, label?: string) => {
    latest.current += 1;
    const request = latest.current;
    setState({ status: "loading", ...(label ? { label } : {}) });
    loadAnalysis(id)
      .then((loaded) => {
        if (request !== latest.current) return;
        const title = analysisName({ id, title: loaded.record.title, mode: loaded.record.mode });
        lastGood.current = title;
        sessionStorage.setItem(REMEMBERED, id);
        // Every page reads this clock, so the run opens on a busy moment wherever it is opened.
        clock.configure(loaded.record.fps, loaded.record.frame_count);
        clock.seek(openingFrame(loaded));
        setState({ status: "ready", analysis: { id, label: title }, loaded });
      })
      .catch((error: unknown) => {
        if (request !== latest.current) return;
        setState({
          status: "error",
          message: failure(error, "This analysis couldn't be loaded"),
          ...(lastGood.current ? { lastGood: lastGood.current } : {}),
        });
      });
  }, []);

  const boot = useCallback(
    (hold: boolean) => {
      latest.current += 1;
      const request = latest.current;
      setCatalog({ status: "loading" });
      if (!hold) setState({ status: "loading" });
      listAnalyses()
        .then((listed) => {
          if (request !== latest.current) return;
          const rows = orderAnalyses(offeredAnalyses(listed));
          setCatalog({ status: "ready", analyses: rows });
          if (hold) return;
          const valid = rows.filter((row) => row.valid);
          const remembered = sessionStorage.getItem(REMEMBERED);
          const pick = valid.find((row) => row.id === remembered) ?? valid[0];
          if (pick) select(pick.id, analysisName(pick));
          else setState({ status: "none", reason: NOTHING_LISTED });
        })
        .catch((error: unknown) => {
          if (request !== latest.current) return;
          const message = failure(error, "The analysis list couldn't be read");
          setCatalog({ status: "error", message });
          if (!hold) setState({ status: "error", message });
        });
    },
    [select],
  );

  useEffect(() => boot(preview !== null), [boot, preview]);

  const adopt = useCallback(
    (id: string) => {
      select(id);
      listAnalyses()
        .then((rows) => setCatalog({ status: "ready", analyses: orderAnalyses(offeredAnalyses(rows)) }))
        .catch((error: unknown) => {
          setCatalog({ status: "error", message: failure(error, "The analysis list couldn't be read") });
        });
    },
    [select],
  );

  const value = useMemo<AnalysisContextValue>(
    () => ({ state, catalog, select, adopt, retry: () => boot(false) }),
    [state, catalog, select, adopt, boot],
  );
  return <AnalysisContext.Provider value={value}>{children}</AnalysisContext.Provider>;
}

export function useAnalysis(): AnalysisContextValue {
  const value = useContext(AnalysisContext);
  if (!value) throw new Error("useAnalysis must be used inside AnalysisProvider");
  return value;
}

export function analysisLabel(state: AnalysisState): string {
  switch (state.status) {
    case "ready":
      return state.analysis.label;
    case "loading":
      return state.label ? `Loading ${state.label}` : "Loading analysis";
    case "error":
      return "Analysis failed to load";
    default:
      return "No analysis loaded";
  }
}
