import { useEffect, useState } from "react";
import { openingFrame } from "../domain/footage";
import { clock, loadAnalysis } from "../lib/analysis";
import { useAnalysis } from "../state/analysis";
import { resetFilters } from "./controls";
import { buildModel, type TwinModel } from "./model";
import { listTwinAnalyses, loadSite } from "./site";
import { createStore } from "./store";

const cache = new Map<string, Promise<TwinModel>>();

export function loadTwinModel(analysisId: string): Promise<TwinModel> {
  const existing = cache.get(analysisId);
  if (existing) return existing;
  const loading = Promise.all([loadAnalysis(analysisId), loadSite()]).then(([loaded, site]) =>
    buildModel(analysisId, loaded, site),
  );
  loading.catch(() => cache.delete(analysisId));
  cache.set(analysisId, loading);
  return loading;
}

export type TwinModelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; model: TwinModel };

/** The analysis on screen, so panels mounted anywhere can read the same model as the scene. */
export const activeModel = createStore<TwinModel | null>(null);

export function useTwinModel(analysisId: string | null, attempt = 0): TwinModelState {
  const [state, setState] = useState<TwinModelState>({ status: "loading" });

  useEffect(() => {
    if (!analysisId) return;
    let current = true;
    setState({ status: "loading" });
    loadTwinModel(analysisId)
      .then((model) => {
        if (!current) return;
        const previous = activeModel.get();
        clock.configure(model.fps, model.frameCount);
        // A time window or a hidden value from another analysis means nothing here, and neither
        // does its playhead: clamped to this analysis it would land on the last frame.
        if (previous?.id !== model.id) resetFilters();
        if (previous && previous.id !== model.id) clock.seek(openingFrame(model));
        activeModel.set(model);
        setState({ status: "ready", model });
      })
      .catch((error: unknown) => {
        if (!current) return;
        setState({
          status: "error",
          message: error instanceof Error ? error.message : "This analysis couldn't be loaded",
        });
      });
    return () => {
      current = false;
    };
  }, [analysisId, attempt]);

  return state;
}

export type ResolvedAnalysis =
  | { status: "resolving" }
  | { status: "none" }
  | { status: "error"; message: string }
  | { status: "resolved"; id: string };

/**
 * Which analysis to show: the one a page names, else the one the app has loaded, else the first
 * valid one the server lists. The overlay always names what is on screen, so the fallback can't
 * pass for a choice the operator made.
 */
export function useResolvedAnalysis(explicit: string | undefined, attempt = 0): ResolvedAnalysis {
  const { state } = useAnalysis();
  const named = explicit ?? (state.status === "ready" ? state.analysis.id : undefined);
  const [listed, setListed] = useState<ResolvedAnalysis>({ status: "resolving" });

  useEffect(() => {
    if (named) return;
    let current = true;
    setListed({ status: "resolving" });
    listTwinAnalyses()
      .then((rows) => {
        if (!current) return;
        const first = rows.find((row) => row.valid);
        setListed(first ? { status: "resolved", id: first.id } : { status: "none" });
      })
      .catch((error: unknown) => {
        if (!current) return;
        setListed({
          status: "error",
          message: error instanceof Error ? error.message : "The analysis list couldn't be read",
        });
      });
    return () => {
      current = false;
    };
  }, [named, attempt]);

  return named ? { status: "resolved", id: named } : listed;
}
