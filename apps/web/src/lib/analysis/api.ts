/**
 * Typed access to /api/v1. Errors carry the server's message so a screen can show why, not just
 * that something failed.
 */
import type { AnalysisRecord, TrackEntry, TrackIndex } from './contract'
import { ActiveTracks, decodeTrajectories, type Trajectories, type TrackSpan } from './traj'

const BASE = '/api/v1'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
    /** The whole error body: the server may add fields beside `detail`, such as a rebuild hint. */
    readonly body?: unknown,
  ) {
    super(message)
  }
}

/** The server's own words: a string, or the list of field errors FastAPI gives for a bad body. */
export function errorText(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.flatMap((item) => {
      const message = (item as { msg?: unknown } | null)?.msg
      return typeof message === 'string' ? [message] : []
    })
    if (parts.length > 0) return parts.join('; ')
  }
  return `Request failed (${status})`
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError('The CairoFlow server is not reachable', 0)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => undefined)
    const detail = (body as { detail?: unknown } | undefined)?.detail ?? body
    throw new ApiError(errorText(detail, response.status), response.status, detail, body)
  }
  return response
}

export async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json() as Promise<T>
}

/** One row of GET /analyses. */
export interface AnalysisSummary {
  id: string
  title: string
  mode: 'precomputed' | 'live'
  data_origin: 'camera_cv' | 'reference_dataset'
  site_id: string
  frame_count: number
  n_tracks: number
  created_at: string
  valid: boolean
  invalid_reason?: string | null
}

/** Everything one screen needs from an analysis, loaded once and shared. */
export interface LoadedAnalysis {
  record: AnalysisRecord
  tracks: TrackEntry[]
  trajectories: Trajectories
  active: ActiveTracks
}

export async function listAnalyses(): Promise<AnalysisSummary[]> {
  const body = await getJson<{ analyses?: unknown }>('/analyses')
  if (!Array.isArray(body.analyses)) throw new ApiError('The analysis list has no rows', 200, body)
  return body.analyses as AnalysisSummary[]
}

const cache = new Map<string, Promise<LoadedAnalysis>>()

export function loadAnalysis(analysisId: string): Promise<LoadedAnalysis> {
  const existing = cache.get(analysisId)
  if (existing) return existing
  const loading = fetchAnalysis(analysisId)
  loading.catch(() => cache.delete(analysisId))
  cache.set(analysisId, loading)
  return loading
}

async function fetchAnalysis(analysisId: string): Promise<LoadedAnalysis> {
  const id = encodeURIComponent(analysisId)
  const [record, index, binary] = await Promise.all([
    getJson<AnalysisRecord>(`/analyses/${id}`),
    getJson<TrackIndex>(`/analyses/${id}/tracks`),
    request(`/analyses/${id}/trajectories`).then((r) => r.arrayBuffer()),
  ])
  const trajectories = decodeTrajectories(binary)
  const spans: TrackSpan[] = index.tracks.map((t) => ({
    firstRow: t.first_row,
    startFrame: t.start_frame,
    n: t.n,
  }))
  return {
    record,
    tracks: index.tracks,
    trajectories,
    active: new ActiveTracks(spans, record.frame_count),
  }
}

/** One analysis's record alone, for a figure from a run that isn't the one on screen. */
export function getAnalysisRecord(analysisId: string): Promise<AnalysisRecord> {
  return getJson<AnalysisRecord>(`/analyses/${encodeURIComponent(analysisId)}`)
}

/** URL of an artifact, for a <video> source. A 404 means it isn't on this machine. */
export function artifactUrl(analysisId: string, role: string): string {
  return `${BASE}/analyses/${encodeURIComponent(analysisId)}/artifacts/${role}`
}

/** Whether an artifact is on this machine, and if not, what the server says to run. */
export type ArtifactProbe =
  | { available: true; url: string }
  | { available: false; detail: string; rebuildHint: string | null; licenceNote: string | null }

function textOf(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/**
 * Asks for one byte, so a video is not sent to find out it exists, and a 404 still carries its
 * rebuild hint, which a HEAD would not.
 */
export async function probeArtifact(analysisId: string, role: string): Promise<ArtifactProbe> {
  const url = artifactUrl(analysisId, role)
  try {
    await request(url.slice(BASE.length), { headers: { Range: 'bytes=0-0' } })
    return { available: true, url }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      const body = error.body as { rebuild_hint?: unknown; licence_note?: unknown } | undefined
      return {
        available: false,
        detail: error.message,
        rebuildHint: textOf(body?.rebuild_hint),
        licenceNote: textOf(body?.licence_note),
      }
    }
    throw error
  }
}
