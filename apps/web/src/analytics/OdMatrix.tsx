import { EVIDENCE, Label } from "../design";
import { TURNS } from "../domain/vocabulary";
import type { MovementCell } from "../lib/analysis";
import { movementTitle, type Pairing } from "./JunctionFlows";
import { movementKey, type Arm } from "./junction";

const ARMS: readonly Arm[] = ["N", "E", "S", "W"];
const SHORT: Record<Arm, string> = { N: "North", E: "East", S: "South", W: "West" };
const turnLabel = (turn: string) => TURNS.find((item) => item.id === turn)?.label ?? turn;
/** A turn as the arrow a lane marking would use. */
const TURN_GLYPH: Record<string, string> = { left: "↰", through: "↑", right: "↱", u_turn: "↶" };

/**
 * Approaches against exits. Shade is the count; a cell with a published reference shows both
 * figures and two bars, so the pair reads without a computed difference.
 */
export function OdMatrix({
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
  const colour = EVIDENCE[pairing.kind].colour;
  const largest = Math.max(
    1,
    ...movements.map((cell) => Math.max(cell.count ?? 0, pairing.paired ? (cell.reference_count ?? 0) : 0)),
  );
  const find = (from: Arm, to: Arm) => movements.find((cell) => cell.approach === from && cell.exit_arm === to);

  return (
    <div className="mt-3" onMouseLeave={() => onActive(null)}>
      <table className="w-full table-fixed border-collapse">
        <caption className="sr-only">
          {`Turning movements, approach by exit. Each cell: ${pairing.main.toLowerCase()} count${pairing.paired ? ", then the published NGSIM count" : ""}.`}
        </caption>
        <thead>
          <tr>
            <th scope="col" className="w-[4.5rem] pb-2 text-left align-bottom">
              <Label className="block text-micro leading-tight">From · to</Label>
            </th>
            {ARMS.map((arm) => (
              <th key={arm} scope="col" className="pb-2 text-left align-bottom">
                <Label tone="body">{SHORT[arm]}</Label>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ARMS.map((from) => (
            <tr key={from}>
              <th scope="row" className="pr-2 text-left">
                <Label tone={focus === from ? "paint" : "body"}>{SHORT[from]}</Label>
              </th>
              {ARMS.map((to) => {
                const cell = find(from, to);
                if (!cell || cell.count == null) {
                  return (
                    <td key={to} className="p-[2px]">
                      <div
                        role="img"
                        aria-label={from === to ? "U-turn: none recorded" : "None recorded"}
                        className="h-[4.75rem] border border-dashed border-rule-1"
                      />
                    </td>
                  );
                }
                const key = movementKey(cell);
                const lit = active ? key === active : focus ? from === focus : true;
                const share = cell.count / largest;
                const ref = pairing.paired ? (cell.reference_count ?? null) : null;
                return (
                  <td key={to} className="p-[2px]">
                    <div
                      tabIndex={0}
                      onMouseEnter={() => onActive(key)}
                      onFocus={() => onActive(key)}
                      onBlur={() => onActive(null)}
                      title={`${movementTitle(cell)}, ${turnLabel(cell.turn).toLowerCase()}`}
                      className={`relative flex h-[4.75rem] flex-col justify-between overflow-hidden border p-2 outline-none transition-[opacity,border-color] duration-200 ${
                        active === key ? "border-amber" : "border-rule-1"
                      } focus-visible:border-amber`}
                      style={{ opacity: lit ? 1 : 0.35 }}
                    >
                      <span
                        aria-hidden="true"
                        className="absolute inset-0"
                        style={{ background: colour, opacity: 0.06 + share * 0.34 }}
                      />
                      <span className="relative flex items-baseline justify-between gap-1">
                        <span className="font-display text-num-s leading-none font-medium text-paint-1">{cell.count}</span>
                        <span className={`mono ${share > 0.6 ? "text-paint-1" : "text-paint-3"}`} aria-label={turnLabel(cell.turn)}>
                          {TURN_GLYPH[cell.turn] ?? turnLabel(cell.turn)}
                        </span>
                      </span>
                      <span className="relative block">
                        {ref !== null ? (
                          <span className="mono block text-micro whitespace-nowrap text-paint-2" title={`NGSIM ${ref}`}>{`/ ${ref}`}</span>
                        ) : null}
                        <span aria-hidden="true" className="mt-1 block h-[3px] bg-asphalt-0/60">
                          <span className="block h-full" style={{ width: `${(cell.count / largest) * 100}%`, background: colour }} />
                        </span>
                        {ref !== null ? (
                          <span aria-hidden="true" className="mt-[2px] block h-[3px] bg-asphalt-0/60">
                            <span className="block h-full bg-ev-observed/70" style={{ width: `${(ref / largest) * 100}%` }} />
                          </span>
                        ) : null}
                      </span>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
