import { AnimatePresence, motion } from "motion/react";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import { DUR, EASE } from "./motion";

type ButtonTone = "primary" | "quiet" | "text";

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 font-display text-body font-medium " +
  "leading-none tracking-[0.005em] whitespace-nowrap transition-colors duration-150 select-none";

const BUTTON_TONE: Record<ButtonTone, string> = {
  primary: "min-h-10 px-4 bg-amber text-amber-ink hover:bg-paint-1",
  quiet:
    "min-h-10 px-4 border border-rule-2 bg-asphalt-2 text-paint-1 hover:bg-asphalt-3 hover:border-paint-4",
  text: "min-h-6 text-paint-2 underline decoration-rule-2 underline-offset-4 hover:text-paint-1 hover:decoration-amber",
};

const BUTTON_DISABLED =
  "min-h-10 px-4 border border-rule-1 bg-transparent text-paint-3 cursor-not-allowed";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: ButtonTone;
  /** Why the control cannot be used. A disabled control always says why. */
  disabledReason?: string | undefined;
  /** Fill the width of the instrument. */
  block?: boolean;
}

export function Button({
  tone = "quiet",
  disabledReason,
  block = false,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const disabled = Boolean(disabledReason);
  const look = disabled ? BUTTON_DISABLED : BUTTON_TONE[tone];
  const button = (
    <button
      type="button"
      {...rest}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : rest.onClick}
      className={`${BUTTON_BASE} ${look} ${block ? "w-full" : ""} ${className} active:translate-y-px`}
    >
      {children}
    </button>
  );
  return disabledReason ? (
    <Tooltip content={disabledReason} block={block}>
      {button}
    </Tooltip>
  ) : (
    button
  );
}

export function ButtonLink({
  to,
  tone = "primary",
  className = "",
  children,
}: {
  to: string;
  tone?: ButtonTone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link to={to} className={`${BUTTON_BASE} ${BUTTON_TONE[tone]} ${className} active:translate-y-px`}>
      {children}
    </Link>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

/** A native select in the instrument voice, so keyboard and screen reader behaviour come for free. */
export function Select({
  label,
  value,
  options,
  onChange,
  className = "",
}: {
  /** Accessible name. */
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <span className={`relative inline-flex min-w-0 items-center ${className}`}>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mono w-full min-w-0 cursor-pointer appearance-none truncate border-b border-rule-2 bg-transparent py-1 pr-5 text-paint-1 transition-colors duration-150 hover:border-amber"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} className="bg-asphalt-2 text-paint-1">
            {option.label}
          </option>
        ))}
      </select>
      <svg
        aria-hidden="true"
        width="10"
        height="6"
        viewBox="0 0 10 6"
        fill="none"
        className="pointer-events-none absolute right-1 text-paint-3"
      >
        <path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.25" />
      </svg>
    </span>
  );
}

/** A number typed by the operator. The text is kept as typed, so a half-written value can stand. */
export function NumberField({
  label,
  unit,
  value,
  min,
  step,
  disabled = false,
  onChange,
}: {
  label: string;
  unit?: string;
  value: string;
  min?: number;
  step?: number | "any";
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex items-baseline justify-between gap-4 border-b border-rule-1 py-2">
      <span className="mono text-paint-3">{label}</span>
      <span className="flex items-baseline gap-1.5">
        <input
          type="number"
          inputMode="decimal"
          value={value}
          min={min}
          step={step}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className="mono w-20 border-b border-rule-2 bg-transparent py-0.5 text-right text-paint-1 transition-colors duration-150 hover:border-amber disabled:cursor-not-allowed disabled:text-paint-3"
        />
        {unit ? <span className="mono w-4 text-paint-3">{unit}</span> : null}
      </span>
    </label>
  );
}

/**
 * How much of something real is done. It moves only when its value does: there is no easing and no
 * idle motion, because a bar that moves by itself says something nothing reported.
 */
export function ProgressBar({ label, value, max }: { label: string; value: number; max: number }) {
  const share = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className="h-1.5 w-full bg-rule-1"
    >
      <div className="h-full bg-amber" style={{ width: `${share * 100}%` }} />
    </div>
  );
}

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  disabled?: boolean;
  /** Why this one option is off, shown on it alone. */
  reason?: string;
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  disabledReason,
}: {
  /** Accessible group name. */
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  disabledReason?: string | undefined;
}) {
  const id = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const groupDisabled = Boolean(disabledReason);

  function step(event: KeyboardEvent, from: number) {
    const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
    if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    for (let n = 1; n <= options.length; n += 1) {
      const next = (from + delta * n + options.length) % options.length;
      const option = options[next];
      if (option && !option.disabled) {
        onChange(option.value);
        refs.current[next]?.focus();
        return;
      }
    }
  }

  const group = (
    <div
      role="radiogroup"
      aria-label={label}
      aria-disabled={groupDisabled || undefined}
      className="inline-flex border border-rule-2 bg-asphalt-1"
    >
      {options.map((option, index) => {
        const selected = option.value === value;
        const off = groupDisabled || option.disabled;
        const button = (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-disabled={off || undefined}
            tabIndex={selected ? 0 : -1}
            onClick={off ? undefined : () => onChange(option.value)}
            onKeyDown={off ? undefined : (event) => step(event, index)}
            className={`mono relative min-h-8 px-3 transition-colors duration-150 ${
              off
                ? "cursor-not-allowed text-paint-3"
                : selected
                  ? "text-paint-1"
                  : "text-paint-2 hover:text-paint-1"
            }`}
          >
            {selected && (
              <motion.span
                layoutId={`segment-${id}`}
                className={`absolute inset-0 border-b-2 bg-asphalt-3 ${groupDisabled ? "border-paint-4" : "border-amber"}`}
                transition={{ duration: DUR.base, ease: EASE }}
              />
            )}
            <span className="relative">{option.label}</span>
          </button>
        );
        return option.disabled && option.reason ? (
          <Tooltip key={option.value} content={option.reason} side="bottom" align="end">
            {button}
          </Tooltip>
        ) : (
          button
        );
      })}
    </div>
  );

  return disabledReason ? <Tooltip content={disabledReason}>{group}</Tooltip> : group;
}

type TooltipSide = "top" | "bottom" | "left" | "right";

const TOOLTIP_SIDE: Record<TooltipSide, string> = {
  top: "bottom-full left-0 mb-2",
  bottom: "top-full left-0 mt-2",
  left: "right-full top-0 mr-2",
  right: "left-full top-0 ml-2",
};

/** Clear space kept between a tooltip and the edge of the window, in pixels. */
const EDGE_PX = 8;
/** The context strip's height: a tooltip above this line would slide under the strip. */
const STRIP_PX = 44;

/** A bubble never leaves the window: main clips overflow, so one past the edge is simply cut off. */
function fitInWindow(
  host: HTMLElement,
  tip: HTMLElement,
  side: TooltipSide,
  align: "start" | "end",
): { shiftPx: number; below: boolean } {
  const box = host.getBoundingClientRect();
  const width = tip.offsetWidth;
  const left = align === "end" ? box.right - width : box.left;
  const limit = document.documentElement.clientWidth - EDGE_PX;
  const shiftPx = side === "left" || side === "right" ? 0 : Math.min(0, limit - (left + width)) + Math.max(0, EDGE_PX - left);
  return { shiftPx, below: side === "top" && box.top - tip.offsetHeight - EDGE_PX < STRIP_PX };
}

/**
 * What a reader asks for, as a click and not a hover: it stays open to be read, it works from a
 * keyboard, and Escape or a click elsewhere closes it.
 */
export function Popover({
  label,
  trigger,
  side = "top",
  align = "start",
  triggerClassName = "",
  children,
}: {
  /** Accessible name of the opened panel. */
  label: string;
  trigger: ReactNode;
  side?: "top" | "bottom";
  align?: "start" | "end";
  triggerClassName?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [fit, setFit] = useState({ shiftPx: 0, below: false });
  const anchor = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const host = anchor.current;
    const tip = bubble.current;
    if (open && host && tip) setFit(fitInWindow(host, tip, side, align));
  }, [open, side, align]);

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const placed = fit.below ? "bottom" : side;
  const position = align === "end" ? TOOLTIP_SIDE[placed].replace("left-0", "right-0") : TOOLTIP_SIDE[placed];

  return (
    <span ref={anchor} className="relative inline-flex">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((held) => !held)}
        className={`cursor-pointer text-left ${triggerClassName}`}
      >
        {trigger}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            ref={bubble}
            role="dialog"
            aria-label={label}
            initial={{ opacity: 0, y: 3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: DUR.fast, ease: EASE }}
            style={{ marginLeft: fit.shiftPx }}
            className={`absolute z-50 w-80 max-w-[90vw] border border-rule-2 bg-asphalt-2 p-3 text-left whitespace-normal ${position}`}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </span>
  );
}

export function Tooltip({
  content,
  side = "top",
  align = "start",
  block = false,
  children,
}: {
  content: ReactNode;
  side?: TooltipSide;
  align?: "start" | "end";
  block?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [fit, setFit] = useState({ shiftPx: 0, below: false });
  const id = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const host = anchor.current;
    const tip = bubble.current;
    if (open && host && tip) setFit(fitInWindow(host, tip, side, align));
  }, [open, side, align]);

  const placed = fit.below ? "bottom" : side;
  const position = align === "end" ? TOOLTIP_SIDE[placed].replace("left-0", "right-0") : TOOLTIP_SIDE[placed];

  return (
    <span
      ref={anchor}
      className={block ? "relative flex w-full" : "relative inline-flex"}
      aria-describedby={open ? id : undefined}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      <AnimatePresence>
        {open && (
          <motion.span
            ref={bubble}
            id={id}
            role="tooltip"
            initial={{ opacity: 0, y: 3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: DUR.fast, ease: EASE }}
            style={{ marginLeft: fit.shiftPx }}
            className={`mono pointer-events-none absolute z-50 w-max max-w-72 border border-rule-2 bg-asphalt-2 px-3 py-2 whitespace-normal text-paint-1 ${position}`}
          >
            {content}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  );
}
