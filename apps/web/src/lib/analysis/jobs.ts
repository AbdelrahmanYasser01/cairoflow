/**
 * Live analysis over /api/v1: uploads, jobs and their event stream. Every number that reaches a
 * screen from here is a line a worker wrote. A field the worker did not report stays undefined, and
 * a job that has not reported has no progress at all, so nothing here estimates or interpolates.
 */
import { ApiError, errorText, getJson } from './api'
import type { FootageSource } from './records'

const BASE = '/api/v1'
const ABORTED = -1

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'

/** One worker line, as jobs.py documents it. */
export interface JobProgress {
  stage: string
  frame?: number
  frames_total?: number
  fps?: number
  detections?: number
  tracks?: number
  elapsed_s?: number
  message?: string
  analysis_id?: string
  error?: string
}

export interface Job {
  id: string
  source_id: string
  start_s: number
  duration_s: number
  frame_stride: number
  status: JobStatus
  cancelling: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  analysis_id: string | null
  error: string | null
  progress: JobProgress | null
  has_preview: boolean
}

export interface JobRequest {
  source_id: string
  start_s: number
  duration_s: number
  frame_stride: number
}

export function isFinished(status: JobStatus): boolean {
  return status !== 'queued' && status !== 'running'
}

export async function listJobs(): Promise<Job[]> {
  const body = await getJson<{ jobs?: unknown }>('/jobs')
  if (!Array.isArray(body.jobs)) throw new ApiError('The job list has no rows', 200, body)
  return body.jobs as Job[]
}

export function startJob(request: JobRequest): Promise<Job> {
  return getJson<Job>('/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
}

export function cancelJob(jobId: string): Promise<Job> {
  return getJson<Job>(`/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

/** The latest annotated frame. The frame number only makes each reported frame a new URL. */
export function jobPreviewUrl(jobId: string, frame: number): string {
  return `${BASE}/jobs/${encodeURIComponent(jobId)}/preview.jpg?frame=${frame}`
}

export interface JobWatcher {
  onStatus: (status: JobStatus) => void
  onProgress: (line: JobProgress) => void
  onEnd: (job: Job) => void
  /** The stream dropped and the browser is reconnecting, or it came back. */
  onConnection: (live: boolean) => void
}

/** Follows a job's Server-Sent Events until it ends. Returns the function that stops following. */
export function watchJob(jobId: string, watcher: JobWatcher): () => void {
  const stream = new EventSource(`${BASE}/jobs/${encodeURIComponent(jobId)}/events`)
  stream.addEventListener('status', (event) => {
    watcher.onStatus((JSON.parse(event.data) as { status: JobStatus }).status)
  })
  stream.addEventListener('progress', (event) => {
    watcher.onProgress(JSON.parse(event.data) as JobProgress)
  })
  stream.addEventListener('end', (event) => {
    stream.close()
    watcher.onEnd(JSON.parse(event.data) as Job)
  })
  stream.onopen = () => watcher.onConnection(true)
  stream.onerror = () => watcher.onConnection(false)
  return () => stream.close()
}

export interface UploadHandlers {
  /** Bytes the browser has handed to the network, from the browser's own upload events. */
  onProgress: (sentBytes: number, totalBytes: number) => void
  /** Every byte has left; the server is now checking the file. */
  onSent: () => void
}

export interface Upload {
  done: Promise<FootageSource>
  abort: () => void
}

/** XHR rather than fetch: only XHR reports upload progress. */
export function uploadVideo(file: File, handlers: UploadHandlers): Upload {
  const xhr = new XMLHttpRequest()
  const done = new Promise<FootageSource>((resolve, reject) => {
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) handlers.onProgress(event.loaded, event.total)
    }
    xhr.upload.onload = () => handlers.onSent()
    xhr.onload = () => {
      let body: unknown
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = undefined
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as FootageSource)
        return
      }
      const detail = (body as { detail?: unknown } | undefined)?.detail
      reject(new ApiError(errorText(detail, xhr.status), xhr.status, detail, body))
    }
    xhr.onerror = () => reject(new ApiError('The upload could not reach the server', 0))
    xhr.onabort = () => reject(new ApiError('Upload cancelled', ABORTED))
  })
  xhr.open('PUT', `${BASE}/uploads/${encodeURIComponent(file.name)}`)
  xhr.send(file)
  return { done, abort: () => xhr.abort() }
}

export function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.status === ABORTED
}
