import { motion, useReducedMotion } from "motion/react";
import { useMemo, type ReactElement } from "react";
import { DUR, EASE, EVIDENCE, Label, StatusMark, type EvidenceKind } from "../design";
import { ARM_NAMES, TURNS } from "../domain/vocabulary";
import type { MovementCell } from "../lib/analysis";
import { CALLOUT, HALF_EW, HALF_NS, ROAD, VIEW, movementKey, ribbons, type Arm } from "./junction";
import "./analytics.css";

const ARMS: readonly Arm[] = ["N", "E", "S", "W"];
const TO_NAME: Record<Arm, string> = { N: "north", E: "east", S: "south", W: "west" };
const turnLabel = (turn: string) => TURNS.find((item) => item.id === turn)?.label ?? turn;
/** Flow dots move at one speed, in view units per second; their spacing follows the count. */
const FLOW_SPEED = 34;
const flowPeriod = (count: number, busiest: number) => Math.round(14 + 96 * (1 - Math.sqrt(count / busiest)));
const POOLED_NOTE = "Turning counts were checked pooled over held-out blocks, not movement by movement.";

export interface Pairing {
  /** The main count's own name: "Camera" for a camera run, "NGSIM" for the published file. */
  main: string;
  /** True when each cell also carries the published reference count. */
  paired: boolean;
  kind: EvidenceKind;
}

export function movementTitle(cell: MovementCell): string {
  return `${ARM_NAMES[cell.approach].from} → ${TO_NAME[cell.exit_arm as Arm]}`;
}

/** Road markings: two carriageways, lane lines, stop lines and crossings, drawn as a plan. */
function Road() {
  const [cx, cy] = ROAD.centre;
  const { width: W, height: H } = VIEW;
  const lanesNS = [-64, -32, 32, 64];
  const lanesEW = [-30, 30];
  const zebra = (arm: Arm) => {
    const stripes: ReactElement[] = [];
    const half = arm === "N" || arm === "S" ? HALF_NS : HALF_EW;
    const near = arm === "N" || arm === "S" ? HALF_EW + 7 : HALF_NS + 7;
    const deep = ROAD.stop[arm] - 7 - near;
    for (let lateral = -half + 6; lateral < half - 4; lateral += 12) {
      const a = ROAD.at(arm, near, lateral, ROAD.exitSide(arm));
      const vertical = arm === "N" || arm === "S";
      const x = vertical ? a[0] : arm === "E" ? a[0] : a[0] - deep;
      const y = vertical ? (arm === "N" ? a[1] - deep : a[1]) : a[1];
      stripes.push(
        <rect
          key={`${arm}${lateral}`}
          x={vertical ? x - 3 : x}
          y={vertical ? y : y - 3}
          width={vertical ? 6 : deep}
          height={vertical ? deep : 6}
          fill="var(--rule-1)"
        />,
      );
    }
    return stripes;
  };
  return (
    <g aria-hidden="true">
      <rect x={cx - HALF_NS} y={0} width={HALF_NS * 2} height={H} fill="var(--asphalt-1)" />
      <rect x={0} y={cy - HALF_EW} width={W} height={HALF_EW * 2} fill="var(--asphalt-1)" />
      {/* Kerbs */}
      {[
        [cx - HALF_NS, 0, cx - HALF_NS, cy - HALF_EW],
        [cx + HALF_NS, 0, cx + HALF_NS, cy - HALF_EW],
        [cx - HALF_NS, cy + HALF_EW, cx - HALF_NS, H],
        [cx + HALF_NS, cy + HALF_EW, cx + HALF_NS, H],
        [0, cy - HALF_EW, cx - HALF_NS, cy - HALF_EW],
        [0, cy + HALF_EW, cx - HALF_NS, cy + HALF_EW],
        [cx + HALF_NS, cy - HALF_EW, W, cy - HALF_EW],
        [cx + HALF_NS, cy + HALF_EW, W, cy + HALF_EW],
      ].map(([x1, y1, x2, y2]) => (
        <line key={`${x1}-${y1}-${x2}-${y2}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--rule-2)" strokeWidth={1.5} />
      ))}
      {/* Lane lines */}
      {lanesNS.map((dx) => (
        <g key={`ns${dx}`} stroke="var(--rule-2)" strokeDasharray="10 14" strokeWidth={1}>
          <line x1={cx + dx} y1={0} x2={cx + dx} y2={cy - ROAD.stop.N} />
          <line x1={cx + dx} y1={cy + ROAD.stop.S} x2={cx + dx} y2={H} />
        </g>
      ))}
      {lanesEW.map((dy) => (
        <g key={`ew${dy}`} stroke="var(--rule-2)" strokeDasharray="10 14" strokeWidth={1}>
          <line x1={0} y1={cy + dy} x2={cx - ROAD.stop.W} y2={cy + dy} />
          <line x1={cx + ROAD.stop.E} y1={cy + dy} x2={W} y2={cy + dy} />
        </g>
      ))}
      {/* Centre lines */}
      <g stroke="var(--paint-4)" strokeWidth={1.2}>
        {[-2.5, 2.5].map((o) => (
          <g key={o}>
            <line x1={cx + o} y1={0} x2={cx + o} y2={cy - ROAD.stop.N} />
            <line x1={cx + o} y1={cy + ROAD.stop.S} x2={cx + o} y2={H} />
            <line x1={0} y1={cy + o} x2={cx - ROAD.stop.W} y2={cy + o} />
            <line x1={cx + ROAD.stop.E} y1={cy + o} x2={W} y2={cy + o} />
          </g>
        ))}
      </g>
      {/* Stop lines, across each approach */}
      <g stroke="var(--paint-3)" strokeWidth={3}>
        <line x1={cx - HALF_NS} y1={cy - ROAD.stop.N} x2={cx - 4} y2={cy - ROAD.stop.N} />
        <line x1={cx + 4} y1={cy + ROAD.stop.S} x2={cx + HALF_NS} y2={cy + ROAD.stop.S} />
        <line x1={cx + ROAD.stop.E} y1={cy - HALF_EW} x2={cx + ROAD.stop.E} y2={cy - 4} />
        <line x1={cx - ROAD.stop.W} y1={cy + 4} x2={cx - ROAD.stop.W} y2={cy + HALF_EW} />
      </g>
      {ARMS.flatMap(zebra)}
    </g>
  );
}

function Callout({
  arm,
  cells,
  pairing,
  isLit,
  onActive,
}: {
  arm: Arm;
  cells: readonly MovementCell[];
  pairing: Pairing;
  isLit: (cell: MovementCell) => boolean;
  onActive: (key: string | null) => void;
}) {
  const spot = CALLOUT[arm];
  const rows = [...cells].sort(
    (a, b) => TURNS.findIndex((t) => t.id === a.turn) - TURNS.findIndex((t) => t.id === b.turn),
  );
  const end = spot.anchor === "end";
  return (
    <g fontFamily="var(--font-mono)">
      <text x={spot.x} y={spot.y} textAnchor={spot.anchor} fill="var(--paint-3)" fontSize={12} letterSpacing="0.08em">
        {ARM_NAMES[arm].from.toUpperCase()}
      </text>
      {rows.map((cell, index) => {
        const y = spot.y + 26 + index * 24;
        const lit = isLit(cell);
        const ref = pairing.paired && cell.reference_count != null ? ` / ${cell.reference_count}` : "";
        return (
          <g
            key={movementKey(cell)}
            opacity={lit ? 1 : 0.3}
            onMouseEnter={() => onActive(movementKey(cell))}
            onMouseLeave={() => onActive(null)}
            style={{ cursor: "default", transition: "opacity 200ms" }}
          >
            <rect x={end ? spot.x - 176 : spot.x - 4} y={y - 17} width={180} height={23} fill="transparent" />
            <text x={spot.x} y={y} textAnchor={spot.anchor} fontSize={15}>
              {end ? (
                <>
                  <tspan fill="var(--paint-3)">{`${turnLabel(cell.turn)}  `}</tspan>
                  <tspan fill="var(--paint-1)" fontWeight={500}>
                    {cell.count ?? "–"}
                  </tspan>
                  <tspan fill="var(--paint-3)">{ref}</tspan>
                </>
              ) : (
                <>
                  <tspan fill="var(--paint-1)" fontWeight={500}>
                    {cell.count ?? "–"}
                  </tspan>
                  <tspan fill="var(--paint-3)">{ref}</tspan>
                  <tspan fill="var(--paint-3)">{`  ${turnLabel(cell.turn)}`}</tspan>
                </>
              )}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/**
 * The hero: every turning movement as a ribbon through a schematic junction. Width is the count;
 * a pale band behind it is the published NGSIM count for the same movement.
 */
export function JunctionFlows({
  movements,
  pairing,
  focus,
  active,
  onActive,
}: {
  movements: readonly MovementCell[];
  pairing: Pairing;
  focus: Arm | null;
  active: string | null;
  onActive: (key: string | null) => void;
}) {
  const drawn = useMemo(() => ribbons(movements), [movements]);
  const still = useReducedMotion() ?? false;
  const busiest = Math.max(1, ...movements.map((cell) => cell.count ?? 0));
  const colour = EVIDENCE[pairing.kind].colour;
  const isLit = (cell: MovementCell) =>
    active ? movementKey(cell) === active : focus ? cell.approach === focus : true;
  const current = active ? movements.find((cell) => movementKey(cell) === active) : undefined;
  const [cx, cy] = ROAD.centre;

  return (
    <div className="relative">
      {/* Readout for the movement under the pointer: a strip above the plan, never over it. */}
      <div className="flex h-14 items-center gap-6 border-b border-rule-1" aria-live="polite">
        {current ? (
          <motion.div
            key={active}
            initial={{ opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: DUR.base, ease: EASE }}
            className="flex min-w-0 items-center gap-6 border-l-2 border-amber pl-3"
          >
            <span className="min-w-0">
              <Label tone="paint" className="block truncate">
                {movementTitle(current)}
              </Label>
              <Label className="block">{current.turn === "through" ? "Through" : `${turnLabel(current.turn)} turn`}</Label>
            </span>
            <span className="flex items-baseline gap-1.5">
              <span className="font-display text-num-m leading-none font-medium" style={{ color: colour }}>
                {current.count ?? "–"}
              </span>
              <Label>{pairing.main}</Label>
            </span>
            {pairing.paired && current.reference_count != null ? (
              <span className="flex items-baseline gap-1.5">
                <span className="font-display text-num-m leading-none font-medium text-paint-2">
                  {current.reference_count}
                </span>
                <Label>NGSIM</Label>
              </span>
            ) : null}
            {current.status === "gate_pass" ? (
              <span className="flex items-center gap-2">
                <StatusMark status={current.status} bare note={POOLED_NOTE} />
                <Label>Pooled check passed</Label>
              </span>
            ) : (
              <StatusMark status={current.status} />
            )}
          </motion.div>
        ) : (
          <Label>
            {`${movements.filter((cell) => cell.count != null && (focus === null || cell.approach === focus)).length} movements · ribbon width = vehicles`}
          </Label>
        )}
      </div>
      <svg
        viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
        className="block h-auto w-full"
        role="img"
        aria-label={`Turning movements as ribbons through a schematic junction. Width is the ${pairing.main.toLowerCase()} count. The table beside it lists every value.`}
        onMouseLeave={() => onActive(null)}
      >
        <defs>
          <pattern id="cf-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--rule-1)" strokeWidth="0.6" opacity="0.55" />
          </pattern>
          <filter id="cf-glow" x={-0.2} y={-0.2} width={1.4} height={1.4}>
            <feGaussianBlur stdDeviation="6" />
          </filter>
          <radialGradient id="cf-vignette" cx={0.5} cy={0.5} r={0.65}>
            <stop offset={0.55} stopColor="var(--asphalt-0)" stopOpacity="0" />
            <stop offset={1} stopColor="var(--asphalt-0)" stopOpacity="0.9" />
          </radialGradient>
        </defs>
        <rect width={VIEW.width} height={VIEW.height} fill="url(#cf-grid)" />
        <Road />

        {/* Published reference: a pale band at its own width, under the measured ribbon. */}
        {pairing.paired
          ? drawn.map((ribbon) =>
              ribbon.refWidth === null ? null : (
                <path
                  key={`ref${ribbon.key}`}
                  className="cf-ribbon"
                  d={ribbon.d}
                  fill="none"
                  stroke="var(--ev-observed)"
                  strokeOpacity={0.2}
                  strokeWidth={ribbon.refWidth + 2}
                  opacity={isLit(ribbon.cell) ? 1 : 0.15}
                />
              ),
            )
          : null}

        <g filter="url(#cf-glow)" aria-hidden="true">
          {drawn.map((ribbon) => (
            <path
              key={`glow${ribbon.key}`}
              className="cf-ribbon"
              d={ribbon.d}
              fill="none"
              stroke={colour}
              strokeWidth={ribbon.width + 4}
              opacity={isLit(ribbon.cell) ? (active ? 0.55 : 0.3) : 0}
            />
          ))}
        </g>

        {drawn.map((ribbon, index) => (
          <motion.path
            key={`main${ribbon.key}`}
            className="cf-ribbon"
            d={ribbon.d}
            fill="none"
            stroke={colour}
            strokeWidth={ribbon.width}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: DUR.draw, ease: EASE, delay: 0.15 + index * 0.05 }}
            opacity={isLit(ribbon.cell) ? 0.92 : 0.12}
          />
        ))}

        {/* Flow: dots travel from approach to exit, closer together on busier movements. */}
        {drawn.map((ribbon) => {
          const period = flowPeriod(ribbon.cell.count ?? 0, busiest);
          return (
            <motion.path
              key={`flow${ribbon.key}`}
              className="cf-ribbon"
              d={ribbon.d}
              fill="none"
              stroke={pairing.kind === "observed" ? "var(--asphalt-0)" : "var(--paint-1)"}
              strokeOpacity={0.85}
              strokeWidth={Math.min(3, Math.max(1, ribbon.width * 0.22))}
              strokeDasharray={`2 ${period - 2}`}
              strokeLinecap="round"
              opacity={isLit(ribbon.cell) ? 1 : 0}
              pointerEvents="none"
              initial={{ strokeDashoffset: 0 }}
              animate={still ? { strokeDashoffset: 0 } : { strokeDashoffset: -period }}
              transition={still ? { duration: 0 } : { duration: period / FLOW_SPEED, ease: "linear", repeat: Infinity }}
            />
          );
        })}

        {/* Hit areas, wider than thin ribbons. */}
        {drawn.map((ribbon) => (
          <path
            key={`hit${ribbon.key}`}
            d={ribbon.d}
            fill="none"
            stroke="transparent"
            strokeWidth={Math.max(16, (ribbon.refWidth ?? 0) + 6, ribbon.width + 6)}
            onMouseEnter={() => onActive(ribbon.key)}
          >
            <title>{`${movementTitle(ribbon.cell)}, ${turnLabel(ribbon.cell.turn).toLowerCase()}: ${pairing.main.toLowerCase()} ${ribbon.cell.count}${pairing.paired && ribbon.cell.reference_count != null ? `, NGSIM ${ribbon.cell.reference_count}` : ""}`}</title>
          </path>
        ))}

        <rect width={VIEW.width} height={VIEW.height} fill="url(#cf-vignette)" pointerEvents="none" />

        {/* Compass letters at the arm ends. */}
        <g fontFamily="var(--font-mono)" fontSize={12} fill="var(--paint-4)" textAnchor="middle" aria-hidden="true">
          <text x={cx} y={16}>N</text>
          <text x={cx} y={VIEW.height - 8}>S</text>
          <text x={VIEW.width - 10} y={cy + 4}>E</text>
          <text x={10} y={cy + 4}>W</text>
        </g>

        {/* Callouts shrink with the plan; on a phone the table below carries every value. */}
        <g className="max-sm:hidden">
          {ARMS.map((arm) => {
            const cells = movements.filter((cell) => cell.approach === arm && cell.count != null);
            return cells.length > 0 ? (
              <Callout key={arm} arm={arm} cells={cells} pairing={pairing} isLit={isLit} onActive={onActive} />
            ) : null;
          })}
        </g>
      </svg>

    </div>
  );
}
