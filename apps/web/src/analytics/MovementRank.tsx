import { motion } from "motion/react";
import { DUR, EASE, EVIDENCE, Label } from "../design";
import type { MovementCell } from "../lib/analysis";
import { movementTitle, type Pairing } from "./JunctionFlows";
import { movementKey, type Arm } from "./junction";

/**
 * Movements ranked by count, one bar each: the table's own figures, sorted. Shown when a run has
 * no published pair to plot against.
 */
export function MovementRank({
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
  const rows = movements
    .filter((cell): cell is MovementCell & { count: number } => cell.count != null)
    .sort((a, b) => b.count - a.count);
  if (rows.length === 0) return null;
  const largest = Math.max(1, ...rows.map((cell) => cell.count));
  const colour = EVIDENCE[pairing.kind].colour;
  return (
    <div className="mt-6">
      <div className="flex items-baseline justify-between gap-3 border-b border-rule-1 pb-2">
        <Label tone="body">Busiest movements</Label>
        <Label>{`${pairing.main} count`}</Label>
      </div>
      <ol className="m-0 mt-2 list-none p-0" onMouseLeave={() => onActive(null)}>
        {rows.map((cell, index) => {
          const key = movementKey(cell);
          const lit = active ? key === active : focus ? cell.approach === focus : true;
          return (
            <li
              key={key}
              tabIndex={0}
              onMouseEnter={() => onActive(key)}
              onFocus={() => onActive(key)}
              onBlur={() => onActive(null)}
              className={`grid grid-cols-[minmax(0,9.5rem)_minmax(0,1fr)_2.75rem] items-center gap-3 border-l-2 py-[5px] pl-2 outline-none transition-[opacity,border-color] duration-200 ${
                active === key ? "border-amber" : "border-transparent"
              }`}
              style={{ opacity: lit ? 1 : 0.35 }}
            >
              <span className="mono truncate text-micro text-paint-2">{movementTitle(cell)}</span>
              <span aria-hidden="true" className="block h-[6px] bg-rule-1">
                <motion.span
                  className="block h-full"
                  style={{ background: colour }}
                  initial={{ width: 0 }}
                  animate={{ width: `${(cell.count / largest) * 100}%` }}
                  transition={{ duration: DUR.draw, ease: EASE, delay: 0.1 + index * 0.03 }}
                />
              </span>
              <span className="mono text-right text-paint-1">{cell.count}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
