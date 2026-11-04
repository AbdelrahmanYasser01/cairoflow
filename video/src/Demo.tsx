import "./fonts";
import type { FC } from "react";
import { AbsoluteFill, interpolate, Sequence, useCurrentFrame } from "remotion";
import type { Clip } from "./primitives";
import { Analytics, Close, Cold, Evidence, Gate, Muse, Nothing, Place, Sam, type SceneProps, Sumo, Title, Tracks, Twin, Withheld } from "./scenes";
import { placeScenes, type SceneId, VO } from "./script";
import { C, F, FPS } from "./theme";

export type DemoProps = {
  captures: SceneProps["captures"];
  slots: SceneProps["slots"];
  footage?: string;
  showSlotLabels: boolean;
  captions: boolean;
};

export const defaultDemoProps: DemoProps = { captures: {}, slots: {}, showSlotLabels: true, captions: true };

const SCENE_COMPONENTS: Record<SceneId, FC<SceneProps>> = {
  cold: Cold,
  title: Title,
  place: Place,
  tracks: Tracks,
  sam: Sam,
  twin: Twin,
  analytics: Analytics,
  gate: Gate,
  withheld: Withheld,
  evidence: Evidence,
  muse: Muse,
  sumo: Sumo,
  nothing: Nothing,
  close: Close,
};

const { placed, total } = placeScenes();
export const TOTAL_FRAMES = total;

const CUES = VO.filter(([, , , , caption]) => caption !== false).map(([scene, at, len, text]) => {
  const start = placed.find((p) => p.id === scene)!.from + Math.round(at * FPS);
  return { start, end: start + Math.round(len * FPS), text };
});

function Captions() {
  const frame = useCurrentFrame();
  const cue = CUES.find((c) => frame >= c.start && frame < c.end);
  if (!cue) return null;
  const opacity = Math.min(
    interpolate(frame, [cue.start, cue.start + 5], [0, 1], { extrapolateRight: "clamp" }),
    interpolate(frame, [cue.end - 5, cue.end], [1, 0], { extrapolateLeft: "clamp" }),
  );
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 64, pointerEvents: "none" }}>
      <div
        style={{
          opacity,
          maxWidth: 1500,
          padding: "10px 26px 12px",
          background: "rgba(10,12,14,0.78)",
          fontFamily: F.text,
          fontWeight: 500,
          fontSize: 38,
          lineHeight: 1.3,
          color: C.paint1,
          textAlign: "center",
        }}
      >
        {cue.text}
      </div>
    </AbsoluteFill>
  );
}

function Progress() {
  const frame = useCurrentFrame();
  return <div style={{ position: "absolute", left: 0, bottom: 0, height: 3, width: `${(frame / total) * 100}%`, background: C.amber, opacity: 0.8 }} />;
}

export const Demo: FC<DemoProps> = (props) => {
  const sceneProps = (dur: number): SceneProps => ({
    dur,
    captures: props.captures as Partial<Record<keyof SceneProps["captures"], Clip>>,
    slots: props.slots,
    footage: props.footage,
    showSlotLabels: props.showSlotLabels,
  });
  return (
    <AbsoluteFill style={{ background: C.bg }}>
      {placed.map(({ id, from, durationInFrames }) => {
        const Scene = SCENE_COMPONENTS[id];
        return (
          <Sequence key={id} from={from} durationInFrames={durationInFrames} name={id}>
            <Scene {...sceneProps(durationInFrames)} />
          </Sequence>
        );
      })}
      {props.captions ? <Captions /> : null}
      <Progress />
    </AbsoluteFill>
  );
};
