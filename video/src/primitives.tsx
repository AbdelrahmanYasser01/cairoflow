import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, Easing, interpolate, OffthreadVideo, staticFile, useCurrentFrame } from "remotion";
import type { Status } from "./facts";
import { C, F } from "./theme";

export const ease = Easing.bezier(0.2, 0.8, 0.2, 1);
export const easeInOut = Easing.bezier(0.65, 0, 0.35, 1);

/** 0..1 progress between two local frames, eased. */
export function prog(frame: number, start: number, length: number, easing = ease) {
  return interpolate(frame, [start, start + length], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });
}

/** Fades the whole scene in and out over its first and last `edge` frames. */
export function SceneFade({ dur, edge = 12, children }: { dur: number; edge?: number; children: ReactNode }) {
  const frame = useCurrentFrame();
  const opacity = Math.min(prog(frame, 0, edge, Easing.linear), 1 - prog(frame, dur - edge, edge, Easing.linear));
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
}

/** Text that rises and sharpens in at `at`. */
export function Rise({
  at,
  len = 16,
  y = 18,
  out,
  style,
  children,
}: {
  at: number;
  len?: number;
  y?: number;
  out?: number;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const frame = useCurrentFrame();
  const p = prog(frame, at, len);
  const q = out === undefined ? 0 : prog(frame, out, 12, Easing.linear);
  return (
    <div
      style={{
        opacity: p * (1 - q),
        transform: `translateY(${(1 - p) * y}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Typewriter: reveals `text` one character every `every` frames from `at`, with a block caret. */
export function TypeOn({
  text,
  at,
  every = 4,
  caret = true,
  style,
}: {
  text: string;
  at: number;
  every?: number;
  caret?: boolean;
  style?: CSSProperties;
}) {
  const frame = useCurrentFrame();
  const shown = Math.max(0, Math.min(text.length, Math.floor((frame - at) / every) + 1));
  const done = shown >= text.length;
  const blink = Math.floor(frame / 15) % 2 === 0;
  return (
    <span style={{ whiteSpace: "pre", ...style }}>
      {frame >= at ? text.slice(0, shown) : ""}
      {caret && frame >= at - 10 && (!done || blink) ? (
        <span style={{ display: "inline-block", width: "0.55em", height: "0.9em", marginLeft: "0.06em", background: C.amber, verticalAlign: "-0.08em" }} />
      ) : null}
    </span>
  );
}

/** Counts up to a real figure. `decimals` matches how the source writes it. */
export function Counter({
  to,
  at,
  len = 40,
  decimals = 0,
  prefix = "",
  suffix = "",
  thousands = true,
  style,
}: {
  to: number;
  at: number;
  len?: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  thousands?: boolean;
  style?: CSSProperties;
}) {
  const frame = useCurrentFrame();
  const p = prog(frame, at, len, Easing.out(Easing.cubic));
  const value = to * p;
  const text = p >= 1 ? to.toFixed(decimals) : value.toFixed(decimals);
  const pretty = thousands ? Number(text).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : text;
  return (
    <span style={{ fontVariantNumeric: "tabular-nums", ...style }}>
      {prefix}
      {pretty}
      {suffix}
    </span>
  );
}

export const Kicker = ({ children, color = C.amber, style }: { children: ReactNode; color?: string; style?: CSSProperties }) => (
  <div
    style={{
      fontFamily: F.mono,
      fontSize: 22,
      letterSpacing: "0.18em",
      textTransform: "uppercase",
      color,
      ...style,
    }}
  >
    {children}
  </div>
);

const STATUS: Record<Status, { label: string; color: string }> = {
  checked: { label: "Checked", color: C.go },
  approximated: { label: "Approximated", color: C.approximated },
  assumed: { label: "Assumed", color: C.assumed },
  ungated: { label: "Ungated", color: C.paint2 },
  simulated: { label: "Simulated", color: C.simulated },
  model: { label: "Model output", color: C.model },
  withheld: { label: "Withheld", color: C.stop },
};

/** The app's evidence grammar as a glyph: filled, half, dashed, diamond, grid. */
export function Glyph({ status, size = 18 }: { status: Status; size?: number }) {
  const { color } = STATUS[status];
  const r = size / 2 - 1.5;
  const c = size / 2;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flex: "none" }}>
      {status === "approximated" ? (
        <>
          <circle cx={c} cy={c} r={r} fill="none" stroke={color} strokeWidth={2} />
          <path d={`M ${c} ${c - r} A ${r} ${r} 0 0 0 ${c} ${c + r} Z`} fill={color} />
        </>
      ) : status === "assumed" || status === "ungated" ? (
        <circle cx={c} cy={c} r={r} fill="none" stroke={color} strokeWidth={2} strokeDasharray="3 3" />
      ) : status === "model" ? (
        <path d={`M ${c} 1.5 L ${size - 1.5} ${c} L ${c} ${size - 1.5} L 1.5 ${c} Z`} fill="none" stroke={color} strokeWidth={2} />
      ) : status === "simulated" ? (
        <g fill={color}>
          <rect x={2} y={2} width={6} height={6} />
          <rect x={10} y={2} width={6} height={6} />
          <rect x={2} y={10} width={6} height={6} />
          <rect x={10} y={10} width={6} height={6} />
        </g>
      ) : status === "withheld" ? (
        <g stroke={color} strokeWidth={2.2}>
          <circle cx={c} cy={c} r={r} fill="none" />
          <line x1={c - r * 0.7} y1={c + r * 0.7} x2={c + r * 0.7} y2={c - r * 0.7} />
        </g>
      ) : (
        <circle cx={c} cy={c} r={r} fill={color} />
      )}
    </svg>
  );
}

export function StatusMark({ status, style, size = 22 }: { status: Status; style?: CSSProperties; size?: number }) {
  const { label, color } = STATUS[status];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 10, fontFamily: F.mono, fontSize: size, color, ...style }}>
      <Glyph status={status} size={size - 2} />
      {label}
    </span>
  );
}

/** Near-black room with a faint survey grid and a vignette, so flat scenes still have depth. */
export function Backdrop({ grid = true, glow = 0.12 }: { grid?: boolean; glow?: number }) {
  const frame = useCurrentFrame();
  const drift = (frame * 0.15) % 80;
  return (
    <AbsoluteFill style={{ background: C.bg }}>
      {grid ? (
        <AbsoluteFill
          style={{
            backgroundImage: `linear-gradient(${C.rule1}40 1px, transparent 1px), linear-gradient(90deg, ${C.rule1}40 1px, transparent 1px)`,
            backgroundSize: "80px 80px",
            backgroundPosition: `${drift}px ${drift * 0.5}px`,
          }}
        />
      ) : null}
      {/* One gradient for the glow and the vignette together: cheap to rasterise in software. */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse 75% 70% at 50% 45%, rgba(245,166,35,${glow * 0.06}) 0%, rgba(10,12,14,0) 45%, rgba(0,0,0,0.7) 100%)`,
        }}
      />
    </AbsoluteFill>
  );
}

export type Clip = { src: string; durationInFrames: number };

/**
 * A real capture of the app inside a hairline frame, with a slow push-in. With no capture it
 * shows a labelled empty frame, so the edit still renders before the first capture.
 */
export function Screen({
  clip,
  label,
  x = 0,
  y = 0,
  w = 1920,
  h = 1080,
  zoomFrom = 1.0,
  zoomTo = 1.06,
  originX = 50,
  originY = 50,
  dur,
  startAt = 0,
  frameless = false,
}: {
  clip?: Clip;
  label: string;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  zoomFrom?: number;
  zoomTo?: number;
  originX?: number;
  originY?: number;
  dur: number;
  startAt?: number;
  frameless?: boolean;
}) {
  const frame = useCurrentFrame();
  const z = interpolate(frame, [0, dur], [zoomFrom, zoomTo], { extrapolateRight: "clamp", easing: easeInOut });
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        overflow: "hidden",
        background: C.s1,
        border: frameless ? undefined : `1px solid ${C.rule2}`,
        boxShadow: frameless ? undefined : "0 40px 120px rgba(0,0,0,0.6)",
      }}
    >
      {clip ? (
        <div style={{ position: "absolute", inset: 0, transform: `scale(${z})`, transformOrigin: `${originX}% ${originY}%` }}>
          <OffthreadVideo
            src={staticFile(clip.src)}
            startFrom={startAt}
            muted
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        </div>
      ) : (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", fontFamily: F.mono, color: C.paint3, fontSize: 26 }}>
          capture pending: {label}
        </AbsoluteFill>
      )}
    </div>
  );
}

/** Lower-left tag naming what is on screen: source, then status. */
export function SourceTag({ children, at = 6, style }: { children: ReactNode; at?: number; style?: CSSProperties }) {
  return (
    <Rise at={at} style={{ position: "absolute", left: 72, top: 64, display: "flex", gap: 18, alignItems: "center", ...style }}>
      <span style={{ width: 10, height: 10, background: C.amber, display: "inline-block" }} />
      <span style={{ fontFamily: F.mono, fontSize: 22, letterSpacing: "0.14em", textTransform: "uppercase", color: C.paint1 }}>{children}</span>
    </Rise>
  );
}

/** The Higgsfield slot: a generated shot when one is dropped in, otherwise a labelled card. */
export function Slot({
  id,
  src,
  dur,
  label,
  showLabel,
}: {
  id: string;
  src?: string;
  dur: number;
  label: string;
  showLabel: boolean;
}) {
  const frame = useCurrentFrame();
  const z = interpolate(frame, [0, dur], [1.04, 1.12]);
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: C.bg }}>
      {src ? (
        <AbsoluteFill style={{ transform: `scale(${z})` }}>
          <OffthreadVideo src={staticFile(src)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        </AbsoluteFill>
      ) : (
        <SlotPlaceholder frame={frame} />
      )}
      <AbsoluteFill style={{ background: "linear-gradient(90deg, rgba(10,12,14,0.92) 0%, rgba(10,12,14,0.55) 45%, rgba(10,12,14,0.15) 100%)" }} />
      {/* Always on screen, with or without the generated shot: it is not footage of the site. */}
      <div style={{ position: "absolute", right: 64, top: 56, fontFamily: F.mono, fontSize: 18, letterSpacing: "0.16em", color: C.paint3, textTransform: "uppercase", textAlign: "right", lineHeight: 1.6 }}>
        {showLabel ? `Higgsfield slot ${id} · ` : ""}Illustrative, not data
        {showLabel && !src ? <div style={{ color: C.paint4 }}>{label}</div> : null}
      </div>
    </AbsoluteFill>
  );
}

/** Abstract dusk-street placeholder: light trails on a dark plane. Deliberately not a map. */
function SlotPlaceholder({ frame }: { frame: number }) {
  const trails = Array.from({ length: 26 }, (_, i) => i);
  return (
    <AbsoluteFill style={{ background: `radial-gradient(ellipse 80% 60% at 70% 110%, #2a1d0c 0%, ${C.bg} 60%)` }}>
      <svg width="100%" height="100%" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="trail" x1="0" x2="1">
            <stop offset="0" stopColor={C.amber} stopOpacity="0" />
            <stop offset="1" stopColor={C.amber} stopOpacity="0.9" />
          </linearGradient>
          <linearGradient id="trailW" x1="1" x2="0">
            <stop offset="0" stopColor={C.paint1} stopOpacity="0" />
            <stop offset="1" stopColor={C.paint1} stopOpacity="0.8" />
          </linearGradient>
        </defs>
        {/* Perspective lane lines toward a vanishing point. */}
        {Array.from({ length: 9 }, (_, i) => (
          <line key={i} x1={1260} y1={420} x2={-400 + i * 420} y2={1080} stroke={C.rule2} strokeWidth={1.2} opacity={0.55} />
        ))}
        {trails.map((i) => {
          const lane = i % 8;
          const speed = 0.004 + ((i * 37) % 11) * 0.0007;
          const t = ((frame * speed + i * 0.137) % 1 + 1) % 1;
          const toward = i % 2 === 0;
          const k = toward ? t : 1 - t;
          const x2 = -400 + lane * 420 + 210;
          const px = 1260 + (x2 - 1260) * k * k;
          const py = 420 + (1080 - 420) * k * k;
          const len = 30 + k * 260;
          const ang = Math.atan2(1080 - 420, x2 - 1260);
          return (
            <line
              key={i}
              x1={px - Math.cos(ang) * len * (toward ? 1 : -1)}
              y1={py - Math.sin(ang) * len * (toward ? 1 : -1)}
              x2={px}
              y2={py}
              stroke={toward ? "url(#trail)" : "url(#trailW)"}
              strokeWidth={1 + k * 5}
              strokeLinecap="round"
              opacity={0.25 + k * 0.6}
            />
          );
        })}
      </svg>
    </AbsoluteFill>
  );
}
