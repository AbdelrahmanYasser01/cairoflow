// The eleven scenes. Every figure on screen is the product's own (docs/CLAIMS.md); product shots are
// the real app on its committed records. Times are seconds from the scene start, keyed to captions.
import React from "react";
import { AbsoluteFill, Easing, interpolate, Sequence } from "remotion";
import { Video } from "@remotion/media";
import { staticFile } from "remotion";
import { C, F } from "./theme";
import { cue, FPS, scene } from "./timeline";
import { Callout, Chip, EASE, Footnote, Ground, Kicker, Lamp, ramp, Shot, shown, useSec } from "./ui";

const CLAMP = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const f = (s: number) => Math.round(s * FPS);

/** Fade the whole scene in from the ground, and out at the end when asked. */
const Edge: React.FC<{ children: React.ReactNode; seconds: number; inn?: number; out?: number }> = ({ children, seconds, inn = 0.3, out = 0 }) => {
  const t = useSec();
  const a = Math.min(ramp(t, 0, inn), out ? 1 - ramp(t, seconds - out, seconds) : 1);
  return <AbsoluteFill style={{ opacity: a }}>{children}</AbsoluteFill>;
};

const Logo: React.FC<{ size: number }> = ({ size }) => (
  <svg width={size} height={size} viewBox="0 0 32 32">
    <g fill={C.paint1}>
      <rect x="12" y="3" width="8" height="3" />
      <rect x="12" y="26" width="8" height="3" />
      <rect x="3" y="12" width="3" height="8" />
      <rect x="26" y="12" width="3" height="8" />
    </g>
    <circle cx="16" cy="16" r="5" fill={C.amber} />
  </svg>
);

/* 1 ─ Cold open: the number CairoFlow refuses to show. */
export const S01Cold: React.FC = () => {
  const t = useSec();
  const { seconds } = scene("s01_cold");
  const [l1] = cue("s01_cold", 2);
  const [l2] = cue("s01_cold", 3);
  const v = interpolate(t, [0.3, 1.6], [0, 20.33], { ...CLAMP, easing: Easing.bezier(0.15, 0.75, 0.25, 1) });
  const failed = v > 20.0;
  const gone = ramp(t, l2 + 1.0, l2 + 1.9);
  const withheld = ramp(t, l2 + 1.6, l2 + 2.5);
  const barW = 980;
  return (
    <Edge seconds={seconds} inn={0.2} out={0.45}>
      <AbsoluteFill style={{ background: C.bg }} />
      <div style={{ position: "absolute", left: 0, right: 0, top: 250, display: "flex", flexDirection: "column", alignItems: "center", opacity: 1 - gone, filter: `blur(${gone * 26}px)` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 22, opacity: ramp(t, 0.1, 0.6) }}>
          <Kicker color={C.paint2}>Entering count error · held-out blocks</Kicker>
          <Lamp state={failed ? "fail" : "off"} size={16} />
        </div>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 280, lineHeight: 1, marginTop: 26, color: failed ? C.stop : C.paint1, fontVariantNumeric: "tabular-nums", textShadow: failed ? `0 0 60px ${C.stop}55` : "none" }}>
          {v.toFixed(2)}%
        </div>
        <div style={{ position: "relative", width: barW, height: 90, marginTop: 34 }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: 18, height: 6, background: C.rule1, borderRadius: 3 }} />
          <div style={{ position: "absolute", left: 0, top: 18, height: 6, width: (Math.min(v, 25) / 25) * barW, background: failed ? C.stop : C.amber, borderRadius: 3 }} />
          <div style={{ position: "absolute", left: (20 / 25) * barW - 1, top: -4, width: 3, height: 50, background: C.paint1, opacity: ramp(t, 0.2, 0.6) }} />
          <div style={{ position: "absolute", left: (20 / 25) * barW - 160, width: 320, top: 54, textAlign: "center", fontFamily: F.mono, fontSize: 22, color: C.paint1, opacity: ramp(t, 0.3, 0.8) }}>
            LIMIT 20%
            <div style={{ fontSize: 19, color: C.paint3, marginTop: 4, opacity: ramp(t, l1, l1 + 0.6) }}>fixed before we looked</div>
          </div>
        </div>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 270, display: "flex", flexDirection: "column", alignItems: "center", opacity: withheld, transform: `translateY(${(1 - withheld) * 18}px)` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 20, marginBottom: 30 }}>
          <Kicker color={C.paint2}>Entering flow</Kicker>
          <Lamp state="fail" size={16} />
        </div>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 230, lineHeight: 1, color: C.paint1, borderBottom: `9px solid ${C.stop}`, paddingBottom: 10 }}>Withheld</div>
        <div style={{ fontFamily: F.mono, fontSize: 26, color: C.paint2, marginTop: 34 }}>20.33% count error, limit 20%</div>
        <div style={{ fontFamily: F.mono, fontSize: 22, color: C.paint3, marginTop: 8 }}>Limit fixed before the read</div>
      </div>
    </Edge>
  );
};

/* 2 ─ Title. */
export const S02Title: React.FC = () => {
  const t = useSec();
  const { seconds } = scene("s02_title");
  const line = ramp(t, 0.35, 1.4, Easing.bezier(0.65, 0, 0.35, 1));
  return (
    <Edge seconds={seconds} inn={0.25} out={0.5}>
      <Ground grid={0.25} />
      <div style={{ position: "absolute", left: 0, right: 0, top: 300, display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 36, opacity: ramp(t, 0.1, 0.8), transform: `translateY(${(1 - ramp(t, 0.1, 0.9)) * 24}px)` }}>
          <Logo size={150} />
          <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 190, lineHeight: 1, color: C.paint1, letterSpacing: "-0.01em" }}>CairoFlow</div>
        </div>
        <div style={{ marginTop: 40, display: "flex", flexDirection: "column", gap: 9, alignItems: "center" }}>
          <div style={{ width: 860 * line, height: 4, background: C.amber }} />
          <div style={{ width: 860 * line, height: 4, background: C.amber }} />
        </div>
        <div style={{ marginTop: 40, fontFamily: F.text, fontWeight: 500, fontSize: 54, color: C.paint2, opacity: ramp(t, 0.8, 1.5) }}>Traffic intelligence that shows its working.</div>
        <div style={{ marginTop: 70, fontFamily: F.mono, fontSize: 22, color: C.paint3, letterSpacing: "0.08em", opacity: ramp(t, 1.4, 2.1) }}>
          META GLOBAL AI DEVELOPER HACKATHON 2026 · SAM 3.1 · MUSE SPARK
        </div>
      </div>
    </Edge>
  );
};

/* 3 ─ The problem, over the product's command page. */
export const S03Problem: React.FC = () => {
  const { seconds } = scene("s03_problem");
  const [c3] = cue("s03_problem", 3);
  const [c2] = cue("s03_problem", 2);
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Shot
        src="stills/command.png"
        kind="still"
        keys={[
          { t: 0, x: 960, y: 540, z: 1.0 },
          { t: c2 - 0.6, x: 620, y: 520, z: 1.16 },
          { t: c2 + 0.9, x: 430, y: 660, z: 1.75 },
          { t: seconds, x: 440, y: 665, z: 1.82 },
        ]}
      >
        <Callout x={112} y={604} w={218} h={74} from={c3 + 0.0} color={C.stop} label="GATE FAILED" />
        <Callout x={341} y={604} w={172} h={74} from={c3 + 0.55} color={C.go} label="GATE PASSED" />
        <Callout x={524} y={604} w={196} h={74} from={c3 + 1.1} color={C.amber} label="UNGATED" />
      </Shot>
    </Edge>
  );
};

/* 4 ─ Vision: detector and tracker, then SAM 3.1 seeded with those tracks. */
const SamAlone: React.FC = () => {
  const t = useSec();
  const followed = Math.floor(interpolate(t, [0.4, 2.2], [0, 16], CLAMP));
  const bars = [
    { label: "SAM 3.1 alone, tracked boxes", v: 0.45, colour: C.model },
    { label: "Our detector and tracker", v: 0.8, colour: C.paint1 },
  ];
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{ position: "absolute", left: 200, top: 130, opacity: ramp(t, 0, 0.5) }}>
        <Kicker color={C.model}>SAM 3.1 · text prompt "car"</Kicker>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 92, color: C.paint1, marginTop: 14, lineHeight: 1.02 }}>Followed at most 16.</div>
        <div style={{ fontFamily: F.display, fontWeight: 500, fontSize: 92, color: C.paint3, lineHeight: 1.02, opacity: ramp(t, 2.4, 3.1) }}>Nothing new after frame one.</div>
      </div>
      <div style={{ position: "absolute", left: 200, top: 470, display: "flex", gap: 14, flexWrap: "wrap", width: 1520 }}>
        {Array.from({ length: 24 }, (_, i) => {
          const tracked = i < followed;
          const late = i >= 16;
          const a = late ? ramp(t, 2.6 + (i - 16) * 0.12, 3.0 + (i - 16) * 0.12) : ramp(t, 0.3 + i * 0.07, 0.6 + i * 0.07);
          return (
            <div key={i} style={{ width: 48, height: 30, borderRadius: 7, opacity: a, border: `2.5px ${late ? "dashed" : "solid"} ${late ? C.paint4 : tracked ? C.model : C.rule2}`, background: tracked ? `${C.model}22` : "transparent" }} />
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 200, top: 540, fontFamily: F.mono, fontSize: 20, color: C.paint3, opacity: ramp(t, 3.0, 3.5) }}>
        dashed: vehicles entering later, never picked up
      </div>
      <div style={{ position: "absolute", left: 200, top: 640, width: 1520, opacity: ramp(t, 3.6, 4.2) }}>
        <div style={{ fontFamily: F.mono, fontSize: 20, color: C.paint2, marginBottom: 18 }}>RECALL ON SAM'S BEST WINDOW (A TUNING WINDOW)</div>
        {bars.map((b, i) => (
          <div key={b.label} style={{ display: "flex", alignItems: "center", gap: 24, marginBottom: 16 }}>
            <div style={{ width: 440, fontFamily: F.text, fontSize: 30, color: C.paint1 }}>{b.label}</div>
            <div style={{ height: 22, width: 820 * b.v * ramp(t, 3.8 + i * 0.3, 4.8 + i * 0.3), background: b.colour, borderRadius: 3 }} />
            <div style={{ fontFamily: F.mono, fontSize: 30, color: C.paint1, opacity: ramp(t, 4.4 + i * 0.3, 4.8 + i * 0.3) }}>{b.v.toFixed(2)}</div>
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const Seeded: React.FC = () => {
  const t = useSec();
  const box = (label: string, sub: string, colour: string, at: number) => (
    <div style={{ width: 420, padding: "26px 30px", border: `2px solid ${colour}`, borderRadius: 10, background: C.bg1, opacity: ramp(t, at, at + 0.5), transform: `translateY(${(1 - ramp(t, at, at + 0.6)) * 16}px)` }}>
      <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 46, color: C.paint1 }}>{label}</div>
      <div style={{ fontFamily: F.mono, fontSize: 21, color: C.paint2, marginTop: 8 }}>{sub}</div>
    </div>
  );
  const arrow = (label: string, at: number) => (
    <div style={{ width: 200, display: "flex", flexDirection: "column", alignItems: "center", opacity: ramp(t, at, at + 0.4) }}>
      <div style={{ fontFamily: F.mono, fontSize: 21, color: C.amber, marginBottom: 10, textAlign: "center" }}>{label}</div>
      <div style={{ width: 170 * ramp(t, at, at + 0.6), height: 3, background: C.amber }} />
    </div>
  );
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{ position: "absolute", left: 200, top: 150, opacity: ramp(t, 0, 0.5) }}>
        <Kicker>One 60-second window</Kicker>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 92, color: C.paint1, marginTop: 14 }}>Our tracks became SAM's prompts.</div>
      </div>
      <div style={{ position: "absolute", left: 160, right: 160, top: 470, display: "flex", alignItems: "center", justifyContent: "center", gap: 12 }}>
        {box("Detector tracks", "boxes per vehicle", C.paint1, 0.4)}
        {arrow("box prompts, up to 16 per request", 1.0)}
        {box("SAM 3.1", "Meta Model API", C.model, 1.5)}
        {arrow("follows each one", 2.0)}
        {box("Vehicle masks", "model-generated", C.model, 2.4)}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: 760, textAlign: "center", fontFamily: F.mono, fontSize: 24, color: C.paint2, opacity: ramp(t, 3.0, 3.6) }}>
        9 box-prompt requests · $0.32 · seeded from these same tracks
      </div>
    </AbsoluteFill>
  );
};

const Agreed: React.FC = () => {
  const t = useSec();
  const n = Math.round(interpolate(t, [0.3, 1.8], [0, 9900], { ...CLAMP, easing: EASE }));
  return (
    <div style={{ position: "absolute", right: 200, top: 110, width: 600, padding: "28px 34px", background: "rgba(17,21,26,0.95)", border: `1px solid ${C.rule2}`, borderLeft: `4px solid ${C.model}`, borderRadius: 8, opacity: ramp(t, 0.1, 0.5), boxShadow: "0 30px 80px rgba(0,0,0,0.6)" }}>
      <Kicker color={C.model} style={{ fontSize: 19 }}>SAM 3.1, seeded with our tracks</Kicker>
      <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 104, color: C.paint1, lineHeight: 1.05, marginTop: 8, fontVariantNumeric: "tabular-nums" }}>
        {n.toLocaleString("en-US")}<span style={{ color: C.paint3, fontSize: 60 }}> / 10,858</span>
      </div>
      <div style={{ fontFamily: F.text, fontSize: 28, color: C.paint1, marginTop: 4 }}>observed detector rows agreed</div>
      <div style={{ fontFamily: F.mono, fontSize: 19, color: C.paint2, marginTop: 14, lineHeight: 1.5, opacity: ramp(t, 2.0, 2.6) }}>
        49 rejected · 909 not reached<br />a consistency check, not independent confirmation
      </div>
    </div>
  );
};

export const S04Vision: React.FC = () => {
  const { seconds } = scene("s04_vision");
  const [c1] = cue("s04_vision", 1);
  const [c2] = cue("s04_vision", 2);
  const [c3] = cue("s04_vision", 3);
  const [c4] = cue("s04_vision", 4);
  const aEnd = c1 - 0.2;
  const bEnd = c2 - 0.2;
  const cEnd = c3 - 0.2;
  const dEnd = c4 - 0.25;
  const eLen = seconds - dEnd;
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Sequence durationInFrames={f(aEnd)} layout="none">
        <Shot src="clips/vision_boxes.mp4" kind="video" keys={[{ t: 0, x: 960, y: 540, z: 1.0 }, { t: aEnd, x: 760, y: 520, z: 1.1 }]} />
        <Chip x={200} y={760} from={0.4} to={aEnd} title="Vision Lab · 15-minute study" sub="NGSIM camera 2 · Lankershim Blvd, Los Angeles · 16 June 2005" />
      </Sequence>
      <Sequence from={f(aEnd)} durationInFrames={f(bEnd - aEnd)} layout="none">
        <Shot src="clips/vision_tracks.mp4" kind="video" keys={[{ t: 0, x: 560, y: 520, z: 1.3 }, { t: bEnd - aEnd, x: 600, y: 500, z: 1.2 }]} />
        <Chip x={200} y={760} from={0.3} to={bEnd - aEnd} title="Local detector + tracker" sub="967 counted tracks over fifteen minutes" />
      </Sequence>
      <Sequence from={f(bEnd)} durationInFrames={f(cEnd - bEnd)} layout="none">
        <SamAlone />
      </Sequence>
      <Sequence from={f(cEnd)} durationInFrames={f(dEnd - cEnd)} layout="none">
        <Seeded />
      </Sequence>
      <Sequence from={f(dEnd)} durationInFrames={f(eLen)} layout="none">
        <Shot src="clips/vision_sam.mp4" kind="video" playbackRate={7 / (eLen + 0.1)} keys={[{ t: 0, x: 600, y: 470, z: 1.25 }, { t: eLen, x: 640, y: 470, z: 1.32 }]} />
        <Agreed />
        <Chip x={200} y={760} from={c4 - dEnd + 6.2} to={eLen} title="SAM 3.1 masks · model-generated" sub="outlines from SAM, on the shared clock" color={C.model} />
      </Sequence>
    </Edge>
  );
};

/* 5 ─ The twin. */
export const S05Twin: React.FC = () => {
  const { seconds } = scene("s05_twin");
  const [c1] = cue("s05_twin", 1);
  const [c2] = cue("s05_twin", 2);
  const aEnd = c1 - 0.15;
  const bLen = seconds - aEnd;
  const label = c2 - aEnd;
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Sequence durationInFrames={f(aEnd)} layout="none">
        <Shot src="clips/twin_along.mp4" kind="video" keys={[{ t: 0, x: 960, y: 540, z: 1.0 }, { t: aEnd, x: 980, y: 520, z: 1.07 }]} />
        <Chip x={200} y={92} from={0.4} to={aEnd} title="Digital twin" sub="built from the same record, driven by the same clock" />
      </Sequence>
      <Sequence from={f(aEnd)} durationInFrames={f(bLen)} layout="none">
        <Shot
          src="clips/twin_select.mp4"
          kind="video"
          playbackRate={6 / (bLen + 0.1)}
          keys={[{ t: 0, x: 960, y: 540, z: 1.0 }, { t: label - 0.4, x: 960, y: 540, z: 1.0 }, { t: label + 0.8, x: 760, y: 900, z: 1.4 }, { t: bLen, x: 760, y: 905, z: 1.44 }]}
        >
          <Callout x={1558} y={168} w={322} h={600} from={0.25} to={label - 0.15} label="ITS RECORD" />
          <Callout x={116} y={940} w={1534} h={32} from={label + 0.6} spot label="EVERY APPROXIMATION, LABELLED" />
        </Shot>
      </Sequence>
    </Edge>
  );
};

/* 6 ─ Analytics. */
export const S06Analytics: React.FC = () => {
  const { seconds } = scene("s06_analytics");
  const [c1] = cue("s06_analytics", 1);
  const aEnd = 2.9;
  const bLen = seconds - aEnd;
  const r1 = c1 - aEnd;
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Sequence durationInFrames={f(aEnd)} layout="none">
        <Shot src="clips/twin_plan.mp4" kind="video" keys={[{ t: 0, x: 960, y: 540, z: 1.0 }, { t: aEnd, x: 960, y: 540, z: 1.08 }]} />
      </Sequence>
      <Sequence from={f(aEnd)} durationInFrames={f(bLen)} layout="none">
        <Shot
          src="stills/analytics.png"
          kind="still"
          keys={[
            { t: 0, x: 300, y: 230, z: 1.7 },
            { t: 1.3, x: 300, y: 230, z: 1.7 },
            { t: 2.4, x: 625, y: 830, z: 1.15 },
            { t: r1 - 0.2, x: 625, y: 830, z: 1.15 },
            { t: r1 + 0.8, x: 1520, y: 720, z: 1.75 },
            { t: bLen, x: 1520, y: 722, z: 1.8 },
          ]}
        >
          <Callout x={112} y={178} w={150} h={66} from={0.3} to={2.2} label="COUNTED TRACKS" />
          <Callout x={1664} y={677} w={72} h={26} from={r1 + 0.9} label="CAMERA / PUBLISHED" />
        </Shot>
      </Sequence>
    </Edge>
  );
};

/* 7 ─ The gate. */
const GATE = [
  { name: "Share of vehicle-frames found", measured: 76.12, unit: "%", limit: "at least 70%", pass: true },
  { name: "Track rows matched to NGSIM", measured: 86.34, unit: "%", limit: "at least 80%", pass: true },
  { name: "Sideways position error, p90", measured: 1.39, unit: " m", limit: "at most 1.5 m", pass: true },
  { name: "Same lane as published", measured: 88.08, unit: "%", limit: "at least 85%", pass: true },
  { name: "Entering count error", measured: 20.33, unit: "%", limit: "at most 20%", pass: false },
  { name: "Turning count agreement", measured: 84.7, unit: "%", limit: "at least 80%", pass: true },
  { name: "Speed error, median", measured: 0.48, unit: " m/s", limit: "at most 1 m/s", pass: true },
];

/** When each passing row reports, seconds from the scene start (the edit puts a tick on each). */
export const gatePassTimes = (): number[] => {
  const [c4] = cue("s07_gate", 4);
  return GATE.map((row, i) => (row.pass ? c4 + 0.05 + (i > 4 ? i - 1 : i) * 0.19 : -1)).filter((x) => x >= 0);
};

const Blocks: React.FC<{ t: number }> = ({ t }) => {
  const x0 = 210;
  const w = 88;
  const gap = 12;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0 }}>
      <div style={{ position: "absolute", left: 200, top: 130 }}>
        <Kicker>Fifteen one-minute blocks · source 120 to 1020 s</Kicker>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 84, color: C.paint1, marginTop: 12 }}>Tune on even. Score odd, once.</div>
      </div>
      {Array.from({ length: 15 }, (_, i) => {
        const held = i % 2 === 1;
        const a = ramp(t, 0.15 + i * 0.07, 0.45 + i * 0.07);
        const fill = held ? ramp(t, 3.6, 4.4) : ramp(t, 1.6, 2.4);
        return (
          <div key={i} style={{ position: "absolute", left: x0 + i * (w + gap), top: 380, width: w, height: 150, opacity: a, borderRadius: 6, border: `2.5px solid ${held ? C.amber : C.rule2}`, background: held ? `rgba(245,166,35,${0.18 * fill})` : `rgba(95,102,109,${0.55 * fill})`, boxShadow: held ? `0 0 ${30 * fill}px rgba(245,166,35,${0.35 * fill})` : "none" }}>
            <div style={{ position: "absolute", bottom: 10, left: 0, right: 0, textAlign: "center", fontFamily: F.mono, fontSize: 15, color: held ? C.amber : C.paint2, opacity: fill }}>{held ? "HELD" : "TUNE"}</div>
          </div>
        );
      })}
      <div style={{ position: "absolute", left: x0, top: 560, fontFamily: F.mono, fontSize: 22, color: C.paint2, opacity: ramp(t, 1.8, 2.4) }}>■ tuned the detector, tracker and thresholds</div>
      <div style={{ position: "absolute", left: x0 + 760, top: 560, fontFamily: F.mono, fontSize: 22, color: C.amber, opacity: ramp(t, 3.8, 4.4) }}>■ held out: one blind read, limits fixed first</div>
      <div style={{ position: "absolute", left: x0, top: 640, fontFamily: F.text, fontSize: 34, color: C.paint1, opacity: ramp(t, 6.4, 7.0) }}>
        Scored against the published NGSIM trajectories.
      </div>
    </div>
  );
};

const GateTable: React.FC<{ t: number; reveal: number; passFrom: number; failAt: number; limitAt: number }> = ({ t, reveal, passFrom, failAt, limitAt }) => {
  const passIndex = (i: number) => (i > 4 ? i - 1 : i);
  return (
    <div style={{ position: "absolute", left: 200, right: 200, top: 120 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Kicker>Gate cv-int2-v2 · limits committed before the read</Kicker>
        <div style={{ fontFamily: F.mono, fontSize: 22, color: C.paint2, opacity: ramp(t, failAt + 0.6, failAt + 1.2) }}>6 of 7 checks passed</div>
      </div>
      <div style={{ display: "flex", marginTop: 28, paddingBottom: 12, borderBottom: `1px solid ${C.rule2}`, fontFamily: F.mono, fontSize: 19, color: C.paint3, letterSpacing: "0.06em" }}>
        <div style={{ flex: 1 }}>CHECK</div>
        <div style={{ width: 260, textAlign: "right" }}>HELD-OUT RESULT</div>
        <div style={{ width: 300, textAlign: "right" }}>LIMIT</div>
        <div style={{ width: 150, textAlign: "right" }}>OUTCOME</div>
      </div>
      {GATE.map((row, i) => {
        const appear = ramp(t, reveal + i * 0.22, reveal + 0.4 + i * 0.22);
        const at = row.pass ? passFrom + passIndex(i) * 0.19 : failAt;
        const known = t >= at;
        const red = !row.pass && known;
        const limitHi = !row.pass ? shown(t, limitAt, 1e6, 0.3) : 0;
        return (
          <div key={row.name} style={{ position: "relative", display: "flex", alignItems: "center", height: 84, borderBottom: `1px solid ${C.rule1}`, opacity: appear, background: red ? "rgba(240,82,79,0.10)" : "transparent" }}>
            {red ? <div style={{ position: "absolute", left: -18, top: 14, bottom: 14, width: 6, background: C.stop, borderRadius: 3 }} /> : null}
            <div style={{ flex: 1, fontFamily: F.text, fontSize: 33, color: C.paint1 }}>{row.name}</div>
            <div style={{ width: 260, textAlign: "right", fontFamily: F.mono, fontSize: 32, color: red ? C.stop : C.paint1, opacity: known ? 1 : 0.35, fontWeight: red ? 500 : 400 }}>
              {known ? `${row.measured.toFixed(2)}${row.unit}` : "—"}
            </div>
            <div style={{ width: 300, textAlign: "right", fontFamily: F.mono, fontSize: 28, color: limitHi ? C.amber : C.paint2 }}>
              <span style={{ padding: "4px 10px", border: `2px solid rgba(245,166,35,${limitHi})`, borderRadius: 5 }}>{row.limit}</span>
            </div>
            <div style={{ width: 150, display: "flex", justifyContent: "flex-end" }}>
              <Lamp state={known ? (row.pass ? "pass" : "fail") : "off"} size={15} />
            </div>
          </div>
        );
      })}
    </div>
  );
};

export const S07Gate: React.FC = () => {
  const t = useSec();
  const { seconds } = scene("s07_gate");
  const [c1] = cue("s07_gate", 1);
  const [c2] = cue("s07_gate", 2);
  const [c3, c3e] = cue("s07_gate", 3);
  const [c4] = cue("s07_gate", 4);
  const [c5] = cue("s07_gate", 5);
  const [c6] = cue("s07_gate", 6);
  const tableA = 1 - ramp(t, c2 - 0.1, c2 + 0.4);
  const blocks = shown(t, c2 + 0.1, c3e + 0.3, 0.45);
  const agree = shown(t, c3 - 0.1, c3e + 0.25, 0.3);
  const tableB = ramp(t, c3e + 0.15, c3e + 0.6);
  return (
    <Edge seconds={seconds}>
      <Ground />
      <AbsoluteFill style={{ opacity: tableA }}>
        <GateTable t={t} reveal={c1} passFrom={1e6} failAt={1e6} limitAt={1e6} />
      </AbsoluteFill>
      <AbsoluteFill style={{ opacity: blocks * (1 - agree) }}>
        <Sequence from={f(c2)} layout="none">
          <BlocksAt />
        </Sequence>
        <Footnote from={c2 + 4.5} y={760}>
          Gate v1's read was lost with its session and gave no result; v2 kept identical limits and took its own single read.
          <br />The camera placement was not held out, so the position and lane checks aren't independent of its fit.
        </Footnote>
      </AbsoluteFill>
      <AbsoluteFill style={{ opacity: agree, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column" }}>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 150, color: C.paint1, lineHeight: 1 }}>Agreement,</div>
        <div style={{ fontFamily: F.display, fontWeight: 500, fontSize: 150, color: C.paint3, lineHeight: 1.05 }}>not accuracy.</div>
        <div style={{ fontFamily: F.mono, fontSize: 22, color: C.paint2, marginTop: 30 }}>The reference shares the video. No independent labels exist.</div>
      </AbsoluteFill>
      <AbsoluteFill style={{ opacity: tableB }}>
        <GateTable t={t} reveal={-10} passFrom={c4 + 0.05} failAt={c5} limitAt={c6} />
      </AbsoluteFill>
    </Edge>
  );
};

const BlocksAt: React.FC = () => {
  const t = useSec();
  return <Blocks t={t} />;
};

/* 8 ─ Withheld: the miss, the reason, the contract. */
const CODE = [
  { text: "class Metric(EvidenceModel):", hi: false },
  { text: '    """One number shown to a person, with what it is and how far it can be trusted."""', hi: false, dim: true },
  { text: "    ...", hi: false, dim: true },
  { text: "    value: float | None = Field(None, allow_inf_nan=False)", hi: false },
  { text: "    ...", hi: false, dim: true },
  { text: "    status: MetricStatus", hi: false },
  { text: "    ...", hi: false, dim: true },
  { text: '    @model_validator(mode="after")', hi: false },
  { text: "    def _honest(self) -> Metric:", hi: false },
  { text: '        if self.status in ("gate_fail", "not_available") and self.value is not None:', hi: true },
  { text: '            raise ValueError(f"a metric with status {self.status} cannot carry a value")', hi: true },
  { text: "        ...", hi: false, dim: true },
  { text: '        if self.status != "gate_pass" and not self.reason:', hi: false },
  { text: '            raise ValueError(f"status {self.status} needs a reason")', hi: false },
];

const Contract: React.FC = () => {
  const t = useSec();
  const hi = ramp(t, 3.4, 4.2);
  return (
    <AbsoluteFill>
      <Ground />
      <div style={{ position: "absolute", left: 200, top: 110, opacity: ramp(t, 0, 0.4) }}>
        <Kicker>packages/contracts/cairoflow_contracts/analysis_record.py</Kicker>
        <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 84, color: C.paint1, marginTop: 12 }}>Absent, not zero.</div>
      </div>
      <div style={{ position: "absolute", left: 200, right: 200, top: 330, padding: "30px 36px", background: C.bg1, border: `1px solid ${C.rule2}`, borderRadius: 10 }}>
        {CODE.map((line, i) => {
          const a = ramp(t, 0.3 + i * 0.12, 0.6 + i * 0.12);
          return (
            <div key={i} style={{ position: "relative", fontFamily: F.mono, fontSize: 25, lineHeight: "40px", height: 40, whiteSpace: "pre", color: line.dim ? C.paint3 : line.hi ? C.paint1 : C.paint2, opacity: a }}>
              {line.hi ? <div style={{ position: "absolute", left: -18, right: -18, top: 2, bottom: 2, background: `rgba(240,82,79,${0.16 * hi})`, borderLeft: `4px solid rgba(240,82,79,${hi})` }} /> : null}
              <span style={{ position: "relative" }}>{line.text}</span>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const S08Withheld: React.FC = () => {
  const { seconds } = scene("s08_withheld");
  const [c1] = cue("s08_withheld", 1);
  const [c2] = cue("s08_withheld", 2);
  const [c3] = cue("s08_withheld", 3);
  const aEnd = c2 - 0.2;
  const bEnd = c3 - 0.2;
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Sequence durationInFrames={f(aEnd)} layout="none">
        <Shot src="stills/evidence_gates.png" kind="still" keys={[{ t: 0, x: 960, y: 520, z: 1.0 }, { t: 1.2, x: 960, y: 600, z: 1.05 }, { t: aEnd, x: 960, y: 605, z: 1.08 }]}>
          <Callout x={112} y={546} w={1772} h={60} from={0.5} color={C.stop} label="GATE FAILED" />
          <Callout x={112} y={742} w={965} h={44} from={c1 + 0.1} label="NOT MOVED" />
        </Shot>
        <Chip x={1110} y={660} from={0.6} to={aEnd} title="The miss" sub="0.2033 against at most 0.20: over by 0.0033" color={C.stop} width={560} />
      </Sequence>
      <Sequence from={f(aEnd)} durationInFrames={f(bEnd - aEnd)} layout="none">
        <Shot src="stills/analytics_check_open.png" kind="still" keys={[{ t: 0, x: 470, y: 300, z: 1.45 }, { t: 1.6, x: 470, y: 300, z: 1.45 }, { t: 2.6, x: 450, y: 560, z: 1.45 }, { t: bEnd - aEnd, x: 450, y: 565, z: 1.5 }]}>
          <Callout x={556} y={180} w={232} h={98} from={0.3} color={C.stop} label="NO FIGURE" />
          <Callout x={124} y={372} w={680} h={372} from={2.4} label="ONLY THE REASON" />
        </Shot>
      </Sequence>
      <Sequence from={f(bEnd)} durationInFrames={f(seconds - bEnd)} layout="none">
        <Contract />
      </Sequence>
    </Edge>
  );
};

/* 9 ─ Muse Spark reads the evidence. */
export const S09Muse: React.FC = () => {
  const { seconds } = scene("s09_muse");
  const [c1] = cue("s09_muse", 1);
  const [c2] = cue("s09_muse", 2);
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Shot
        src="stills/evidence_muse.png"
        kind="still"
        keys={[
          { t: 0, x: 760, y: 330, z: 1.0 },
          { t: 1.6, x: 660, y: 220, z: 1.4 },
          { t: c1 - 0.3, x: 660, y: 230, z: 1.42 },
          { t: c1 + 0.8, x: 520, y: 380, z: 1.6 },
          { t: c2 - 0.2, x: 520, y: 385, z: 1.6 },
          { t: c2 + 0.8, x: 520, y: 250, z: 1.65 },
          { t: seconds, x: 520, y: 252, z: 1.68 },
        ]}
      >
        <Callout x={964} y={62} w={322} h={22} from={1.3} to={c1} color={C.model} label="MODEL-GENERATED" />
        <Callout x={116} y={322} w={520} h={94} from={c1 + 0.9} to={c2} color={C.approx} label="CITATIONS RESOLVE TO EVIDENCE IDS" />
        <Callout x={114} y={234} w={430} h={22} from={c2 + 0.9} color={C.go} label="CHECKED BY CODE" />
      </Shot>
      <Chip x={1170} y={600} from={c1 + 0.3} to={c2 - 0.1} title="Reply schema" sub="No numeric field. Code flags any number in its text that no evidence holds." color={C.model} width={540} />
      <Chip x={1170} y={600} from={c2 + 0.2} title="muse-spark-1.3 · 3 replies" sub="3 of 3 usable, checked by code. None cited the withheld counts." color={C.go} width={540} />
    </Edge>
  );
};

/* 10 ─ The decision: recommend nothing, and say why. */
export const S10Decision: React.FC = () => {
  const { seconds } = scene("s10_decision");
  const [c1] = cue("s10_decision", 1);
  const [c2] = cue("s10_decision", 2);
  const aEnd = c2 - 0.15;
  const bLen = seconds - aEnd;
  return (
    <Edge seconds={seconds}>
      <Ground />
      <Sequence durationInFrames={f(aEnd)} layout="none">
        <Shot
          src="stills/evidence_decision.png"
          kind="still"
          keys={[
            { t: 0, x: 680, y: 660, z: 1.12 },
            { t: c1 - 0.4, x: 680, y: 700, z: 1.25 },
            { t: c1 + 0.6, x: 500, y: 300, z: 1.45 },
            { t: c1 + 2.4, x: 500, y: 300, z: 1.45 },
            { t: c1 + 3.4, x: 700, y: 760, z: 1.35 },
            { t: aEnd, x: 700, y: 765, z: 1.38 },
          ]}
        >
          <Callout x={112} y={455} w={1176} h={46} from={0.5} to={c1} color={C.sim} label="SUMO · 3 CANDIDATES · 20 PAIRED SEEDS · 4 SIGNAL TIMELINES" />
          <Callout x={112} y={168} w={360} h={104} from={c1 + 0.6} to={c1 + 3.1} color={C.assumed} label="NO VALIDATED BASELINE" />
          <Callout x={604} y={556} w={300} h={490} from={c1 + 3.3} color={C.stop} label="EVERY CANDIDATE WORSENS SOMETHING" />
        </Shot>
        <Footnote from={0.8} to={aEnd} y={880}>This decision used the published NGSIM trajectories, not this camera analysis.</Footnote>
      </Sequence>
      <Sequence from={f(aEnd)} durationInFrames={f(bLen)} layout="none">
        <Shot src="stills/evidence_decision.png" kind="still" keys={[{ t: 0, x: 760, y: 270, z: 1.7 }, { t: bLen, x: 760, y: 275, z: 1.85 }]}>
          <Callout x={640} y={232} w={530} h={124} from={0.5} label="ABSTAIN" spot />
        </Shot>
      </Sequence>
    </Edge>
  );
};

/* 11 ─ Close. */
export const S11Close: React.FC = () => {
  const t = useSec();
  const { seconds } = scene("s11_close");
  return (
    <Edge seconds={seconds} inn={0.6} out={1.0}>
      <AbsoluteFill style={{ background: C.bg }} />
      <AbsoluteFill style={{ opacity: 0.22, filter: "blur(5px)", transform: `scale(${1.08 + t * 0.006})` }}>
        <Video src={staticFile("clips/twin_along.mp4")} muted trimBefore={f(1)} style={{ width: 1920, height: 1080 }} />
      </AbsoluteFill>
      <AbsoluteFill style={{ background: "radial-gradient(900px 520px at 50% 45%, rgba(10,12,14,0.55), rgba(10,12,14,0.95))" }} />
      <div style={{ position: "absolute", left: 0, right: 0, top: 250, display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 30, opacity: ramp(t, 0.2, 0.9) }}>
          <Logo size={120} />
          <div style={{ fontFamily: F.display, fontWeight: 600, fontSize: 150, color: C.paint1, lineHeight: 1 }}>CairoFlow</div>
        </div>
        <div style={{ marginTop: 36, fontFamily: F.text, fontWeight: 500, fontSize: 52, color: C.paint1, opacity: ramp(t, 0.6, 1.3) }}>
          It tells you what it knows, <span style={{ color: C.amber }}>and what it doesn't.</span>
        </div>
        <div style={{ marginTop: 64, display: "flex", gap: 56, fontFamily: F.mono, fontSize: 23, color: C.paint2, opacity: ramp(t, 2.0, 2.7) }}>
          <span>Open source · MIT</span>
          <span style={{ color: C.rule2 }}>|</span>
          <span>Runs offline from a fresh clone · no API key</span>
          <span style={{ color: C.rule2 }}>|</span>
          <span>Meta SAM 3.1 · Muse Spark</span>
        </div>
        <div style={{ marginTop: 26, fontFamily: F.mono, fontSize: 24, color: C.amber, opacity: ramp(t, 2.4, 3.1) }}>github.com/AbdelrahmanYasser01/cairoflow</div>
      </div>
      <div style={{ position: "absolute", left: 160, right: 160, bottom: 46, textAlign: "center", fontFamily: F.mono, fontSize: 15, lineHeight: 1.6, color: C.paint3, opacity: ramp(t, 3.2, 3.9) }}>
        Derived tracks: NGSIM Lankershim Boulevard data, U.S. Department of Transportation Federal Highway Administration, CC BY-SA 4.0. Los Angeles, 2005; no Cairo data.
        <br />Decision support only: nothing here controls real traffic. Voice: Kokoro, synthetic. Score: original. This film: CC BY-SA 4.0.
      </div>
    </Edge>
  );
};
