import { motion } from "motion/react";
import { Link } from "react-router-dom";
import { DUR, EASE, Label, Popover, SignalHead } from "../design";
import { comparatorWord, gateName, gateReading, limitWords, measuredWords, type Withheld } from "../domain/gates";

/**
 * A value the gate held back. This is the product doing what it says, so it takes the same place and
 * the same weight a number would, and states its case: never a blank, never a stand-in figure, and
 * never the reference count in this value's place.
 */

/** Which check stopped it, in full. Short enough to read in place, so nobody has to leave the page. */
export function WithheldCheck({ withheld }: { withheld: Withheld }) {
  const { check } = withheld;
  return (
    <div>
      <p className="m-0 flex items-center gap-2 font-display text-head leading-6 font-medium text-paint-1">
        <SignalHead status="gate_fail" />
        {withheld.gateId ? `Withheld by gate ${withheld.gateId}` : "Withheld by its gate"}
      </p>
      {check ? (
        <dl className="mono m-0 mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-0.5">
          <dt className="text-paint-3">Check</dt>
          <dd className="m-0 text-paint-1">{gateName(check.metric)}</dd>
          <dt className="text-paint-3">Measured</dt>
          <dd className="m-0 text-paint-1">
            {check.measured === null || check.measured === undefined
              ? "Not recorded"
              : `${measuredWords(check.measured)} (${gateReading(check.metric, check.measured)})`}
          </dd>
          <dt className="text-paint-3">Limit</dt>
          <dd className="m-0 text-paint-1">{`${comparatorWord(check.comparator)} ${limitWords(check.threshold)}`}</dd>
          <dt className="text-paint-3">Sample</dt>
          <dd className="m-0 text-paint-1">{`${check.n.toLocaleString("en-US")}, ${check.split === "held_out" ? "held-out read" : "development read"}`}</dd>
        </dl>
      ) : (
        <p className="mt-2 leading-snug text-paint-2">{withheld.reason}</p>
      )}
      <p className="m-0 mt-2 leading-snug text-paint-2">{withheld.limitNote}</p>
      {check ? <Label className="mt-1 block break-all">{`Gate file ${check.gate_sha256.slice(0, 12)}`}</Label> : null}
      <Link
        to="/evidence"
        className="mt-2 inline-flex min-h-6 items-center text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1 hover:decoration-amber"
      >
        Read every check
      </Link>
    </div>
  );
}

/** "20.33% count error, limit 20%", and that the limit came first: what the eye should land on. */
function Versus({ withheld }: { withheld: Withheld }) {
  return (
    <>
      {withheld.versus ? <span className="block text-paint-1">{withheld.versus}</span> : null}
      <span className="block text-paint-3">Limit fixed before the read</span>
    </>
  );
}

/** In a row of figures: the word stands where the number would, at the number's size. */
export function WithheldFigure({ what, withheld, size = "m" }: { what: string; withheld: Withheld; size?: "s" | "m" }) {
  const scale = size === "m" ? "text-num-m" : "text-num-s";
  return (
    <span className="block">
      <Popover
        label={`Why ${what} is withheld`}
        trigger={
          <motion.span
            className={`inline-block font-display leading-none font-medium tracking-[-0.01em] text-paint-1 underline decoration-stop decoration-2 underline-offset-[5px] ${scale}`}
            initial={{ y: "0.5em", opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: DUR.slow, ease: EASE }}
          >
            Withheld
          </motion.span>
        }
      >
        <WithheldCheck withheld={withheld} />
      </Popover>
      <span className="mono mt-2 block">
        <Versus withheld={withheld} />
      </span>
    </span>
  );
}

/** Small, for a figure set on the plan, where one word has to carry it. */
export function WithheldChip({
  name,
  what,
  withheld,
  align,
}: {
  name: string;
  what: string;
  withheld: Withheld;
  align: "start" | "end";
}) {
  return (
    <Popover
      label={`Why ${what} is withheld`}
      align={align}
      triggerClassName="flex items-baseline gap-1.5 border-l-2 border-stop pl-1.5 transition-colors duration-150 hover:bg-asphalt-2"
      trigger={
        <>
          <Label>{name}</Label>
          <span className="font-display text-num-s leading-none font-medium text-paint-1">Withheld</span>
        </>
      }
    >
      <WithheldCheck withheld={withheld} />
    </Popover>
  );
}

/**
 * Where a chart would be. The figure's own tile has already said Withheld, so this says why in a
 * sentence and keeps the check itself one click away.
 */
export function WithheldBlock({ what, withheld, note }: { what: string; withheld: Withheld; note?: string | null }) {
  return (
    <div className="mt-3 border-l-2 border-stop pl-4">
      <p className="m-0 max-w-[40rem] leading-snug text-paint-1">
        {`${what} failed its gate: no figure, and none is estimated in its place.`}
      </p>
      {note ? <p className="m-0 mt-2 max-w-[40rem] leading-snug text-paint-2">{note}</p> : null}
      <details className="mt-2 max-w-[26rem]">
        <summary className="mono cursor-pointer text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1">
          Which check stopped it
        </summary>
        <div className="mt-2">
          <WithheldCheck withheld={withheld} />
        </div>
      </details>
    </div>
  );
}
