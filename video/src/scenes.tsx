import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { CHECKS, CREDITS, MISS, MUSE, SAM, SITE, SUMO, TRACKS } from "./facts";
import samMasks from "./generated/sam-masks.json";
import {
  Backdrop,
  type Clip,
  Counter,
  ease,
  easeInOut,
  Glyph,
  Kicker,
  prog,
  Rise,
  SceneFade,
  Screen,
  Slot,
  SourceTag,
  StatusMark,
  TypeOn,
} from "./primitives";
import { C, F } from "./theme";

export type SceneProps = {
  dur: number;
  captures: Partial<Record<"twin" | "command" | "evidence" | "analytics" | "vision", Clip>>;
  slots: Partial<Record<"h1" | "h2", string>>;
  showSlotLabels: boolean;
};

const M = 96; // outer margin

/* 1. Cold open: the number we refused to show. */
export function Cold({ dur }: SceneProps) {
  const frame = useCurrentFrame();
  const slide = prog(frame, 118, 46, easeInOut);
  const wipe = prog(frame, 226, 22, easeInOut);
  const stamp = prog(frame, 244, 14);
  // Gauge spans 0.195 to 0.205 so the 0.0033 miss is visible: 0.20 sits at 50 %.
  const gaugeW = 900;
  const at = (v: number) => ((v - 0.195) / 0.01) * gaugeW;
  const marker = interpolate(slide, [0, 1], [at(0.195), at(0.2033)]);
  return (
    <SceneFade dur={dur} edge={10}>
      <AbsoluteFill style={{ background: "#000" }} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
        <div style={{ position: "relative", top: -120 }}>
          <div style={{ fontFamily: F.mono, fontWeight: 600, fontSize: 300, color: C.paint1, letterSpacing: "-0.03em", lineHeight: 1 }}>
            <TypeOn text="0.2033" at={20} every={7} caret={frame < 226} />
          </div>
          <div
            style={{
              position: "absolute",
              left: -30,
              top: 20,
              height: 280,
              width: `calc(${wipe * 100}% + ${wipe * 60}px)`,
              background: C.s3,
              borderLeft: wipe > 0 ? `4px solid ${C.stop}` : undefined,
            }}
          />
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 30,
              opacity: stamp,
              transform: `scale(${1.08 - stamp * 0.08})`,
            }}
          >
            <Glyph status="withheld" size={84} />
            <span style={{ fontFamily: F.mono, fontWeight: 600, fontSize: 120, letterSpacing: "0.2em", color: C.stop }}>WITHHELD</span>
          </div>
        </div>
      </AbsoluteFill>
      <div style={{ position: "absolute", left: (1920 - gaugeW) / 2, top: 640, width: gaugeW, opacity: prog(frame, 100, 16) }}>
        <div style={{ position: "relative", height: 60 }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: 30, height: 2, background: C.rule2 }} />
          {[0.195, 0.2, 0.205].map((v) => (
            <div key={v} style={{ position: "absolute", left: at(v), top: v === 0.2 ? 6 : 22, width: v === 0.2 ? 4 : 2, height: v === 0.2 ? 50 : 18, marginLeft: v === 0.2 ? -2 : -1, background: v === 0.2 ? C.amber : C.rule2 }} />
          ))}
          {[0.195, 0.205].map((v) => (
            <div key={`l${v}`} style={{ position: "absolute", left: at(v) - 60, width: 120, top: 48, textAlign: "center", fontFamily: F.mono, fontSize: 18, color: C.paint4 }}>
              {v.toFixed(3)}
            </div>
          ))}
          <div style={{ position: "absolute", left: marker - 9, top: 21, width: 18, height: 18, borderRadius: 9, background: slide > 0.98 ? C.stop : C.paint1, boxShadow: `0 0 24px ${slide > 0.98 ? C.stop : C.paint1}` }} />
          <div
            style={{
              position: "absolute",
              left: at(0.2),
              width: at(0.2033) - at(0.2),
              top: -8,
              height: 10,
              borderTop: `2px solid ${C.stop}`,
              borderLeft: `2px solid ${C.stop}`,
              borderRight: `2px solid ${C.stop}`,
              opacity: prog(frame, 168, 10),
            }}
          />
          <div style={{ position: "absolute", left: at(0.2) + 6, top: -52, fontFamily: F.mono, fontSize: 26, color: C.stop, opacity: prog(frame, 170, 10) }}>+{MISS}</div>
        </div>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 26 }}>
          <span style={{ fontFamily: F.mono, fontSize: 52, color: C.paint3 }}>
            limit <span style={{ color: C.amber }}>0.20</span>
          </span>
        </div>
      </div>
      <Rise at={196} style={{ position: "absolute", left: 0, right: 0, top: 860, textAlign: "center", fontFamily: F.text, fontWeight: 500, fontSize: 64, color: C.paint1 }}>
        So we didn't show it.
      </Rise>
    </SceneFade>
  );
}

/* 2. Title card. */
export function Title({ dur }: SceneProps) {
  const frame = useCurrentFrame();
  const word = "CairoFlow";
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.5} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <div style={{ display: "flex", fontFamily: F.display, fontWeight: 700, fontSize: 230, color: C.paint1, letterSpacing: "-0.01em", lineHeight: 1 }}>
          {word.split("").map((ch, i) => {
            const p = prog(frame, 6 + i * 2.2, 18);
            return (
              <span key={i} style={{ opacity: p, transform: `translateY(${(1 - p) * 40}px)`, display: "inline-block" }}>
                {ch}
              </span>
            );
          })}
        </div>
        <div style={{ width: 560 * prog(frame, 26, 30), height: 3, background: C.amber, margin: "34px 0 38px" }} />
        <Rise at={40} style={{ fontFamily: F.text, fontWeight: 500, fontSize: 54, color: C.paint2 }}>
          Traffic intelligence that shows its working.
        </Rise>
      </AbsoluteFill>
    </SceneFade>
  );
}

/* 3. Place: Higgsfield slot H1 under the dateline. */
export function Place({ dur, slots, showSlotLabels }: SceneProps) {
  return (
    <SceneFade dur={dur}>
      <Slot id="H1" src={slots.h1} dur={dur} label="Aerial, intersection at dusk" showLabel={showSlotLabels} />
      <div style={{ position: "absolute", left: M, top: 300 }}>
        <Rise at={8}>
          <Kicker>One camera · {SITE.source}</Kicker>
        </Rise>
        <Rise at={22} style={{ fontFamily: F.display, fontWeight: 700, fontSize: 128, color: C.paint1, lineHeight: 1, marginTop: 30 }}>
          Lankershim Blvd
        </Rise>
        <Rise at={34} style={{ fontFamily: F.display, fontWeight: 600, fontSize: 72, color: C.paint2, lineHeight: 1.1, marginTop: 10 }}>
          at Universal Hollywood Dr
        </Rise>
        <Rise at={58} style={{ fontFamily: F.mono, fontSize: 30, color: C.paint2, marginTop: 44, display: "flex", gap: 28 }}>
          <span>{SITE.city}</span>
          <span style={{ color: C.paint4 }}>/</span>
          <span>{SITE.date}</span>
          <span style={{ color: C.paint4 }}>/</span>
          <span>overhead video</span>
        </Rise>
      </div>
    </SceneFade>
  );
}

/* 4. Camera lane: detector and tracker, on the real Vision Lab. */
export function Tracks({ dur, captures }: SceneProps) {
  const frame = useCurrentFrame();
  const chain = ["Video", "Detector", "Tracker", "Trajectories", "Placement"];
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.2} />
      <Screen clip={captures.vision} label="Vision Lab" x={M} y={170} w={1120} h={630} dur={dur} zoomFrom={1.0} zoomTo={1.1} originX={40} originY={45} />
      <SourceTag at={4}>Vision Lab · camera output</SourceTag>
      <div style={{ position: "absolute", left: 1300, top: 170, width: 540 }}>
        <Kicker color={C.paint3}>Camera lane</Kicker>
        <div style={{ marginTop: 26 }}>
          {chain.map((step, i) => {
            const p = prog(frame, 14 + i * 12, 14);
            const lit = frame > 14 + i * 12 + 6;
            return (
              <div key={step} style={{ display: "flex", alignItems: "center", gap: 18, height: 50, opacity: 0.25 + p * 0.75 }}>
                <span style={{ width: 14, height: 14, border: `2px solid ${lit ? C.amber : C.paint4}`, background: lit ? C.amber : "transparent", transform: "rotate(45deg)" }} />
                <span style={{ fontFamily: F.mono, fontSize: 30, color: lit ? C.paint1 : C.paint3 }}>{step}</span>
              </div>
            );
          })}
        </div>
        <div style={{ height: 1, background: C.rule2, margin: "34px 0 26px", width: 520 * prog(frame, 80, 20) }} />
        <Rise at={110} style={{ fontFamily: F.display, fontWeight: 700, fontSize: 200, color: C.paint1, lineHeight: 0.9 }}>
          <Counter to={TRACKS.value} at={110} len={50} />
        </Rise>
        <Rise at={120} style={{ fontFamily: F.mono, fontSize: 30, color: C.paint2, marginTop: 10 }}>
          tracks · {SITE.study}
        </Rise>
        <Rise at={170} style={{ display: "flex", gap: 28, marginTop: 26 }}>
          <StatusMark status="approximated" />
          <StatusMark status="ungated" />
        </Rise>
      </div>
    </SceneFade>
  );
}

type MaskFrame = { frame: number; masks: [number, [number, number][]][] };
const MASK_FRAMES = (samMasks as { frames: MaskFrame[] }).frames;
const [, , MAX_X, MAX_Y] = (samMasks as { bounds: number[] }).bounds;

/* 5. SAM 3.1: real mask outlines from the SAM window, and what it changed. */
export function Sam({ dur }: SceneProps) {
  const frame = useCurrentFrame();
  const index = Math.floor(frame / 3) % MASK_FRAMES.length;
  const shown = MASK_FRAMES[index];
  const vw = Math.ceil(MAX_X);
  const vh = Math.ceil(MAX_Y);
  const boxW = 1060;
  const boxH = (boxW * vh) / vw;
  const draw = prog(frame, 6, 30);
  const rows: { at: number; big: string; small: string; tone?: string; count?: number }[] = [
    { at: 60, big: String(SAM.replaced), small: "interpolated rows replaced", count: SAM.replaced },
    { at: 120, big: `${SAM.interpolatedBefore} → ${SAM.interpolatedAfter}`, small: "interpolated share" },
    { at: 170, big: `${SAM.acrossBefore} → ${SAM.acrossAfter}`, small: "across-road error, median" },
    { at: 220, big: `${SAM.alongBefore} → ${SAM.alongAfter}`, small: "along-road error: no gain", tone: C.paint3 },
  ];
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.15} />
      <SourceTag at={2}>SAM 3.1 · Meta</SourceTag>
      <div style={{ position: "absolute", left: M, top: 180, width: boxW, height: boxH, border: `1px solid ${C.rule2}`, background: C.s1, overflow: "hidden" }}>
        <svg width={boxW} height={boxH} viewBox={`0 0 ${vw} ${vh}`}>
          {Array.from({ length: 9 }, (_, i) => (
            <line key={`g${i}`} x1={(vw / 8) * i} y1={0} x2={(vw / 8) * i} y2={vh} stroke={C.rule1} strokeWidth={0.6} />
          ))}
          {shown.masks.map(([track, poly]) => (
            <polygon
              key={track}
              points={poly.map(([x, y]) => `${x},${y}`).join(" ")}
              fill={C.model}
              fillOpacity={0.22 * draw}
              stroke={C.model}
              strokeWidth={1.4}
              strokeOpacity={draw}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        <div style={{ position: "absolute", left: 20, bottom: 16, display: "flex", gap: 24, alignItems: "center" }}>
          <StatusMark status="model" size={20} />
          <span style={{ fontFamily: F.mono, fontSize: 20, color: C.paint3 }}>
            masks, frame {shown.frame} · {SAM.window} window
          </span>
        </div>
      </div>
      <div style={{ position: "absolute", left: 1250, top: 180, width: 600 }}>
        {rows.map((row) => (
          <Rise key={row.small} at={row.at} style={{ marginBottom: 40 }}>
            <div style={{ fontFamily: F.display, fontWeight: 700, fontSize: row.count ? 120 : 76, color: row.tone ?? C.paint1, lineHeight: 1 }}>
              {row.count ? <Counter to={row.count} at={row.at} len={36} /> : row.big}
            </div>
            <div style={{ fontFamily: F.mono, fontSize: 24, color: C.paint3, marginTop: 8 }}>{row.small}</div>
          </Rise>
        ))}
        <Rise at={262} style={{ display: "flex", gap: 24, alignItems: "center", marginTop: 6 }}>
          <StatusMark status="ungated" size={20} />
          <span style={{ fontFamily: F.mono, fontSize: 20, color: C.paint3 }}>seeded from detector tracks · {SAM.cost}</span>
        </Rise>
      </div>
    </SceneFade>
  );
}

/* 6. The twin, full bleed. */
export function Twin({ dur, captures }: SceneProps) {
  return (
    <SceneFade dur={dur}>
      <Screen clip={captures.twin} label="Digital Twin" dur={dur} frameless zoomFrom={1.0} zoomTo={1.05} />
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(10,12,14,0.7) 0%, transparent 18%, transparent 78%, rgba(10,12,14,0.85) 100%)" }} />
      <SourceTag at={6}>Digital twin · 4× · one clock</SourceTag>
    </SceneFade>
  );
}

/* 7. Analytics: turning movements, in the app. */
export function Analytics({ dur, captures }: SceneProps) {
  return (
    <SceneFade dur={dur}>
      <Screen clip={captures.analytics} label="Analytics" dur={dur} frameless zoomFrom={1.02} zoomTo={1.1} originX={55} originY={45} />
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(10,12,14,0.7) 0%, transparent 18%, transparent 78%, rgba(10,12,14,0.85) 100%)" }} />
      <SourceTag at={4}>Analytics · turning movements</SourceTag>
    </SceneFade>
  );
}

/* 8. The gate: seven instruments against one line drawn before the read. */
export function Gate({ dur }: SceneProps) {
  const frame = useCurrentFrame();
  const top = 330;
  const rowH = 88;
  const trackX = 600;
  const trackW = 860;
  const lineX = trackX + trackW * 0.72;
  const lineDraw = prog(frame, 40, 36, easeInOut);
  const fails = CHECKS.findIndex((c) => !c.pass);
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.1} />
      <div style={{ position: "absolute", left: M, top: 84 }}>
        <Rise at={0}>
          <Kicker>Agreement gate · held-out</Kicker>
        </Rise>
        <Rise at={8} style={{ fontFamily: F.display, fontWeight: 700, fontSize: 84, color: C.paint1, marginTop: 18, lineHeight: 1 }}>
          Seven checks. Limits first.
        </Rise>
        <Rise at={22} style={{ fontFamily: F.mono, fontSize: 24, color: C.paint3, marginTop: 18 }}>
          vs published NGSIM trajectories · 7 held-out blocks · one read
        </Rise>
      </div>
      {/* The committed line. */}
      <div style={{ position: "absolute", left: lineX - 1.5, top: top - 30, width: 3, height: (CHECKS.length * rowH + 20) * lineDraw, background: C.amber, boxShadow: `0 0 18px ${C.amber}88` }} />
      <div style={{ position: "absolute", left: lineX - 120, top: top - 70, width: 240, textAlign: "center", fontFamily: F.mono, fontSize: 18, letterSpacing: "0.14em", color: C.amber, opacity: prog(frame, 60, 14) }}>
        LIMIT
      </div>
      {CHECKS.map((check, i) => {
        const y = top + i * rowH;
        const start = 110 + i * 34;
        const isFail = !check.pass;
        const p = prog(frame, start, isFail ? 64 : 34, isFail ? Easing.inOut(Easing.cubic) : ease);
        const ratio = check.value / check.limit;
        const end = trackW * 0.72 * ratio;
        const done = frame > start + (isFail ? 64 : 34);
        const lampOn = prog(frame, start + (isFail ? 66 : 36), 8);
        const color = done ? (check.pass ? C.go : C.stop) : C.paint2;
        const passLeft = check.sense === "atMost";
        return (
          <div key={check.label} style={{ position: "absolute", left: 0, top: y, width: 1920, height: rowH, opacity: prog(frame, 80 + i * 4, 14) }}>
            <div style={{ position: "absolute", left: M, top: 18, fontFamily: F.text, fontWeight: 500, fontSize: 32, color: isFail && done ? C.stop : C.paint1 }}>{check.label}</div>
            <div
              style={{
                position: "absolute",
                left: passLeft ? trackX : lineX,
                width: passLeft ? lineX - trackX : trackX + trackW - lineX,
                top: 22,
                height: 34,
                background: `${C.go}10`,
              }}
            />
            <div style={{ position: "absolute", left: trackX, top: 38, width: trackW, height: 2, background: C.rule1 }} />
            <div style={{ position: "absolute", left: trackX, top: 30, width: end * p, height: 18, background: color, boxShadow: done ? `0 0 20px ${color}66` : undefined }} />
            <div style={{ position: "absolute", left: 1500, top: 8, fontFamily: F.mono, fontWeight: 600, fontSize: 40, color: done ? color : C.paint1, fontVariantNumeric: "tabular-nums" }}>
              {p > 0 ? (done ? check.shown : (check.value * p).toFixed(check.shown.split(" ")[0].split(".")[1]?.length ?? 0)) : ""}
            </div>
            <div style={{ position: "absolute", left: 1500, top: 58, fontFamily: F.mono, fontSize: 20, color: C.paint3 }}>{check.limitShown}</div>
            <div
              style={{
                position: "absolute",
                left: 1730,
                top: 16,
                width: 100,
                height: 44,
                border: `2px solid ${check.pass ? C.go : C.stop}`,
                background: lampOn > 0.5 ? (check.pass ? `${C.go}22` : `${C.stop}22`) : "transparent",
                opacity: lampOn,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: F.mono,
                fontWeight: 600,
                fontSize: 22,
                letterSpacing: "0.12em",
                color: check.pass ? C.go : C.stop,
              }}
            >
              {check.pass ? "PASS" : "FAIL"}
            </div>
            {isFail ? (
              <div style={{ position: "absolute", left: lineX + 30, top: -34, fontFamily: F.mono, fontSize: 24, color: C.stop, opacity: prog(frame, start + 76, 12) }}>
                over by {MISS}
              </div>
            ) : null}
          </div>
        );
      })}
      {fails >= 0 ? (
        <div
          style={{
            position: "absolute",
            left: M - 24,
            top: top + fails * rowH - 6,
            width: 1920 - 2 * M + 48,
            height: rowH,
            border: `1px solid ${C.stop}`,
            opacity: prog(frame, 110 + fails * 34 + 70, 10),
          }}
        />
      ) : null}
      <Rise at={470} style={{ position: "absolute", left: M, top: 980 - 70, fontFamily: F.display, fontWeight: 600, fontSize: 54, color: C.paint1 }}>
        Six pass. One misses by <span style={{ color: C.stop }}>{MISS}</span>.
      </Rise>
    </SceneFade>
  );
}

/* 9. The product withholds it. */
export function Withheld({ dur, captures }: SceneProps) {
  const frame = useCurrentFrame();
  const card = prog(frame, 30, 20);
  return (
    <SceneFade dur={dur}>
      <Screen clip={captures.command} label="Command center" dur={dur} frameless zoomFrom={1.0} zoomTo={1.06} originX={30} originY={40} />
      <AbsoluteFill style={{ background: "linear-gradient(90deg, transparent 40%, rgba(10,12,14,0.9) 75%)" }} />
      <SourceTag at={4}>Command center</SourceTag>
      <div
        style={{
          position: "absolute",
          right: M,
          top: 270,
          width: 660,
          padding: "44px 48px",
          background: "rgba(17,21,26,0.92)",
          borderLeft: `4px solid ${C.stop}`,
          opacity: card,
          transform: `translateX(${(1 - card) * 40}px)`,
        }}
      >
        <div style={{ fontFamily: F.mono, fontSize: 22, letterSpacing: "0.14em", color: C.paint3, textTransform: "uppercase" }}>Entering flow</div>
        <div style={{ display: "flex", alignItems: "center", gap: 22, marginTop: 18 }}>
          <Glyph status="withheld" size={56} />
          <span style={{ fontFamily: F.mono, fontWeight: 600, fontSize: 84, color: C.stop, letterSpacing: "0.04em" }}>Withheld</span>
        </div>
        <Rise at={62} style={{ fontFamily: F.mono, fontSize: 28, color: C.paint2, marginTop: 26 }}>
          gate failed: 0.2033 &gt; 0.20
        </Rise>
        <Rise at={90} style={{ fontFamily: F.text, fontWeight: 500, fontSize: 34, color: C.paint1, marginTop: 18 }}>
          No value. The reason, in its place.
        </Rise>
      </div>
    </SceneFade>
  );
}

/* 10. Evidence: every figure carries its status. */
export function Evidence({ dur, captures }: SceneProps) {
  const statuses = ["checked", "approximated", "assumed", "ungated", "simulated", "model"] as const;
  return (
    <SceneFade dur={dur}>
      <Screen clip={captures.evidence} label="Evidence" dur={dur} frameless zoomFrom={1.0} zoomTo={1.04} />
      <AbsoluteFill style={{ background: "linear-gradient(180deg, rgba(10,12,14,0.7) 0%, transparent 18%, transparent 70%, rgba(10,12,14,0.92) 100%)" }} />
      <SourceTag at={4}>Evidence · every figure</SourceTag>
      <div style={{ position: "absolute", left: M, bottom: 200, display: "flex", gap: 44 }}>
        {statuses.map((s, i) => (
          <Rise key={s} at={20 + i * 6}>
            <StatusMark status={s} size={26} />
          </Rise>
        ))}
      </div>
    </SceneFade>
  );
}

/* 11. Muse Spark hypotheses; every citation snaps to an evidence id the code checked. */
export function Muse({ dur }: SceneProps) {
  const frame = useCurrentFrame();
  const listX = 1250;
  const listY = 196;
  const rowH = 30;
  const chipY = 700;
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.12} />
      <SourceTag at={2}>Muse Spark · Meta</SourceTag>
      <div style={{ position: "absolute", left: M, top: 196, width: 1040 }}>
        <Rise at={6} style={{ display: "flex", gap: 22, alignItems: "center" }}>
          <StatusMark status="model" size={24} />
          <span style={{ fontFamily: F.mono, fontSize: 22, color: C.paint3 }}>
            {MUSE.model} · {MUSE.hypothesisId} · confidence {MUSE.confidence}
          </span>
        </Rise>
        <div style={{ marginTop: 34, borderLeft: `3px solid ${C.model}`, paddingLeft: 34, fontFamily: F.text, fontWeight: 500, fontSize: 44, lineHeight: 1.32, color: C.paint1, minHeight: 360 }}>
          <TypeOn text={MUSE.statement} at={18} every={0.9} caret={frame < 200} />
        </div>
      </div>
      <div style={{ position: "absolute", left: M, top: chipY - 50, fontFamily: F.mono, fontSize: 20, letterSpacing: "0.14em", color: C.paint3, opacity: prog(frame, 170, 12) }}>
        CITES
      </div>
      {MUSE.supporting.map((id, i) => {
        const appear = prog(frame, 176 + i * 8, 12);
        const fly = prog(frame, 236 + i * 20, 22, easeInOut);
        const target = MUSE.evidence.indexOf(id);
        const x0 = M + i * 262;
        const y0 = chipY;
        const x1 = listX - 14;
        const y1 = listY + 60 + target * rowH - 6;
        const x = x0 + (x1 - x0) * fly;
        const y = y0 + (y1 - y0) * fly;
        return (
          <div
            key={id}
            style={{
              position: "absolute",
              left: x,
              top: y,
              padding: "4px 12px",
              border: `1.5px solid ${C.model}`,
              background: "rgba(17,21,26,0.95)",
              fontFamily: F.mono,
              fontSize: fly > 0 ? 20 - fly * 2 : 20,
              color: C.model,
              opacity: appear * (1 - prog(frame, 236 + i * 20 + 24, 8)),
              whiteSpace: "nowrap",
            }}
          >
            {id}
          </div>
        );
      })}
      <div style={{ position: "absolute", left: listX, top: listY, width: 600 }}>
        <Rise at={10} style={{ fontFamily: F.mono, fontSize: 20, letterSpacing: "0.14em", color: C.paint3, marginBottom: 20 }}>
          EVIDENCE IDS · {MUSE.evidence.length} · FROM THE RECORD
        </Rise>
        {MUSE.evidence.map((id, i) => {
          const hitIndex = MUSE.supporting.indexOf(id);
          const hit = hitIndex >= 0 ? prog(frame, 236 + hitIndex * 20 + 22, 6) : 0;
          return (
            <div key={id} style={{ height: rowH, display: "flex", alignItems: "center", gap: 14, fontFamily: F.mono, fontSize: 19, color: hit > 0.5 ? C.paint1 : C.paint4, opacity: prog(frame, 14 + i * 1.5, 10) }}>
              <span style={{ width: 18, color: C.go, opacity: hit }}>✓</span>
              <span style={{ background: hit > 0.5 ? `${C.go}18` : "transparent", padding: "0 8px" }}>{id}</span>
            </div>
          );
        })}
      </div>
      <Rise at={330} style={{ position: "absolute", left: M, top: 850, display: "flex", gap: 56, fontFamily: F.mono, fontSize: 30, color: C.paint2 }}>
        <span>
          <span style={{ color: C.paint1 }}>{MUSE.replicates}</span> runs
        </span>
        <span>
          <span style={{ color: C.go }}>{MUSE.citationProblems}</span> citation problems
        </span>
        <span>
          <span style={{ color: C.go }}>{MUSE.unsupportedNumbers}</span> unsupported numbers
        </span>
      </Rise>
    </SceneFade>
  );
}

/* 12. SUMO: 3 candidates x 20 seeds x 4 timelines; every one worsens something. */
export function Sumo({ dur }: SceneProps) {
  const frame = useCurrentFrame();
  const cellW = 13;
  const gap = 4;
  const groupGap = 22;
  const x0 = 330;
  const rowTop = 330;
  const rowStep = 150;
  const cellH = 46;
  const colX = (c: number) => x0 + c * (cellW + gap) + Math.floor(c / SUMO.seeds) * groupGap;
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.1} />
      <SourceTag at={2}>SUMO · decision lane</SourceTag>
      <div style={{ position: "absolute", left: M, top: 140 }}>
        <Rise at={4} style={{ fontFamily: F.display, fontWeight: 700, fontSize: 80, color: C.paint1, lineHeight: 1 }}>
          Muse proposes. SUMO tests.
        </Rise>
        <Rise at={18} style={{ display: "flex", gap: 30, alignItems: "center", marginTop: 20 }}>
          <StatusMark status="model" size={22} />
          <span style={{ fontFamily: F.mono, fontSize: 22, color: C.paint3 }}>3 proposals</span>
          <span style={{ color: C.paint4 }}>→</span>
          <StatusMark status="simulated" size={22} />
          <span style={{ fontFamily: F.mono, fontSize: 22, color: C.paint3 }}>
            {SUMO.seeds} seeds × {SUMO.timelines} timelines each
          </span>
        </Rise>
      </div>
      {Array.from({ length: SUMO.timelines }, (_, g) => (
        <div key={g} style={{ position: "absolute", left: colX(g * SUMO.seeds), top: rowTop - 44, fontFamily: F.mono, fontSize: 18, color: C.paint3, opacity: prog(frame, 40, 12) }}>
          timeline {g + 1}
        </div>
      ))}
      {SUMO.candidates.map((cand, r) => {
        const y = rowTop + r * rowStep;
        const verdict = prog(frame, 300 + r * 22, 14);
        return (
          <div key={cand.id}>
            <div style={{ position: "absolute", left: M, top: y + 4, fontFamily: F.mono, fontWeight: 600, fontSize: 34, color: verdict > 0.5 ? C.stop : C.paint1 }}>{cand.id}</div>
            {Array.from({ length: SUMO.seeds * SUMO.timelines }, (_, c) => {
              const on = prog(frame, 56 + c * 2.1 + r * 10, 8);
              return (
                <div
                  key={c}
                  style={{
                    position: "absolute",
                    left: colX(c),
                    top: y,
                    width: cellW,
                    height: cellH,
                    background: verdict > 0 ? interpolateColor(verdict) : C.simulated,
                    opacity: 0.12 + on * 0.78,
                  }}
                />
              );
            })}
            <div style={{ position: "absolute", left: x0, top: y + cellH + 14, fontFamily: F.mono, fontSize: 19, color: C.stop, opacity: verdict, whiteSpace: "nowrap" }}>
              worsens {cand.worsens.length}: {cand.worsens.join(" · ")}
            </div>
          </div>
        );
      })}
      <Rise at={380} style={{ position: "absolute", left: M, top: 830, fontFamily: F.mono, fontSize: 22, color: C.paint3, lineHeight: 1.7 }}>
        <div>inputs: {SUMO.inputs}</div>
        <div>baseline: {SUMO.baseline}</div>
      </Rise>
    </SceneFade>
  );
}

function interpolateColor(t: number) {
  // simulated blue to stop red, through the app's two hex values only.
  const a = [0x6e, 0xa2, 0xff];
  const b = [0xf0, 0x52, 0x4f];
  const mix = a.map((v, i) => Math.round(v + (b[i] - v) * Math.min(1, t)));
  return `rgb(${mix.join(",")})`;
}

/* 13. The decision. */
export function Nothing({ dur }: SceneProps) {
  return (
    <SceneFade dur={dur}>
      <Backdrop glow={0.06} grid={false} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <Rise at={4}>
          <Kicker>Decision</Kicker>
        </Rise>
        <Rise at={12} len={22} style={{ fontFamily: F.display, fontWeight: 700, fontSize: 170, color: C.paint1, marginTop: 26, lineHeight: 1 }}>
          Nothing recommended.
        </Rise>
        <Rise at={44} style={{ fontFamily: F.mono, fontSize: 30, color: C.paint2, marginTop: 44, display: "flex", gap: 30, alignItems: "center" }}>
          <StatusMark status="simulated" size={26} />
          <span>every candidate worsened a metric</span>
          <span style={{ color: C.paint4 }}>/</span>
          <span>no validated baseline</span>
        </Rise>
      </AbsoluteFill>
    </SceneFade>
  );
}

/* 14. Close. */
export function Close({ dur, slots, showSlotLabels }: SceneProps) {
  const frame = useCurrentFrame();
  const swap = 168;
  const first = 1 - prog(frame, swap - 14, 14, Easing.linear);
  return (
    <SceneFade dur={dur} edge={16}>
      {slots.h2 ? (
        <div style={{ position: "absolute", inset: 0, opacity: first }}>
          <Slot id="H2" src={slots.h2} dur={dur} label="Night street, signals cycling" showLabel={showSlotLabels} />
        </div>
      ) : (
        <Backdrop glow={0.3} />
      )}
      {frame < swap ? (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", opacity: first }}>
          <Rise at={10} style={{ fontFamily: F.display, fontWeight: 600, fontSize: 104, color: C.paint1 }}>
            It tells you what it knows,
          </Rise>
          <Rise at={52} style={{ fontFamily: F.display, fontWeight: 600, fontSize: 104, color: C.amber }}>
            and what it doesn't.
          </Rise>
        </AbsoluteFill>
      ) : (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
          <Backdrop glow={0.35} />
          <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
            <Rise at={swap + 2} style={{ fontFamily: F.display, fontWeight: 700, fontSize: 170, color: C.paint1, lineHeight: 1 }}>
              CairoFlow
            </Rise>
            <div style={{ width: 420 * prog(frame, swap + 12, 24), height: 3, background: C.amber, margin: "30px 0 34px" }} />
            <Rise at={swap + 20} style={{ fontFamily: F.text, fontWeight: 500, fontSize: 44, color: C.paint1 }}>
              {CREDITS.models}
            </Rise>
            <Rise at={swap + 34} style={{ fontFamily: F.mono, fontSize: 34, color: C.amber, marginTop: 30 }}>
              {CREDITS.repo}
            </Rise>
            <Rise at={swap + 50} style={{ fontFamily: F.mono, fontSize: 22, color: C.paint3, marginTop: 70, textAlign: "center", lineHeight: 1.8 }}>
              <div>{CREDITS.data}</div>
              <div>{CREDITS.scope}</div>
            </Rise>
          </AbsoluteFill>
        </AbsoluteFill>
      )}
    </SceneFade>
  );
}
