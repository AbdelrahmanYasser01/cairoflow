// The film: eleven scenes on the voiceover timeline, the score under them, effects on the cuts.
import React from "react";
import { AbsoluteFill, Sequence, staticFile } from "remotion";
import { Audio } from "@remotion/media";
import { C } from "./theme";
import { FPS, SCENES, scene, type SceneId } from "./timeline";
import { Captions } from "./ui";
import {
  gatePassTimes, S00Open, S01Cold, S03Problem, S04Vision, S05Twin, S06Analytics, S07Gate, S08Withheld,
  S09Muse, S10Decision, S11Close,
} from "./scenes";

const VIEWS: Record<SceneId, React.FC> = {
  s00_open: S00Open,
  s01_cold: S01Cold,
  s03_problem: S03Problem,
  s04_vision: S04Vision,
  s05_twin: S05Twin,
  s06_analytics: S06Analytics,
  s07_gate: S07Gate,
  s08_withheld: S08Withheld,
  s09_muse: S09Muse,
  s10_decision: S10Decision,
  s11_close: S11Close,
};

const at = (seconds: number) => Math.round(seconds * FPS);

export const CairoFlowDemo: React.FC = () => {
  const gate = scene("s07_gate");
  const whooshes: SceneId[] = ["s03_problem", "s07_gate", "s09_muse", "s11_close"];
  return (
    <AbsoluteFill style={{ background: C.bg }}>
      {SCENES.map((slot) => {
        const View = VIEWS[slot.id];
        return (
          <Sequence key={slot.id} name={slot.id} from={slot.from} durationInFrames={slot.frames}>
            <View />
            <Audio src={staticFile(slot.audio)} volume={1} />
          </Sequence>
        );
      })}
      <Audio src={staticFile("audio/score.wav")} volume={0.9} />
      {gatePassTimes().map((s, i) => (
        <Sequence key={`tick${i}`} from={gate.from + at(s)} durationInFrames={at(0.3)}>
          <Audio src={staticFile("audio/sfx_tick.wav")} volume={0.55} />
        </Sequence>
      ))}
      {whooshes.map((id) => (
        <Sequence key={`whoosh-${id}`} from={Math.max(0, scene(id).from - at(0.45))} durationInFrames={at(1)}>
          <Audio src={staticFile("audio/sfx_whoosh.wav")} volume={0.5} />
        </Sequence>
      ))}
      <Captions />
    </AbsoluteFill>
  );
};
