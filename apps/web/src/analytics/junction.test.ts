import { describe, expect, it } from "vitest";
import type { MovementCell } from "../lib/analysis";
import { ribbons, type Arm } from "./junction";

const ARMS: readonly Arm[] = ["N", "E", "S", "W"];
const TURN: Record<string, MovementCell["turn"]> = {
  "N-E": "left", "N-S": "through", "N-W": "right",
  "E-S": "left", "E-W": "through", "E-N": "right",
  "S-W": "left", "S-N": "through", "S-E": "right",
  "W-N": "left", "W-E": "through", "W-S": "right",
};

const cells: MovementCell[] = ARMS.flatMap((from) =>
  ARMS.filter((to) => to !== from).map(
    (to, index) =>
      ({
        approach: from,
        exit_arm: to,
        turn: TURN[`${from}-${to}`],
        count: 10 + index * 40,
        reference_count: 12 + index * 35,
        status: "gate_pass",
      }) as MovementCell,
  ),
);

type Point = [number, number];
const minus = (a: Point, b: Point): Point => [a[0] - b[0], a[1] - b[1]];
const dot = (a: Point, b: Point) => a[0] * b[0] + a[1] * b[1];

/** "M start L stop C c1 c2 enter L end" as its six points. */
function points(d: string): Point[] {
  const numbers = d.match(/-?\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  const out: Point[] = [];
  for (let i = 0; i + 1 < numbers.length; i += 2) out.push([numbers[i] as number, numbers[i + 1] as number]);
  return out;
}

describe("junction ribbons", () => {
  const drawn = ribbons(cells);

  it("draw every movement once", () => {
    expect(drawn).toHaveLength(12);
  });

  it("never double back at a stop line", () => {
    for (const ribbon of drawn) {
      const [start, stop, c1, c2, enter, end] = points(ribbon.d) as [Point, Point, Point, Point, Point, Point];
      // The curve leaves the approach stop line still heading in, and reaches the exit stop line heading out.
      expect(dot(minus(c1, stop), minus(stop, start)), ribbon.key).toBeGreaterThan(0);
      expect(dot(minus(enter, c2), minus(end, enter)), ribbon.key).toBeGreaterThan(0);
    }
  });
});
