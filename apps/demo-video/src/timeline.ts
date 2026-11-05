// The edit is cut to the voiceover: each scene lasts its voice track plus a hold from script.json.
import script from "../scripts/script.json";
import vo from "../public/audio/vo.json";

export const FPS = 30;

export type Caption = { text: string; start: number; end: number };
export type SceneId =
  | "s00_open" | "s01_cold" | "s03_problem" | "s04_vision" | "s05_twin" | "s06_analytics"
  | "s07_gate" | "s08_withheld" | "s09_muse" | "s10_decision" | "s11_close";

type VoScene = { file: string; duration: number; captions: Caption[] };
const voice = vo as Record<string, VoScene>;

export type Slot = {
  id: SceneId;
  from: number; // frames, absolute
  frames: number;
  start: number; // seconds, absolute
  seconds: number;
  audio: string;
  captions: Caption[]; // seconds from scene start
};

export const SCENES: Slot[] = (() => {
  let t = 0;
  return script.scenes.map((scene) => {
    const v = voice[scene.id];
    const seconds = v.duration + scene.hold;
    const slot: Slot = {
      id: scene.id as SceneId,
      from: Math.round(t * FPS),
      frames: 0,
      start: t,
      seconds,
      audio: v.file,
      captions: v.captions,
    };
    t += seconds;
    slot.frames = Math.round(t * FPS) - slot.from;
    return slot;
  });
})();

export const TOTAL_FRAMES = SCENES[SCENES.length - 1].from + SCENES[SCENES.length - 1].frames;

export const scene = (id: SceneId): Slot => {
  const found = SCENES.find((s) => s.id === id);
  if (!found) throw new Error(`no scene ${id}`);
  return found;
};

/** Caption i of a scene: [start, end] in seconds from the scene start. */
export const cue = (id: SceneId, i: number): [number, number] => {
  const c = scene(id).captions[i];
  return [c.start, c.end];
};
