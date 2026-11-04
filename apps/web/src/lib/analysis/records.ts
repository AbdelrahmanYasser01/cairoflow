/**
 * The site-wide records beside an analysis: the decision, the corridor summary, the footage
 * catalogue and the server's own health. Only the fields a screen reads are typed. Each reader
 * checks the shape it depends on, so a changed record fails here, by name, and not inside a page.
 */
import { ApiError, getJson } from './api'
import type { EvidenceKind } from './contract'

export interface HealthReport {
  ok: boolean
  version: string
  vision_worker_available: boolean
  vision_worker_problem: string | null
}

export function getHealth(): Promise<HealthReport> {
  return getJson<HealthReport>('/health')
}

/** One row of GET /sources. */
export interface FootageSource {
  source_id: string
  kind: string
  title: string
  file_name: string
  duration_s: number | null
  width_px: number | null
  height_px: number | null
  fps: number | null
  calibrated: boolean
  calibration_note: string | null
  playable: boolean
  /** Where this file starts in the camera 2 source. Null: not known to be that footage. */
  source_start_s: number | null
  problem: string | null
  video_url: string | null
}

export async function listSources(): Promise<FootageSource[]> {
  const body = await getJson<{ sources?: unknown }>('/sources')
  if (!Array.isArray(body.sources)) throw new Error('The footage catalogue has no rows')
  return body.sources as FootageSource[]
}

/** One numbered intersection of the published corridor. */
export interface CorridorIntersection {
  int_id: number
  site_id: string | null
  name: string | null
  name_status: string
  unique_vehicles: number
  mean_moving_speed_m_s: number
  has_video_analysis: boolean
}

export interface CorridorSummary {
  evidence_kind: EvidenceKind
  evidence_note: string
  intersections: CorridorIntersection[]
}

export async function getCorridor(): Promise<CorridorSummary> {
  const body = await getJson<Partial<CorridorSummary>>('/intersections')
  if (!Array.isArray(body.intersections) || !body.evidence_kind) {
    throw new Error('The corridor summary has no intersections')
  }
  return body as CorridorSummary
}

export interface HypothesisEntry {
  evidence_kind: EvidenceKind
  hypothesis: { hypothesis_id: string; statement: string; confidence_label: string }
  grounding: { status: string; reasons: string[] }
}

export interface CandidateEntry {
  evidence_kind: EvidenceKind
  intervention_id: string
  proposal: { type: string; rationale: string; addresses_hypothesis_ids?: string[] }
  grounding: { eligible: boolean }
  validator: { status: string; reasons: string[]; cycle_s: number | null; baseline_cycle_s: number | null }
}

export interface SimulatedComparison {
  evidence_kind: EvidenceKind
  candidate: string
  metric_name: string
  unit: string
  n_seeds: number
  baseline_mean: number
  candidate_mean: number
  mean_difference: number
  ci95_difference: [number, number]
  verdict: 'improves' | 'worsens' | 'uninformative' | 'inconclusive'
}

/** One candidate on one signal timeline. */
export interface EnsembleMemberVerdict {
  member: string
  mean_delay_difference_s: number
  mean_delay_ci95_s: [number, number]
}

export interface EnsembleVerdict {
  candidate: string
  verdict: string
  reason: string
  robust_harm: string[]
  common_confirmed: string[]
  members: EnsembleMemberVerdict[]
}

export interface DecisionSummary {
  demo_id: string
  title: string
  outcome: {
    evidence_kind: EvidenceKind
    outcome: 'none_selected' | 'selected' | 'not_run'
    selected_intervention_id: string | null
    selection_reason: string
    recommended_intervention_id: string | null
    recommendation: string
  }
  baseline: {
    status: string
    summary: string
    findings: string[]
    assessed_on: string
    documented_in: string
    baseline_manifest_sha256: string
  }
  hypotheses: HypothesisEntry[]
  candidates: CandidateEntry[]
  simulation: {
    evidence_kind: EvidenceKind
    run_id: string
    created_at_utc: string
    seeds: number[]
    replay_window_s: [number, number]
  }
  metrics: SimulatedComparison[]
  ensemble: {
    evidence_kind: EvidenceKind
    members: { member: string; version: string; required: boolean; gate_passed: boolean }[]
    candidates: EnsembleVerdict[]
    selection_reason: string
    recommendation: string
  }
  assumptions: string[]
  limitations: string[]
  sources: { role: string; path: string; sha256: string }[]
}

/** Names the first thing a screen would need and this record lacks. */
export function parseDecision(raw: unknown): DecisionSummary {
  const record = raw as Partial<DecisionSummary> | null
  if (!record?.outcome?.outcome) throw new Error('The decision record has no outcome')
  if (!record.baseline?.status) throw new Error('The decision record has no baseline assessment')
  if (!record.simulation?.run_id) throw new Error('The decision record names no simulation run')
  if (!record.ensemble || !Array.isArray(record.ensemble.members)) {
    throw new Error('The decision record has no ensemble')
  }
  for (const key of ['hypotheses', 'candidates', 'metrics', 'assumptions', 'limitations', 'sources'] as const) {
    if (!Array.isArray(record[key])) throw new Error(`The decision record has no ${key}`)
  }
  return record as DecisionSummary
}

export function getDecision(): Promise<DecisionSummary> {
  return getJson<unknown>('/decision').then(parseDecision)
}

/** Where the decision record can be downloaded as it stands, for an export link. */
export const DECISION_URL = '/api/v1/decision'

/** One SAM mask, simplified to a polygon in image pixels with (0, 0) the top-left corner. */
export interface SamOutline {
  track_id: number
  frame: number
  polygon: readonly (readonly [number, number])[]
}

/** The outlines file a refinement writes. It holds sampled frames only, every `every_frames`. */
export interface SamOutlines {
  format: string
  analysis_id: string
  label: string
  every_frames: number
  entries: SamOutline[]
}

const OUTLINES_FILE = 'sam_outlines.json'

/** The outlines ride as the report artifact, the only role beside the tracks that serves a file. */
export function hasSamOutlines(record: { artifacts: readonly { role: string; path: string }[] }): boolean {
  return record.artifacts.some((artifact) => artifact.role === 'report' && artifact.path === OUTLINES_FILE)
}

/** Names the first thing the canvas could not draw. */
export function parseSamOutlines(raw: unknown): SamOutlines {
  const file = raw as Partial<SamOutlines> | null
  if (file?.format !== 'sam_outlines/1') throw new Error('The report is not a SAM outlines file')
  if (typeof file.every_frames !== 'number' || file.every_frames < 1) {
    throw new Error('The outlines file names no sampling interval')
  }
  if (!Array.isArray(file.entries)) throw new Error('The outlines file has no entries')
  return file as SamOutlines
}

export function getSamOutlines(analysisId: string): Promise<SamOutlines> {
  return getJson<unknown>(`/analyses/${encodeURIComponent(analysisId)}/artifacts/report`).then(parseSamOutlines)
}

/** One fact the model was given, copied from the analysis record by code. */
export interface CvEvidenceItem {
  evidence_id: string
  statement: string
  value: number | null
  unit: string | null
  evidence_kind: EvidenceKind
  status: string
}

export interface CvHypothesis {
  hypothesis_id: string
  statement: string
  supporting_evidence_ids: string[]
  contradicting_evidence_ids: string[]
  confidence: 'low' | 'medium' | 'high'
  how_to_check: string
}

/** One reply, with what code found wrong with it. */
export interface CvReplicate {
  replicate: number
  status: 'ok' | 'failed'
  failure: string | null
  from_cache: boolean
  cost_usd: number | null
  citation_problems: string[]
  unsupported_numbers: string[]
  diagnosis: { summary: string; hypotheses: CvHypothesis[]; limitations: string[] } | null
}

/** packages/contracts/cairoflow_contracts/cv_diagnosis.py. All but `evidence` is model text. */
export interface CvDiagnosisRecord {
  label: string
  analysis_id: string
  model: string
  prompt_version: string
  created_at_utc: string
  evidence: CvEvidenceItem[]
  replicates: CvReplicate[]
}

export type DiagnosisRead =
  | { present: true; record: CvDiagnosisRecord }
  | { present: false; rebuildHint: string | null }

/** `present: false` when the server holds no diagnosis of this analysis. Any other failure throws. */
export async function getDiagnosis(analysisId: string): Promise<DiagnosisRead> {
  try {
    const record = await getJson<Partial<CvDiagnosisRecord>>(
      `/analyses/${encodeURIComponent(analysisId)}/diagnosis`,
    )
    if (!Array.isArray(record.replicates) || !Array.isArray(record.evidence)) {
      throw new Error('The diagnosis record has no replies')
    }
    return { present: true, record: record as CvDiagnosisRecord }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      const hint = (error.body as { rebuild_hint?: unknown } | undefined)?.rebuild_hint
      return { present: false, rebuildHint: typeof hint === 'string' ? hint : null }
    }
    throw error
  }
}
