import { useCallback, useEffect, useState } from "react";
import { SAM_ANALYSIS } from "../domain/analyses";
import {
  getAnalysisRecord,
  getCorridor,
  getDecision,
  getDiagnosis,
  getHealth,
  getSamOutlines,
  listSources,
  probeArtifact,
  type AnalysisRecord,
  type ArtifactProbe,
  type CorridorSummary,
  type DecisionSummary,
  type DiagnosisRead,
  type FootageSource,
  type HealthReport,
  type SamOutlines,
} from "../lib/analysis";
import { loadSite, type SiteGeometry } from "../twin/site";

export type Resource<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; message: string };

/** One shared read per record: every page asking for it gets the same promise. */
function shared<T>(load: () => Promise<T>): { read: () => Promise<T>; forget: () => void } {
  let pending: Promise<T> | null = null;
  return {
    read: () => {
      if (!pending) {
        pending = load();
        pending.catch(() => {
          pending = null;
        });
      }
      return pending;
    },
    forget: () => {
      pending = null;
    },
  };
}

function useRecord<T>(source: { read: () => Promise<T>; forget: () => void }): Resource<T> & { retry: () => void } {
  const [state, setState] = useState<Resource<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    // A record on screen stays there while it is read again, so a refresh never blanks a page.
    setState((held) => (held.status === "ready" ? held : { status: "loading" }));
    source
      .read()
      .then((data) => {
        if (current) setState({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (current) {
          setState({ status: "error", message: error instanceof Error ? error.message : "This record couldn't be read" });
        }
      });
    return () => {
      current = false;
    };
  }, [source, attempt]);

  const retry = useCallback(() => {
    source.forget();
    setAttempt((count) => count + 1);
  }, [source]);

  return { ...state, retry };
}

const decision = shared<DecisionSummary>(getDecision);
const corridor = shared<CorridorSummary>(getCorridor);
const sources = shared<FootageSource[]>(listSources);
const health = shared<HealthReport>(getHealth);
const site = shared<SiteGeometry>(loadSite);
const samRecord = shared<AnalysisRecord>(() => getAnalysisRecord(SAM_ANALYSIS));

export const useDecision = () => useRecord(decision);
export const useCorridor = () => useRecord(corridor);
export const useSources = () => useRecord(sources);
export const useHealth = () => useRecord(health);
export const useSite = () => useRecord(site);
/** The refined run's record, for the Meta strip on any page, whatever is loaded. */
export const useSamRecord = () => useRecord(samRecord);

/** One read per key, for a record that belongs to one analysis. */
function useKeyed<T>(key: string | null, load: (key: string) => Promise<T>): Resource<T> & { retry: () => void } {
  const [state, setState] = useState<Resource<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (key === null) return;
    let current = true;
    setState({ status: "loading" });
    load(key)
      .then((data) => {
        if (current) setState({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (current) {
          setState({ status: "error", message: error instanceof Error ? error.message : "This record couldn't be read" });
        }
      });
    return () => {
      current = false;
    };
  }, [key, load, attempt]);

  const retry = useCallback(() => setAttempt((count) => count + 1), []);
  return { ...state, retry };
}

export const useDiagnosis = (analysisId: string | null) => useKeyed<DiagnosisRead>(analysisId, getDiagnosis);

/** Pass null for an analysis with no outlines: it is not asked for, and stays loading. */
export const useSamOutlines = (analysisId: string | null) => useKeyed<SamOutlines>(analysisId, getSamOutlines);

/** Whether an analysis's annotated video is on this machine. */
const probeAnnotated = (analysisId: string): Promise<ArtifactProbe> => probeArtifact(analysisId, "annotated_video");
export const useAnnotatedVideo = (analysisId: string | null) => useKeyed<ArtifactProbe>(analysisId, probeAnnotated);
