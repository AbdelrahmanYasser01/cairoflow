import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ActiveTracks, decodeTrajectories, trajectoryChecksum, type TrackSpan } from './traj'

const SAMPLES = resolve(__dirname, '../../../../../data/sample')
const ANALYSIS = 'ref-ngsim-int2-960-1020'

function load(): ArrayBuffer {
  const bytes = readFileSync(resolve(SAMPLES, 'analyses', ANALYSIS, 'trajectories.bin'))
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

describe('trajectories decoder', () => {
  it('reads the header of a real analysis', () => {
    const t = decodeTrajectories(load())
    expect(t.header.nFrames).toBe(600)
    expect(t.header.fps).toBeCloseTo(10)
    expect(t.x.length).toBe(t.header.nPoints)
  })

  it('matches the checksum written by the Python decoder', () => {
    const checksums = JSON.parse(readFileSync(resolve(SAMPLES, 'checksums.json'), 'utf8'))
    expect(trajectoryChecksum(decodeTrajectories(load()))).toBe(checksums[ANALYSIS])
  })

  it('refuses a file that is not trajectories', () => {
    const bytes = new Uint8Array(load().slice(0))
    bytes[0] = 88
    expect(() => decodeTrajectories(bytes.buffer as ArrayBuffer)).toThrow(/magic/)
  })

  it('refuses a truncated file', () => {
    expect(() => decodeTrajectories(load().slice(0, 64))).toThrow(/truncated/)
  })
})

describe('ActiveTracks', () => {
  const spans: TrackSpan[] = [
    { firstRow: 0, startFrame: 0, n: 10 },
    { firstRow: 10, startFrame: 5, n: 100 },
    { firstRow: 110, startFrame: 200, n: 3 },
  ]
  const at = (frame: number) => {
    const found: Array<[number, number]> = []
    new ActiveTracks(spans, 300).forEach(frame, (track, row) => found.push([track, row]))
    return found
  }

  it('finds every track on a frame, with its row', () => {
    expect(at(7)).toEqual([
      [0, 7],
      [1, 12],
    ])
  })

  it('crosses bucket boundaries', () => {
    expect(at(70)).toEqual([[1, 75]])
    expect(at(201)).toEqual([[2, 111]])
  })

  it('finds nothing between tracks', () => {
    expect(at(150)).toEqual([])
  })
})
