import { Tooltip } from "./Controls";
import { EVIDENCE, STATUS, type EvidenceKind, type Status } from "./grammar";

export function EvidenceGlyph({ kind, size = 12 }: { kind: EvidenceKind; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
      style={{ color: EVIDENCE[kind].colour }}
    >
      {kind === "observed" && <circle cx="6" cy="6" r="5" fill="currentColor" />}
      {kind === "approximated" && (
        <>
          <circle cx="6" cy="6" r="4.4" stroke="currentColor" strokeWidth="1.2" />
          <path d="M6 1.6a4.4 4.4 0 0 0 0 8.8z" fill="currentColor" />
        </>
      )}
      {kind === "assumed" && (
        <circle
          cx="6"
          cy="6"
          r="4.4"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeDasharray="2.3 1.15"
        />
      )}
      {kind === "model_generated" && (
        <path d="M6 .6 11.4 6 6 11.4.6 6z" stroke="currentColor" strokeWidth="1.2" />
      )}
      {kind === "model_generated" && <path d="M6 3.6 8.4 6 6 8.4 3.6 6z" fill="currentColor" />}
      {kind === "simulated" && (
        <g fill="currentColor">
          <rect x="1" y="1" width="4.4" height="4.4" />
          <rect x="6.6" y="1" width="4.4" height="4.4" />
          <rect x="1" y="6.6" width="4.4" height="4.4" />
          <rect x="6.6" y="6.6" width="4.4" height="4.4" />
        </g>
      )}
    </svg>
  );
}

export function EvidenceBadge({
  kind,
  note,
  compact = false,
  focusable = true,
  tooltipSide = "top",
  tooltipAlign = "start",
}: {
  kind: EvidenceKind;
  /** What this particular value is, after the kind's name. */
  note?: string;
  /** Glyph alone, with the name and meaning in a tooltip. */
  compact?: boolean;
  /** False keeps a compact badge out of the tab order, for a glyph repeated down a list. */
  focusable?: boolean;
  tooltipSide?: "top" | "bottom" | "left" | "right";
  tooltipAlign?: "start" | "end";
}) {
  const spec = EVIDENCE[kind];
  if (compact) {
    return (
      <Tooltip
        side={tooltipSide}
        align={tooltipAlign}
        content={
          <>
            <span style={{ color: spec.colour }}>{spec.label}.</span> {note ?? spec.meaning}
          </>
        }
      >
        <span
          {...(focusable ? { tabIndex: 0 } : {})}
          role="img"
          aria-label={`${spec.label}: ${note ?? spec.meaning}`}
          className="inline-flex size-6 items-center justify-center"
        >
          <EvidenceGlyph kind={kind} />
        </span>
      </Tooltip>
    );
  }
  return (
    <span className="mono inline-flex items-baseline gap-2">
      <span className="translate-y-[2px]">
        <EvidenceGlyph kind={kind} />
      </span>
      <span style={{ color: spec.colour }}>{spec.label}</span>
      {note ? <span className="text-paint-2">{note}</span> : null}
    </span>
  );
}

const LAMP_COLOUR = { stop: "var(--stop)", caution: "var(--amber)", go: "var(--go)" } as const;
const LAMP_ORDER = ["stop", "caution", "go"] as const;

/**
 * A horizontal signal head. Lamp position repeats the meaning, so colour is never alone. Unlit
 * lamps are solid discs: hollow rings at this size read as a row of zeros.
 */
export function SignalHead({ status }: { status: Status }) {
  const lit = STATUS[status].lamp;
  return (
    <svg width="30" height="12" viewBox="0 0 30 12" fill="none" aria-hidden="true" className="shrink-0">
      <rect x="0.5" y="0.5" width="29" height="11" stroke="var(--paint-4)" />
      {LAMP_ORDER.map((lamp, index) => (
        <circle
          key={lamp}
          cx={6 + index * 9}
          cy="6"
          r={lamp === lit ? 3.25 : 2.25}
          fill={lamp === lit ? LAMP_COLOUR[lamp] : "var(--paint-4)"}
        />
      ))}
    </svg>
  );
}

/**
 * `bare` is the head alone, for a mark repeated down a list. It never takes focus, because a
 * status read by a screen reader or a tooltip does not need a tab stop of its own. Inside a link
 * pass `decorative` and put the status in the link's name instead.
 */
export function StatusMark({
  status,
  bare = false,
  decorative = false,
  note,
}: {
  status: Status;
  bare?: boolean;
  decorative?: boolean;
  /** Replaces the status's general meaning with this value's own reason. */
  note?: string | null | undefined;
}) {
  const spec = STATUS[status];
  const meaning = note ?? spec.meaning;
  if (decorative) return <SignalHead status={status} />;
  if (bare) {
    return (
      <Tooltip content={`${spec.label}. ${meaning}`}>
        <span role="img" aria-label={`${spec.label}. ${meaning}`} className="inline-flex">
          <SignalHead status={status} />
        </span>
      </Tooltip>
    );
  }
  return (
    <span className="mono inline-flex items-center gap-2 text-paint-2">
      <SignalHead status={status} />
      <span>{spec.label}</span>
    </span>
  );
}
