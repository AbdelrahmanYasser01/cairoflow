/**
 * Decoder for trajectories.bin, the columnar per-point file of an analysis.
 * The format is defined in packages/perception/cairoflow_perception/analysis_store.py.
 * One fetch, typed-array views, no parsing: a full 15-minute analysis is a few megabytes.
 */

export const NO_GROUND = -32768
export const NO_SPEED = 0xffff

export const POINT_INTERPOLATED = 1
export const POINT_SAM_REFINED = 2
export const POINT_OCCLUDED = 4
export const POINT_OUTSIDE_VALID_REGION = 8

const HEADER_BYTES = 32

export interface TrajectoryHeader {
  nPoints: number
  nTracks: number
  nFrames: number
  fps: number
  pxScale: number
  groundScale: number
  flags: number
}

export interface Trajectories {
  header: TrajectoryHeader
  x: Uint16Array
  y: Uint16Array
  w: Uint16Array
  h: Uint16Array
  gx: Int16Array
  gy: Int16Array
  speedCmS: Uint16Array
  heading: Uint8Array
  conf: Uint8Array
  pointFlags: Uint8Array
}

/** Track rows sit contiguously: row = firstRow + frame - startFrame. */
export interface TrackSpan {
  firstRow: number
  startFrame: number
  n: number
}

export function decodeTrajectories(buffer: ArrayBuffer): Trajectories {
  if (buffer.byteLength < HEADER_BYTES) {
    throw new Error('Trajectories file is shorter than its header')
  }
  const view = new DataView(buffer)
  const magic = String.fromCharCode(
    view.getUint8(0),
    view.getUint8(1),
    view.getUint8(2),
    view.getUint8(3),
  )
  if (magic !== 'CFT1') throw new Error(`Not a trajectories file: magic ${magic}`)
  const header: TrajectoryHeader = {
    nPoints: view.getUint32(4, true),
    nTracks: view.getUint32(8, true),
    nFrames: view.getUint32(12, true),
    fps: view.getFloat32(16, true),
    pxScale: view.getFloat32(20, true),
    groundScale: view.getFloat32(24, true),
    flags: view.getUint32(28, true),
  }
  const n = header.nPoints
  let offset = HEADER_BYTES
  const take = <T>(make: (o: number) => T, bytesPerItem: number): T => {
    const size = n * bytesPerItem
    if (offset + size > buffer.byteLength) throw new Error('Trajectories file is truncated')
    const column = make(offset)
    offset += size + ((4 - (size % 4)) % 4)
    return column
  }
  const u16 = () => take((o) => new Uint16Array(buffer, o, n), 2)
  const i16 = () => take((o) => new Int16Array(buffer, o, n), 2)
  const u8 = () => take((o) => new Uint8Array(buffer, o, n), 1)
  return {
    header,
    x: u16(),
    y: u16(),
    w: u16(),
    h: u16(),
    gx: i16(),
    gy: i16(),
    speedCmS: u16(),
    heading: u8(),
    conf: u8(),
    pointFlags: u8(),
  }
}

/** Matches analysis_store.trajectory_checksum, so both decoders can be compared. */
export function trajectoryChecksum(t: Trajectories): number {
  const columns = [t.x, t.y, t.w, t.h, t.gx, t.gy, t.speedCmS, t.heading, t.conf, t.pointFlags]
  const modulus = 2_147_483_647
  let total = 0
  for (const column of columns) {
    let sum = 0
    for (let i = 0; i < column.length; i++) sum += column[i]
    let strided = 0
    for (let i = 0; i < column.length; i += 97) strided += column[i]
    total = (((total + sum + strided * 3) % modulus) + modulus) % modulus
  }
  return total
}

/** Finds which tracks exist on a frame without scanning all of them. */
export class ActiveTracks {
  private readonly buckets: Int32Array[]
  private readonly bucketFrames = 64

  constructor(
    private readonly spans: readonly TrackSpan[],
    frameCount: number,
  ) {
    const count = Math.ceil(frameCount / this.bucketFrames) + 1
    const lists: number[][] = Array.from({ length: count }, () => [])
    spans.forEach((span, index) => {
      const first = Math.floor(span.startFrame / this.bucketFrames)
      const last = Math.floor((span.startFrame + span.n - 1) / this.bucketFrames)
      for (let b = first; b <= Math.min(last, count - 1); b++) lists[b].push(index)
    })
    this.buckets = lists.map((list) => Int32Array.from(list))
  }

  /** Calls back with the track index and its row for every track present on `frame`. */
  forEach(frame: number, visit: (trackIndex: number, row: number) => void): void {
    const bucket = this.buckets[Math.floor(frame / this.bucketFrames)]
    if (!bucket) return
    for (let i = 0; i < bucket.length; i++) {
      const span = this.spans[bucket[i]]
      const offset = frame - span.startFrame
      if (offset >= 0 && offset < span.n) visit(bucket[i], span.firstRow + offset)
    }
  }
}
