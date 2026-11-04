import { useState } from "react";
import { ARM_ORDER, exitCross, inbound, onArm, turnCross, type Project, type SitePlanArms } from "../domain/plan";
import type { MovementCell } from "../lib/analysis";
import type { Arm, Point2, SiteGeometry } from "../twin/site";

/** How far past the stop line each movement's count is set, in metres. */
const LABEL_BACK_M = 15;
const STUB_M = 9;
const MIN_WIDTH_PX = 1.25;
const MAX_WIDTH_PX = 12;

/** Clear space between two counts on one approach, in pixels: side by side, and stacked. */
const LABEL_ROOM_X_PX = 30;
const LABEL_ROOM_Y_PX = 16;

interface Ribbon {
  cell: MovementCell;
  d: string;
  label: [number, number];
  width: number;
  key: string;
}

/**
 * Two turns can share a lane, and so the same spot. Moves an approach's counts apart along the
 * stop line, keeping them centred where they started.
 */
function spreadLabels(ribbons: readonly Ribbon[]): void {
  for (const arm of ARM_ORDER) {
    const axis = arm === "E" || arm === "W" ? 1 : 0;
    const room = axis === 1 ? LABEL_ROOM_Y_PX : LABEL_ROOM_X_PX;
    const group = ribbons.filter((ribbon) => ribbon.cell.approach === arm).sort((a, b) => a.label[axis] - b.label[axis]);
    if (group.length < 2) continue;
    const before = group.reduce((sum, ribbon) => sum + ribbon.label[axis], 0);
    group.forEach((ribbon, index) => {
      const previous = group[index - 1];
      if (previous) ribbon.label[axis] = Math.max(ribbon.label[axis], previous.label[axis] + room);
    });
    const shift = (before - group.reduce((sum, ribbon) => sum + ribbon.label[axis], 0)) / group.length;
    for (const ribbon of group) ribbon.label[axis] += shift;
  }
}

function ribbonOf(
  cell: MovementCell,
  site: SiteGeometry,
  arms: SitePlanArms,
  project: Project,
  largest: number,
): Ribbon | null {
  const from = arms[cell.approach];
  const to = arms[cell.exit_arm];
  if (!from || !to || cell.count == null || cell.count <= 0) return null;
  const startCross = turnCross(from, cell.turn);
  const endCross = exitCross(site, to);
  const start = onArm(from.arm, startCross, from.near);
  const end = onArm(to.arm, endCross, to.near);
  const back = onArm(from.arm, startCross, from.near + STUB_M);
  const onward = onArm(to.arm, endCross, to.near + STUB_M);
  const reach = Math.hypot(end[0] - start[0], end[1] - start[1]) * 0.42;
  const [fu, fv] = inbound(from.arm);
  const [gu, gv] = inbound(to.arm);
  const first: Point2 = [start[0] + fu * reach, start[1] + fv * reach];
  const second: Point2 = [end[0] + gu * reach, end[1] + gv * reach];
  const at = (point: Point2) => project(point[0], point[1]).map((value) => Math.round(value * 10) / 10).join(" ");
  return {
    cell,
    key: `${cell.approach}-${cell.exit_arm}-${cell.turn}`,
    d: `M${at(back)}L${at(start)}C${at(first)} ${at(second)} ${at(end)}L${at(onward)}`,
    label: [...project(...onArm(from.arm, startCross, from.near + LABEL_BACK_M))],
    width: MIN_WIDTH_PX + (cell.count / largest) * (MAX_WIDTH_PX - MIN_WIDTH_PX),
  };
}

/**
 * The turning-movement diagram, drawn on the plan. Width is the count; the path itself is schematic,
 * from the lanes carrying the turn to the exit they leave by. Each count sits in its own lanes.
 */
export function MovementLayer({
  site,
  arms,
  project,
  movements,
  focus,
}: {
  site: SiteGeometry;
  arms: SitePlanArms;
  project: Project;
  movements: readonly MovementCell[];
  /** Only this arm's movements stay lit. */
  focus: Arm | null;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const largest = Math.max(1, ...movements.map((cell) => cell.count ?? 0));
  const ribbons = movements
    .map((cell) => ribbonOf(cell, site, arms, project, largest))
    .filter((ribbon): ribbon is Ribbon => ribbon !== null)
    .sort((a, b) => b.width - a.width);
  spreadLabels(ribbons);

  const lit = (ribbon: Ribbon) => hover === ribbon.key || (hover === null && focus === ribbon.cell.approach);
  const dim = (ribbon: Ribbon) => (hover !== null || focus !== null) && !lit(ribbon);

  return (
    <g style={{ pointerEvents: "auto" }} onMouseLeave={() => setHover(null)}>
      {ribbons.map((ribbon) => (
        <path
          key={ribbon.key}
          d={ribbon.d}
          stroke={lit(ribbon) ? "var(--amber)" : "var(--paint-2)"}
          strokeWidth={ribbon.width}
          opacity={dim(ribbon) ? 0.22 : lit(ribbon) ? 1 : 0.72}
          strokeLinejoin="round"
        />
      ))}
      {ribbons.map((ribbon) => (
        <path
          key={`hit-${ribbon.key}`}
          d={ribbon.d}
          stroke="transparent"
          strokeWidth={Math.max(14, ribbon.width)}
          onMouseEnter={() => setHover(ribbon.key)}
        />
      ))}
      {ribbons.map((ribbon) => (
        <text
          key={`count-${ribbon.key}`}
          x={ribbon.label[0]}
          y={ribbon.label[1] + 4}
          textAnchor="middle"
          fill={dim(ribbon) ? "var(--paint-3)" : "var(--paint-1)"}
          stroke="var(--asphalt-0)"
          strokeWidth="4"
          paintOrder="stroke"
          fontFamily="var(--font-mono)"
          fontSize="12"
          style={{ pointerEvents: "none" }}
        >
          {ribbon.cell.count}
        </text>
      ))}
    </g>
  );
}
