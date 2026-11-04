// The cut: scene order, lengths and the voice-over, which is also the burned-in caption track.
// docs/VIDEO.md mirrors this table; change both together.
import { FPS } from "./theme";

export type SceneId =
  | "cold"
  | "title"
  | "place"
  | "tracks"
  | "sam"
  | "twin"
  | "analytics"
  | "gate"
  | "withheld"
  | "evidence"
  | "muse"
  | "sumo"
  | "nothing"
  | "close";

/** Seconds per scene, in order. Scenes overlap by OVERLAP frames for the crossfade. */
export const SCENES: { id: SceneId; seconds: number }[] = [
  { id: "cold", seconds: 10 },
  { id: "title", seconds: 6 },
  { id: "place", seconds: 8 },
  { id: "tracks", seconds: 11 },
  { id: "sam", seconds: 13 },
  { id: "twin", seconds: 12 },
  { id: "analytics", seconds: 7 },
  { id: "gate", seconds: 22 },
  { id: "withheld", seconds: 9 },
  { id: "evidence", seconds: 6 },
  { id: "muse", seconds: 15 },
  { id: "sumo", seconds: 16 },
  { id: "nothing", seconds: 7 },
  { id: "close", seconds: 12 },
];

export const OVERLAP = 12;

export type Placed = { id: SceneId; from: number; durationInFrames: number };

export function placeScenes(): { placed: Placed[]; total: number } {
  let cursor = 0;
  const placed = SCENES.map((scene, index) => {
    const durationInFrames = Math.round(scene.seconds * FPS) + (index < SCENES.length - 1 ? OVERLAP : 0);
    const from = cursor;
    cursor += Math.round(scene.seconds * FPS);
    return { id: scene.id, from, durationInFrames };
  });
  return { placed, total: cursor };
}

/**
 * Voice-over lines: [scene, seconds into the scene, seconds shown, text, caption?]. The caption is
 * burned in unless the same words are already the on-screen type (cold open, title, decision, close).
 */
export const VO: [SceneId, number, number, string, boolean?][] = [
  ["cold", 1.2, 3.4, "Zero point two zero three three.", false],
  ["cold", 4.6, 2.2, "Our limit was zero point two zero.", false],
  ["cold", 7.0, 2.6, "So we didn't show it.", false],
  ["title", 1.0, 4.4, "This is CairoFlow. Traffic intelligence that shows its working.", false],
  ["place", 0.6, 3.4, "One overhead camera. Los Angeles, June 2005."],
  ["place", 4.2, 3.4, "Lankershim Boulevard at Universal Hollywood Drive."],
  ["tracks", 0.6, 4.6, "A detector and a tracker turn fifteen minutes of video"],
  ["tracks", 5.4, 5.0, "into nine hundred and sixty-seven tracks. That count is ungated."],
  ["sam", 0.6, 4.4, "On one sixty-second window, Meta's SAM 3.1 refines the tracks."],
  ["sam", 5.2, 3.8, "It replaces five hundred and eighty-six interpolated rows"],
  ["sam", 9.2, 3.4, "and gives each vehicle its own mask."],
  ["twin", 0.6, 5.0, "Video, tracks and analytics share one clock"],
  ["twin", 5.8, 5.6, "with a 3D twin of the junction, here at four times speed."],
  ["analytics", 0.5, 6.0, "Turning movements, arm by arm, straight from the tracks."],
  ["gate", 0.6, 5.6, "Before a single held-out read, we committed limits for seven checks"],
  ["gate", 6.4, 3.6, "against the published NGSIM trajectories."],
  ["gate", 10.6, 4.8, "Recall. Matching. Position. Lanes. Turns. Speed."],
  ["gate", 15.8, 5.8, "Six pass. Entering counts miss, by zero point zero zero three three."],
  ["withheld", 0.5, 4.2, "So the product withholds entering flow"],
  ["withheld", 4.9, 3.8, "and shows the reason in its place. The limit stays."],
  ["evidence", 0.4, 5.4, "Every headline figure carries its evidence, and its gate."],
  ["muse", 0.6, 4.6, "Meta's Muse Spark reads the record and writes hypotheses."],
  ["muse", 5.4, 4.4, "Model output, labelled as such."],
  ["muse", 10.0, 4.8, "Every citation is checked by code. Three runs, zero problems."],
  ["sumo", 0.6, 4.4, "Then Muse proposes signal changes."],
  ["sumo", 5.2, 4.8, "SUMO tests three candidates, twenty seeds, four timelines."],
  ["sumo", 10.4, 5.2, "Every candidate makes something worse."],
  ["nothing", 0.8, 5.6, "So CairoFlow recommends nothing.", false],
  ["close", 0.8, 5.0, "It tells you what it knows, and what it doesn't.", false],
  ["close", 6.4, 4.8, "Built with SAM 3.1 and Muse Spark.", false],
];
