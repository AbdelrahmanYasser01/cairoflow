/**
 * Occupancy from the real rows. Each row is one vehicle present for 1/fps seconds, so a cell holds
 * vehicle-seconds. Computed once per filter change, never per frame.
 */
import { NO_GROUND } from "../lib/analysis";
import type { Bounds, TwinModel } from "./model";

/** Geo mode draws the fullest cell this tall, and every other in proportion. */
export const COLUMN_MAX_HEIGHT_M = 16;
/** Columns under this share of the peak are left out, or they carpet the site in near-zero bricks. */
export const COLUMN_MIN_LEVEL = 0.03;

export interface DensityGrid {
  cells: Float32Array;
  cols: number;
  rows: number;
  cellM: number;
  uMin: number;
  vMin: number;
  /** The fullest cell, in vehicle-seconds. */
  peak: number;
  total: number;
}

type DensitySource = Pick<TwinModel, "tracks" | "trajectories" | "groundScale" | "fps">;

export function accumulateDensity(
  source: DensitySource,
  mask: Uint8Array,
  bounds: Bounds,
  frames: readonly [number, number],
  cellM: number,
): DensityGrid {
  const uMin = Math.floor(bounds.uMin / cellM) * cellM;
  const vMin = Math.floor(bounds.vMin / cellM) * cellM;
  const cols = Math.ceil((bounds.uMax - uMin) / cellM) + 1;
  const rows = Math.ceil((bounds.vMax - vMin) / cellM) + 1;
  const cells = new Float32Array(cols * rows);
  const { gx, gy } = source.trajectories;
  const scale = source.groundScale;
  const perRow = 1 / source.fps;
  source.tracks.forEach((track, index) => {
    if (!mask[index]) return;
    const first = Math.max(0, Math.ceil(frames[0]) - track.start_frame);
    const last = Math.min(track.n - 1, Math.floor(frames[1]) - track.start_frame);
    for (let i = first; i <= last; i++) {
      const row = track.first_row + i;
      if (gx[row] === NO_GROUND) continue;
      const c = Math.floor((gx[row] * scale - uMin) / cellM);
      const r = Math.floor((gy[row] * scale - vMin) / cellM);
      if (c >= 0 && c < cols && r >= 0 && r < rows) cells[r * cols + c] += perRow;
    }
  });
  let peak = 0;
  let total = 0;
  for (let i = 0; i < cells.length; i++) {
    total += cells[i];
    if (cells[i] > peak) peak = cells[i];
  }
  return { cells, cols, rows, cellM, uMin, vMin, peak, total };
}

/** Separable Gaussian, for the heat layer's look only. The legend reports the unblurred peak. */
export function blurCells(grid: DensityGrid, sigmaCells: number): Float32Array {
  const reach = Math.max(1, Math.ceil(sigmaCells * 2.5));
  const kernel = new Float32Array(reach * 2 + 1);
  let sum = 0;
  for (let i = -reach; i <= reach; i++) {
    const weight = Math.exp(-(i * i) / (2 * sigmaCells * sigmaCells));
    kernel[i + reach] = weight;
    sum += weight;
  }
  for (let i = 0; i < kernel.length; i++) kernel[i] /= sum;
  const { cols, rows, cells } = grid;
  const across = new Float32Array(cells.length);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let total = 0;
      for (let k = -reach; k <= reach; k++) {
        const cc = c + k;
        if (cc >= 0 && cc < cols) total += cells[r * cols + cc] * kernel[k + reach];
      }
      across[r * cols + c] = total;
    }
  }
  const out = new Float32Array(cells.length);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let total = 0;
      for (let k = -reach; k <= reach; k++) {
        const rr = r + k;
        if (rr >= 0 && rr < rows) total += across[rr * cols + c] * kernel[k + reach];
      }
      out[r * cols + c] = total;
    }
  }
  return out;
}

/** 0..255 on a square-root scale, so a queue head doesn't flatten everything else to nothing. */
export function toHeatBytes(values: Float32Array): Uint8Array {
  let peak = 0;
  for (let i = 0; i < values.length; i++) if (values[i] > peak) peak = values[i];
  const bytes = new Uint8Array(values.length);
  if (peak <= 0) return bytes;
  for (let i = 0; i < values.length; i++) bytes[i] = Math.round(Math.sqrt(values[i] / peak) * 255);
  return bytes;
}
