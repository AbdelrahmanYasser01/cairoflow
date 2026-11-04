import { motion } from "motion/react";
import type { ElementType, ReactNode } from "react";
import { EvidenceBadge } from "./Evidence";
import type { EvidenceKind } from "./grammar";
import { DUR, EASE } from "./motion";

type RuleVariant = "hair" | "strong" | "lane" | "centre";

const RULE_CLASS: Record<RuleVariant, string> = {
  hair: "border-t border-rule-1",
  strong: "border-t border-rule-2",
  lane: "border-t border-dashed border-paint-4",
  // The double yellow centreline, kept for the one divide on a page that means something.
  centre: "h-[4px] border-y border-amber",
};

export function Rule({ variant = "hair", className = "" }: { variant?: RuleVariant; className?: string }) {
  return <div role="presentation" className={`${RULE_CLASS[variant]} ${className}`} />;
}

/** Mono micro-label: the instrument's own voice, sentence case. */
export function Label({
  as: Tag = "span",
  tone = "dim",
  className = "",
  children,
}: {
  as?: ElementType;
  tone?: "dim" | "body" | "paint";
  className?: string;
  children: ReactNode;
}) {
  const colour = { dim: "text-paint-3", body: "text-paint-2", paint: "text-paint-1" }[tone];
  return <Tag className={`mono ${colour} ${className}`}>{children}</Tag>;
}

const ABSENT_MARK = "inline-block h-[2px] w-3 shrink-0 bg-paint-4";

/**
 * A value that is deliberately not there, with the reason. Never a fake zero. `quiet` drops the
 * words for a row whose reason its group has already stated once; a screen reader still gets it.
 */
export function Absent({
  reason,
  quiet = false,
  className = "",
}: {
  reason: string;
  quiet?: boolean;
  className?: string;
}) {
  if (quiet) {
    return <span role="img" aria-label={reason} className={`${ABSENT_MARK} translate-y-[-3px] ${className}`} />;
  }
  return (
    <span className={`mono inline-flex items-baseline gap-2 text-left text-paint-3 ${className}`}>
      <span aria-hidden="true" className={`${ABSENT_MARK} translate-y-[-3px]`} />
      <span>{reason}</span>
    </span>
  );
}

const NUMERAL_SIZE = {
  s: "text-num-s",
  m: "text-num-m",
  l: "text-num-l",
} as const;

/**
 * A measured number. Digits settle into place one by one; they never count up, because a
 * counter would put numbers on screen that nothing measured.
 */
export function Numeral({
  value,
  unit,
  reason = "Not available",
  size = "m",
  evidence,
  evidenceNote,
  format = (n: number) => n.toLocaleString("en-US"),
}: {
  value: number | null;
  unit?: string;
  /** Shown instead of the number when value is null. */
  reason?: string;
  size?: keyof typeof NUMERAL_SIZE;
  evidence?: EvidenceKind;
  evidenceNote?: string | null | undefined;
  format?: (value: number) => string;
}) {
  if (value === null) return <Absent reason={reason} />;
  const text = format(value);
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span
        className={`inline-flex overflow-hidden font-display font-medium leading-none tracking-[-0.01em] ${NUMERAL_SIZE[size]}`}
        role="img"
        aria-label={unit ? `${text} ${unit}` : text}
      >
        {[...text].map((character, index) => (
          <motion.span
            // Keyed on position and digit: only a digit that changed settles again.
            key={`${index}-${character}`}
            aria-hidden="true"
            className="inline-block"
            initial={{ y: "0.5em", opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: DUR.slow, ease: EASE, delay: index * 0.035 }}
          >
            {character}
          </motion.span>
        ))}
      </span>
      {unit ? <span className="mono text-paint-3">{unit}</span> : null}
      {evidence ? (
        <span className="self-center">
          <EvidenceBadge kind={evidence} compact focusable={false} {...(evidenceNote ? { note: evidenceNote } : {})} />
        </span>
      ) : null}
    </span>
  );
}

/** One labelled line of a record: a value, or the reason there is none. */
export function Field({
  label,
  value,
  reason = "Not recorded",
  quiet = false,
  evidence,
}: {
  label: string;
  value?: ReactNode;
  reason?: string;
  /** The reason is stated once for the group: this row keeps only the mark. */
  quiet?: boolean;
  evidence?: EvidenceKind;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-rule-1 py-2.5">
      <Label className="shrink-0">{label}</Label>
      <span className="flex min-w-0 items-baseline gap-1 text-right">
        {value === undefined || value === null ? (
          <Absent reason={reason} quiet={quiet} />
        ) : (
          <span className="mono min-w-0 text-paint-1">{value}</span>
        )}
        {evidence ? (
          <span className="-my-1 inline-flex self-center">
            <EvidenceBadge kind={evidence} compact focusable={false} />
          </span>
        ) : null}
      </span>
    </div>
  );
}
