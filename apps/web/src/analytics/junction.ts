/**
 * A schematic junction for the turning-movement map. Positions are drawn, not measured: the plan is
 * a diagram of four arms, so ribbons are labelled as assumed paths. Width is the only measured
 * encoding, and it is the record's own count. Right-hand traffic, north up.
 */
import type { MovementCell } from "../lib/analysis";

export type Arm = "N" | "E" | "S" | "W";
type Vec = readonly [number, number];

export const VIEW = { width: 1000, height: 600 } as const;
const CENTRE: Vec = [VIEW.width / 2, VIEW.height / 2];
/** Half the width of the north-south road and of the east-west road, in view units. */
export const HALF_NS = 96;
export const HALF_EW = 60;
/** From the centre to each arm's stop line. */
const STOP: Record<Arm, number> = { N: HALF_EW + 30, S: HALF_EW + 30, E: HALF_NS + 30, W: HALF_NS + 30 };
const FAR: Record<Arm, number> = { N: VIEW.height / 2, S: VIEW.height / 2, E: VIEW.width / 2, W: VIEW.width / 2 };
const OUT: Record<Arm, Vec> = { N: [0, -1], E: [1, 0], S: [0, 1], W: [-1, 0] };

/** Widest ribbon, in view units, and the narrowest one still drawn. */
const MAX_W = 30;
const MIN_W = 1.5;
const INNER_GAP = 7;
const GAP = 6;

/** The driver's right of a direction, on a screen whose y runs down. */
const right = ([dx, dy]: Vec): Vec => [-dy, dx];
const add = (a: Vec, b: Vec, k = 1): Vec => [a[0] + b[0] * k, a[1] + b[1] * k];

function at(arm: Arm, distance: number, lateral: number, side: Vec): Vec {
  return add(add(CENTRE, OUT[arm], distance), side, lateral);
}

/** Approach lanes sit on the driver's right coming in; exit lanes on the driver's right going out. */
const approachSide = (arm: Arm): Vec => right([-OUT[arm][0], -OUT[arm][1]]);
const exitSide = (arm: Arm): Vec => right(OUT[arm]);

export interface Ribbon {
  key: string;
  cell: MovementCell;
  d: string;
  /** Width for the camera (or dataset) count and for the published reference, when there is one. */
  width: number;
  refWidth: number | null;
}

export const movementKey = (cell: Pick<MovementCell, "approach" | "exit_arm">): string =>
  `${cell.approach}-${cell.exit_arm}`;

const TURN_ORDER = { left: 0, u_turn: 0, through: 1, right: 2 } as const;

/** Packs lanes outward from the centre line: left turns inside, through in the middle, rights at the kerb. */
function pack(cells: readonly MovementCell[], widthOf: (cell: MovementCell) => number): Map<string, number> {
  const placed = new Map<string, number>();
  let edge = INNER_GAP;
  const sorted = [...cells].sort((a, b) => TURN_ORDER[a.turn] - TURN_ORDER[b.turn]);
  for (const cell of sorted) {
    const w = widthOf(cell);
    placed.set(movementKey(cell), edge + w / 2);
    edge += w + GAP;
  }
  return placed;
}

export function ribbons(movements: readonly MovementCell[]): Ribbon[] {
  const drawn = movements.filter((cell) => cell.count != null && cell.approach !== cell.exit_arm);
  const largest = Math.max(1, ...drawn.map((cell) => Math.max(cell.count ?? 0, cell.reference_count ?? 0)));
  const scale = (value: number) => Math.max(MIN_W, (value / largest) * MAX_W);
  const span = (cell: MovementCell) => Math.max(scale(cell.count ?? 0), scale(cell.reference_count ?? 0));

  const arms: Arm[] = ["N", "E", "S", "W"];
  const inLane = new Map<string, number>();
  const outLane = new Map<string, number>();
  for (const arm of arms) {
    pack(drawn.filter((cell) => cell.approach === arm), span).forEach((value, key) => inLane.set(key, value));
    // Arriving traffic: a left turn lands in the inside lane, a right turn at the kerb.
    pack(drawn.filter((cell) => cell.exit_arm === arm), span).forEach((value, key) => outLane.set(key, value));
  }

  return drawn.map((cell) => {
    const key = movementKey(cell);
    const from = cell.approach as Arm;
    const to = cell.exit_arm as Arm;
    const a = inLane.get(key) ?? INNER_GAP;
    const b = outLane.get(key) ?? INNER_GAP;
    const start = at(from, FAR[from] + 4, a, approachSide(from));
    const stop = at(from, STOP[from], a, approachSide(from));
    const enter = at(to, STOP[to], b, exitSide(to));
    const end = at(to, FAR[to] + 4, b, exitSide(to));
    const reach = Math.hypot(enter[0] - stop[0], enter[1] - stop[1]) * (cell.turn === "through" ? 0.42 : 0.5);
    const c1 = add(stop, OUT[from], -reach);
    // Both control points sit inside the junction, so the curve leaves the stop line heading out.
    const c2 = add(enter, OUT[to], -reach);
    const f = (p: Vec) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
    return {
      key,
      cell,
      d: `M ${f(start)} L ${f(stop)} C ${f(c1)} ${f(c2)} ${f(enter)} L ${f(end)}`,
      width: scale(cell.count ?? 0),
      refWidth: cell.reference_count == null ? null : scale(cell.reference_count),
    };
  });
}

/** Where an arm's callout sits: outside its approach lanes, clear of the road. */
export const CALLOUT: Record<Arm, { x: number; y: number; anchor: "start" | "end" }> = {
  N: { x: CENTRE[0] - HALF_NS - 18, y: 36, anchor: "end" },
  S: { x: CENTRE[0] + HALF_NS + 18, y: VIEW.height - 104, anchor: "start" },
  E: { x: VIEW.width - 24, y: CENTRE[1] - HALF_EW - 104, anchor: "end" },
  W: { x: 24, y: CENTRE[1] + HALF_EW + 34, anchor: "start" },
};

export const ROAD = {
  centre: CENTRE,
  stop: STOP,
  at,
  approachSide,
  exitSide,
  out: OUT,
} as const;
