/**
 * The site's road geometry, from GET /api/v1/site. Frame: local_enu_junction_m, metres from the
 * junction centre, +v along Lankershim northbound. In the scene x = u, z = -v, y is up.
 */
import { getJson, type CalibrationState } from "../lib/analysis";

export type Point2 = readonly [number, number];
export type Arm = "N" | "E" | "S" | "W";

export interface SiteLane {
  lane_id: string;
  arm: Arm;
  direction: "approach" | "exit";
  polygon_m: readonly Point2[];
  allowed_turns: readonly string[];
  provenance: string;
  source: string;
}

export interface SiteGate {
  arm: Arm;
  role: string;
  segment_m: readonly [Point2, Point2];
  provenance: string;
  source: string;
}

export interface SiteGeometry {
  site_id: string;
  site_name: string | null;
  site_name_status: string | null;
  ground_frame: string;
  drives_on: string;
  lanes: readonly SiteLane[];
  gates: readonly SiteGate[];
  calibration: CalibrationState | null;
}

function isPoint(value: unknown): value is Point2 {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === "number" &&
    typeof value[1] === "number"
  );
}

/** Rejects a response the scene could not draw, naming the first thing wrong with it. */
export function parseSite(raw: unknown): SiteGeometry {
  const site = raw as Partial<SiteGeometry> | null;
  if (!site || !Array.isArray(site.lanes) || !Array.isArray(site.gates)) {
    throw new Error("Site geometry has no lanes or gates");
  }
  for (const lane of site.lanes) {
    if (!Array.isArray(lane.polygon_m) || lane.polygon_m.length < 3 || !lane.polygon_m.every(isPoint)) {
      throw new Error(`Lane ${lane.lane_id} has no usable polygon`);
    }
  }
  for (const gate of site.gates) {
    if (!Array.isArray(gate.segment_m) || gate.segment_m.length !== 2 || !gate.segment_m.every(isPoint)) {
      throw new Error(`A ${gate.role} gate on arm ${gate.arm} has no usable segment`);
    }
  }
  return {
    site_id: site.site_id ?? "",
    site_name: site.site_name ?? null,
    site_name_status: site.site_name_status ?? null,
    ground_frame: site.ground_frame ?? "",
    drives_on: site.drives_on ?? "",
    lanes: site.lanes,
    gates: site.gates,
    calibration: site.calibration ?? null,
  };
}

let loading: Promise<SiteGeometry> | null = null;

export function loadSite(): Promise<SiteGeometry> {
  if (!loading) {
    loading = getJson<unknown>("/site").then(parseSite);
    loading.catch(() => {
      loading = null;
    });
  }
  return loading;
}

export interface TwinAnalysisRow {
  id: string;
  title: string;
  valid: boolean;
}

/** The server answers {analyses: [{id}]}; the shared type still says [{analysis_id}]. Takes both. */
export function parseAnalysisRows(raw: unknown): TwinAnalysisRow[] {
  const list = Array.isArray(raw) ? raw : ((raw as { analyses?: unknown } | null)?.analyses ?? []);
  if (!Array.isArray(list)) return [];
  return list.flatMap((row: { id?: string; analysis_id?: string; title?: string; valid?: boolean }) => {
    const id = row.id ?? row.analysis_id;
    return id ? [{ id, title: row.title ?? id, valid: row.valid !== false }] : [];
  });
}

export function listTwinAnalyses(): Promise<TwinAnalysisRow[]> {
  return getJson<unknown>("/analyses").then(parseAnalysisRows);
}
