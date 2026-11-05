import React, { type CSSProperties, type ReactNode } from "react";
import { Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Video } from "@remotion/media";
import { C, F } from "./theme";
import { SCENES } from "./timeline";

export const EASE = Easing.bezier(0.2, 0.8, 0.2, 1); // the app's --ease-out
export const EASE_IO = Easing.bezier(0.65, 0, 0.35, 1);

const CLAMP = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** Seconds since the start of the enclosing Sequence. */
export const useSec = (): number => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return frame / fps;
};

/** 0 before a, 1 after b, eased between. */
export const ramp = (t: number, a: number, b: number, easing = EASE): number =>
  interpolate(t, [a, b], [0, 1], { ...CLAMP, easing });

/** Visible between a and b with soft edges. */
export const shown = (t: number, a: number, b: number, d = 0.35): number =>
  Math.min(ramp(t, a, a + d), 1 - ramp(t, b - d, b));

/** The product frame: the 1920x1080 UI shown in a ruled frame on the asphalt ground. */
export const FRAME = { x: 160, y: 44, w: 1600, h: 900 };

export type Key = { t: number; x: number; y: number; z: number };

type ShotProps = {
  src: string;
  kind: "still" | "video";
  keys: Key[];
  box?: { x: number; y: number; w: number; h: number };
  children?: ReactNode;
  playbackRate?: number;
  trimBefore?: number;
  opacity?: number;
};

/** A product shot with an eased camera: keys give the focus point (UI pixels) and zoom over time. */
export const Shot: React.FC<ShotProps> = ({ src, kind, keys, box = FRAME, children, playbackRate, trimBefore, opacity = 1 }) => {
  const t = useSec();
  const pick = (f: (k: Key) => number) =>
    keys.length === 1 ? f(keys[0]) : interpolate(t, keys.map((k) => k.t), keys.map(f), { ...CLAMP, easing: EASE_IO });
  const x = pick((k) => k.x);
  const y = pick((k) => k.y);
  const z = pick((k) => k.z);
  const s = (box.w / 1920) * z;
  const tx = Math.min(0, Math.max(box.w - 1920 * s, box.w / 2 - x * s));
  const ty = Math.min(0, Math.max(box.h - 1080 * s, box.h / 2 - y * s));
  return (
    <div
      style={{
        position: "absolute", left: box.x, top: box.y, width: box.w, height: box.h, overflow: "hidden",
        borderRadius: 14, border: `1px solid ${C.rule2}`, background: C.bg, opacity,
        boxShadow: "0 40px 120px rgba(0,0,0,0.6), 0 0 0 1px rgba(0,0,0,0.4)",
      }}
    >
      <div style={{ position: "absolute", left: 0, top: 0, width: 1920, height: 1080, transformOrigin: "0 0", transform: `translate(${tx}px, ${ty}px) scale(${s})` }}>
        {kind === "still" ? (
          <Img src={staticFile(src)} style={{ width: 1920, height: 1080, display: "block" }} />
        ) : (
          <Video src={staticFile(src)} muted playbackRate={playbackRate} trimBefore={trimBefore} style={{ width: 1920, height: 1080, display: "block" }} />
        )}
        {children}
      </div>
    </div>
  );
};

type CalloutProps = {
  x: number; y: number; w: number; h: number; from: number; to?: number;
  color?: string; label?: string; spot?: boolean; pad?: number;
};

/** A hairline box drawn round part of the UI (UI pixels), optionally dimming the rest. */
export const Callout: React.FC<CalloutProps> = ({ x, y, w, h, from, to = 1e6, color = C.amber, label, spot = false, pad = 8 }) => {
  const t = useSec();
  const a = shown(t, from, to, 0.3);
  const grow = ramp(t, from, from + 0.5);
  return (
    <div
      style={{
        position: "absolute", left: x - pad, top: y - pad, width: w + 2 * pad, height: h + 2 * pad,
        border: `3px solid ${color}`, borderRadius: 6, opacity: a, transform: `scale(${1.05 - 0.05 * grow})`,
        boxShadow: spot ? `0 0 0 4000px rgba(10,12,14,${0.62 * a})` : `0 0 24px ${color}55`,
      }}
    >
      {label ? (
        <div style={{ position: "absolute", left: -3, bottom: "100%", marginBottom: 8, whiteSpace: "nowrap", fontFamily: F.mono, fontSize: 17, fontWeight: 500, letterSpacing: "0.06em", color: C.bg, background: color, padding: "3px 9px", borderRadius: 3 }}>
          {label}
        </div>
      ) : null}
    </div>
  );
};

type ChipProps = {
  x: number; y: number; from: number; to?: number; title: string; sub?: string;
  color?: string; width?: number; style?: CSSProperties;
};

/** A label card in screen space, the app's grammar: mono kicker, text below, a coloured rule. */
export const Chip: React.FC<ChipProps> = ({ x, y, from, to = 1e6, title, sub, color = C.amber, width, style }) => {
  const t = useSec();
  const a = shown(t, from, to, 0.35);
  return (
    <div
      style={{
        position: "absolute", left: x, top: y, width, opacity: a, transform: `translateY(${(1 - ramp(t, from, from + 0.5)) * 14}px)`,
        background: "rgba(17,21,26,0.94)", border: `1px solid ${C.rule2}`,
        padding: "14px 22px 16px", borderRadius: 6, boxShadow: "0 18px 50px rgba(0,0,0,0.5)", ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontFamily: F.mono, fontSize: 19, fontWeight: 500, letterSpacing: "0.09em", textTransform: "uppercase", color }}>
        <span style={{ width: 9, height: 9, background: color, flex: "none" }} />
        {title}
      </div>
      {sub ? <div style={{ fontFamily: F.text, fontSize: 28, color: C.paint1, marginTop: 6, lineHeight: 1.25 }}>{sub}</div> : null}
    </div>
  );
};

/** The app's gate lamp: red, amber, green, one lit. */
export const Lamp: React.FC<{ state: "pass" | "fail" | "ungated" | "off"; size?: number }> = ({ state, size = 14 }) => {
  const lit = state === "fail" ? 0 : state === "ungated" ? 1 : state === "pass" ? 2 : -1;
  const colours = [C.stop, C.amber, C.go];
  return (
    <div style={{ display: "inline-flex", gap: size * 0.45, padding: `${size * 0.32}px ${size * 0.5}px`, border: `1.5px solid ${C.rule2}`, borderRadius: 4, background: C.bg1 }}>
      {colours.map((c, i) => (
        <div key={c} style={{ width: size, height: size, borderRadius: size, background: i === lit ? c : C.paint4, opacity: i === lit ? 1 : 0.4, boxShadow: i === lit ? `0 0 ${size * 1.2}px ${c}` : "none" }} />
      ))}
    </div>
  );
};

/** Small print, for footnotes that keep a claim honest. */
export const Footnote: React.FC<{ from: number; to?: number; children: ReactNode; y?: number }> = ({ from, to = 1e6, children, y = 884 }) => {
  const t = useSec();
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: y, display: "flex", justifyContent: "center", opacity: shown(t, from, to, 0.4) }}>
      <div style={{ maxWidth: 1480, padding: "8px 18px", background: "rgba(10,12,14,0.9)", border: `1px solid ${C.rule1}`, borderRadius: 6, fontFamily: F.mono, fontSize: 18, lineHeight: 1.5, color: C.paint2, textAlign: "center" }}>{children}</div>
    </div>
  );
};

/** Burned-in captions from the voice manifest, on the whole timeline. */
export const Captions: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  for (const slot of SCENES) {
    if (t < slot.start || t >= slot.start + slot.seconds) continue;
    const local = t - slot.start;
    const cap = slot.captions.find((c) => local >= c.start - 0.05 && local < c.end + 0.25);
    if (!cap) return null;
    const a = shown(local, cap.start - 0.05, cap.end + 0.25, 0.12);
    return (
      <div style={{ position: "absolute", left: 0, right: 0, top: 966, display: "flex", justifyContent: "center", opacity: a }}>
        <div style={{ maxWidth: 1560, textAlign: "center", fontFamily: F.text, fontWeight: 500, fontSize: 38, lineHeight: 1.3, color: C.paint1, background: "rgba(10,12,14,0.78)", padding: "10px 26px 12px", borderRadius: 8, textShadow: "0 2px 10px rgba(0,0,0,0.9)" }}>
          {cap.text}
        </div>
      </div>
    );
  }
  return null;
};

/** The asphalt ground with a faint lane grid, so motion graphics sit in the product's world. */
export const Ground: React.FC<{ grid?: number }> = ({ grid = 0.35 }) => (
  <div style={{ position: "absolute", inset: 0, background: `radial-gradient(1200px 700px at 50% 42%, #12171c 0%, ${C.bg} 70%)` }}>
    <div style={{ position: "absolute", inset: 0, opacity: grid, backgroundImage: `linear-gradient(${C.rule1}55 1px, transparent 1px), linear-gradient(90deg, ${C.rule1}55 1px, transparent 1px)`, backgroundSize: "96px 96px" }} />
  </div>
);

export const Kicker: React.FC<{ children: ReactNode; color?: string; style?: CSSProperties }> = ({ children, color = C.amber, style }) => (
  <div style={{ fontFamily: F.mono, fontSize: 22, fontWeight: 500, letterSpacing: "0.14em", textTransform: "uppercase", color, ...style }}>{children}</div>
);
