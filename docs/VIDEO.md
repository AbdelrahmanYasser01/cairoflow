# CairoFlow demo video

**"The number we refused to show."** A 2:32 cut for the judges, built in [Remotion](https://www.remotion.dev)
from the app's own palette, fonts and figures, plus clips captured from the running app. It works muted:
the voice-over is burned in as captions wherever the on-screen type doesn't already say it.

- Composition: `video/src` (React). Figures: `video/src/facts.ts`. Cut and voice-over: `video/src/script.ts`.
- Output: `video/out/cairoflow-demo.mp4`, 1920x1080, 30 fps, H.264, yuv420p, 4560 frames, no audio track yet.
- Remotion is free for individuals and companies of up to three people; a larger organisation needs a
  company licence (remotion.dev/license).
- Every figure comes from README.md or `data/sample`, unrounded. The SAM masks on screen are the committed
  outlines (`video/scripts/prepare-data.mjs` copies them); the Muse hypothesis, its citations, the 24
  evidence ids and the SUMO verdicts are copied into `video/src/facts.ts` from `data/sample`.
- The app clips are captured from the merged build (command center, Vision Lab, twin, analytics, evidence).
  They show the app's own top bar, so "Los Angeles, 2005 · decision support only" appears there as well as
  on the last card.
- The two Higgsfield shots are illustrative. They stay labelled "Illustrative, not data" on screen. The
  submission cut is rendered with `--final`, so the slot ids are hidden and only that label shows.
- How this cut was made: one full render from clips of the merged build, then the scenes that changed were
  re-rendered and spliced in (see [Fix one scene](#fix-one-scene-without-a-full-render)). The twin is
  frame-stepped; the command center, evidence, analytics and Vision Lab clips were re-captured with
  `--screencast` after the last UI pass.

## Storyboard

Frames are at 30 fps; each scene runs 12 frames into the next for the crossfade.

| Time | Frames | Scene | Visual | On-screen text | Voice-over |
|---|---|---|---|---|---|
| 0:00 | 0-311 | Cold open | Black. `0.2033` types on in IBM Plex Mono, amber caret. A zoomed gauge: the dot slides past the amber 0.20 tick, a red bracket marks the gap. A grey bar wipes over the number and stamps WITHHELD. | 0.2033 · limit 0.20 · +0.0033 · So we didn't show it. · WITHHELD | "Zero point two zero three three. Our limit was zero point two zero. So we didn't show it." |
| 0:10 | 300-491 | Title | Letters of CairoFlow rise in Barlow Semi Condensed; amber rule draws. | CairoFlow · Traffic intelligence that shows its working. | "This is CairoFlow. Traffic intelligence that shows its working." |
| 0:16 | 480-731 | Place (**Higgsfield H1**) | Generated aerial of an intersection at dusk (until then: abstract light-trail card). Dateline at left. No caption: the dateline says it. | One camera · NGSIM · Lankershim Blvd · at Universal Hollywood Dr · Los Angeles / 16 June 2005 / overhead video · *Illustrative, not data* | "One overhead camera. Los Angeles, June 2005. Lankershim Boulevard at Universal Hollywood Drive." |
| 0:24 | 720-1061 | Camera lane | Vision Lab capture, pushed in on its frame panel: the long exposure of every stored track (Tracks, no video), playing at 1x (or the annotated NGSIM footage, once rendered). The chain lights up: Video, Detector, Tracker, Trajectories, Placement. 967 counts up. | Vision Lab · camera output · Camera lane · 967 tracks · 15-minute study · ◐ Approximated · ◌ Ungated | "A detector and a tracker turn fifteen minutes of video into nine hundred and sixty-seven tracks. That count is ungated." |
| 0:35 | 1050-1451 | SAM 3.1 | The real SAM 3.1 mask outlines of the 60 s window, played from `sam-masks.json`, in the model-output pink. Four results rise at right, including the one that didn't improve. | SAM 3.1 · Meta · 586 interpolated rows replaced · 7.7% → 2.7% interpolated share · 0.37 m → 0.29 m across-road error, median · 0.55 m → 0.58 m along-road error: no gain · ◇ Model output · ◌ Ungated · seeded from detector tracks · $0.32 | "On one sixty-second window, Meta's SAM 3.1 refines the tracks. It replaces five hundred and eighty-six interpolated rows and gives each vehicle its own mask." |
| 0:48 | 1440-1811 | Digital twin | Frame-stepped capture of the 3D twin at 4x: Oblique, Along Lankershim, Plan. The app's own title labels it. | (the app: Digital Twin · 4x) | "Video, tracks and analytics share one clock with a 3D twin of the junction, here at four times speed." |
| 1:00 | 1800-2021 | Analytics | Analytics page capture (counted tracks, Withheld entering flow, the turning-movement diagram and matrix), slow push toward the diagram. | (the app: Traffic Analytics · Turning movements) | "Turning movements, arm by arm, straight from the tracks." |
| 1:07 | 2010-2621 | The gate | Seven instruments. One amber LIMIT line is drawn first, top to bottom, through every row (each limit normalised to the same x). Bars then fill one by one; "at least" checks must cross the line, "at most" checks must stop short. Six lamps go green. The last bar creeps over the line and goes red. | Agreement gate · held-out · Seven checks. Limits first. · ● vs published NGSIM trajectories · 7 held-out blocks · one read · Recall 0.761 ≥ 0.70 · Matched to NGSIM 0.863 ≥ 0.80 · Across-road error p90 1.39 m ≤ 1.5 m · Lane agreement 0.881 ≥ 0.85 · Turning agreement 0.847 ≥ 0.80 · Median speed error 0.48 m/s ≤ 1.0 m/s · Entering count error 0.2033 ≤ 0.20 FAIL · over by 0.0033 · Six pass. One misses by 0.0033. | "Before a single held-out read, we committed limits for seven checks against the published NGSIM trajectories. Recall. Matching. Position. Lanes. Turns. Speed. Six pass. Entering counts miss, by zero point zero zero three three." |
| 1:27 | 2610-2891 | Withheld | Command center capture, pushing in on the Withheld figure; a card slides in at right with a red rule. | Entering flow · ⊘ Withheld · gate failed: 0.2033 > 0.20 · No value. The reason, in its place. | "So the product withholds entering flow and shows the reason in its place. The limit stays." |
| 1:36 | 2880-3071 | Evidence | Evidence page capture: the gate table (the withheld entering figures, the magnified miss), gliding down toward the decision. The evidence key rises along the bottom on a dark band. | ● Checked ◐ Approximated ◌ Assumed ◌ Ungated ▦ Simulated ◇ Model output | "Every headline figure carries its evidence, and its gate." |
| 1:42 | 3060-3521 | Muse Spark | The first hypothesis from `data/sample/diagnoses` types out under a pink model-output rule. Its four cited ids become chips that fly across and snap onto the 24 evidence ids of the record; each lands with a green tick. | Muse Spark · Meta · ◇ Model output · muse-spark-1.3 · h-ns-through · confidence low · CITES · EVIDENCE IDS · 24 · FROM THE RECORD · 3 runs · 0 citation problems · 0 unsupported numbers | "Meta's Muse Spark reads the record and writes hypotheses. Model output, labelled as such. Every citation is checked by code. Three runs, zero problems." |
| 1:57 | 3510-4001 | SUMO | A 3 x 80 matrix: candidates I1, I2, I3 against 20 seeds in each of 4 timelines. Cells light blue as runs land, then each row turns red and lists what it worsened (from `decision_summary.json`). | SUMO · decision lane · Muse proposes. SUMO tests. · ◇ 3 proposals → ▦ 20 seeds × 4 timelines each · I1 worsens 4 · I2 worsens 7 · I3 worsens 5 · inputs: published NGSIM trajectories, not camera output · baseline: 2 timelines partly validated, 2 not assessed | "Then Muse proposes signal changes. SUMO tests three candidates, twenty seeds, four timelines. Every candidate makes something worse." |
| 2:13 | 3990-4211 | Decision | Type only. | Decision · Nothing recommended. · ▦ every candidate worsened a metric / no validated baseline | "So CairoFlow recommends nothing." |
| 2:20 | 4200-4559 | Close (**Higgsfield H2**, optional) | "It tells you what it knows, / and what it doesn't." over the optional H2 shot, then the lockup. | It tells you what it knows, and what it doesn't. · CairoFlow · Built with SAM 3.1 and Muse Spark · github.com/AbdelrahmanYasser01/cairoflow · NGSIM data: U.S. DOT FHWA, CC BY-SA · Los Angeles, 2005 · decision support only | "It tells you what it knows, and what it doesn't. Built with SAM 3.1 and Muse Spark." |

The full-bleed app clips (twin, analytics, command center, evidence) carry no extra source tag: the app's
own page title labels them, and a tag on top collided with it. Captions are burned in for the voice-over
lines the screen doesn't already carry. They are off for the cold
open, the title, the place dateline, "and shows the reason in its place" (the Withheld card says it), the
gate's last line, the decision and the close.

## Voice-over script

Timed to the cut. Read at about 150 words a minute; the gaps are deliberate. Each line starts at the
time given and should end before the next. The same lines are the caption track in `video/src/script.ts`,
so if a line changes, change it there too.

```
0:01.2  Zero point two zero three three.
0:04.6  Our limit was zero point two zero.
0:07.0  So we didn't show it.
                                                   [beat]
0:11.0  This is CairoFlow. Traffic intelligence that shows its working.
0:16.6  One overhead camera. Los Angeles, June 2005.
0:20.2  Lankershim Boulevard at Universal Hollywood Drive.
0:24.6  A detector and a tracker turn fifteen minutes of video
0:29.4  into nine hundred and sixty-seven tracks. That count is ungated.
0:35.6  On one sixty-second window, Meta's SAM 3.1 refines the tracks.
0:40.2  It replaces five hundred and eighty-six interpolated rows
0:44.2  and gives each vehicle its own mask.
0:48.6  Video, tracks and analytics share one clock
0:53.8  with a 3D twin of the junction, here at four times speed.
1:00.5  Turning movements, arm by arm, straight from the tracks.
1:07.6  Before a single held-out read, we committed limits for seven checks
1:11.1  against the published NGSIM trajectories.
1:14.6  Recall. Matching. Position. Lanes. Turns. Speed.      [one word per bar as it fills]
1:21.4  Six pass. Entering counts miss, by zero point zero zero three three.
1:27.5  So the product withholds entering flow
1:31.9  and shows the reason in its place.
1:34.1  The limit stays.
1:36.4  Every headline figure carries its evidence, and its gate.
1:42.6  Meta's Muse Spark reads the record and writes hypotheses.
1:47.4  Model output, labelled as such.
1:52.0  Every citation is checked by code. Three runs, zero problems.
1:57.6  Then Muse proposes signal changes.
2:02.2  SUMO tests three candidates, twenty seeds, four timelines.
2:07.4  Every candidate makes something worse.
                                                   [beat]
2:13.8  So CairoFlow recommends nothing.
                                                   [beat]
2:20.8  It tells you what it knows, and what it doesn't.
2:26.4  Built with SAM 3.1 and Muse Spark.
```

Delivery: calm, low, unhurried; an engineer reading a log, not an announcer. No upward inflection on the
numbers. Let "So we didn't show it" and "recommends nothing" land flat, then leave a second of air.

To generate it rather than record it, any text-to-speech voice works; keep the line starts above and drop
the file in as `video/public/audio/voiceover.wav` (see [Audio](#audio)).

## Higgsfield shots

Both are optional. Without them the cut uses the abstract placeholder cards it renders today. Whatever goes
in, the frame keeps the "Illustrative, not data" label: these are not footage of the site and carry no
figures. No logos, no legible signs, no real brand names.

| Slot | Scene | On screen | Generate | Drop the file at |
|---|---|---|---|---|
| H1 | Place, 0:16 to 0:24 (frames 480-731) | 8 s | 10 s | `video/public/slots/h1.mp4` |
| H2 | Close, 2:20 to 2:26 (frames 4200-4367) | 5.5 s | 6 s | `video/public/slots/h2.mp4` |

`render.mjs` picks up whichever files exist, so dropping one in and rendering the scene's frames (see
[Fix one scene](#fix-one-scene-without-a-full-render)) is enough.

**H1, "Place".**

```
Subject: a wide multi-lane urban boulevard crossing a smaller street, Southern California, palm trees
silhouetted along the median, traffic signals on mast arms, cars moving through with headlights on.
Camera: slow aerial drone push-in with a gentle downward tilt, from about 120 m to 70 m altitude, one
continuous move, no cuts, no rotation.
Lighting: blue hour just after sunset, deep blue sky, warm sodium and LED street light pooling on the
asphalt, light trails, slight atmospheric haze.
Style: photoreal, cinematic, 35 mm lens look, fine film grain, muted colour with amber highlights.
No text, no logos, no legible signage, no people in close-up.
Duration: 10 seconds. Aspect ratio: 16:9, 1920x1080 or larger, 24 or 30 fps.
```

The left third of the frame sits under a dark gradient and the dateline, so keep the subject centre-right.

**H2, "Close".**

```
Subject: a single traffic signal head at night against a dark sky, cycling green to amber to red, light
rain haze, distant car lights as soft bokeh below.
Camera: locked-off tripod shot with a very slow push-in, about 5 percent over the shot.
Lighting: night, the signal lamps are the key light, deep shadows, wet reflections.
Style: photoreal, cinematic, shallow depth of field, fine grain. No text, no logos.
Duration: 6 seconds. Aspect ratio: 16:9, 1920x1080 or larger.
```

H2 sits under the closing line at heavy darkening; it fades out before the CairoFlow lockup.

## Music and audio

- Minimal, pulse-led electronic, 90 to 100 BPM, in a minor key: sub drone and a soft clock tick under the
  cold open (one tick per typed digit), a lift on the title, a steady pulse from the camera lane through the
  gate, near silence for "So we didn't show it" and again for "Nothing recommended.", a warm resolve on the
  closing line.
- Use a track you have the rights to (royalty-free library or commissioned) and credit it if required.
- Mix: voice-over around -16 LUFS integrated, music ducked 10 to 12 dB under the voice, true peak below
  -1 dBTP. Export AAC 320 kbps, 48 kHz.

## Audio

The render has no audio yet. To add a voice-over and music without touching the composition:

```bash
ffmpeg -i video/out/cairoflow-demo.mp4 -i voiceover.wav -i music.wav \
  -filter_complex "[2:a]volume=0.25[m];[1:a][m]amix=inputs=2:duration=first[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 320k -shortest video/out/cairoflow-demo-audio.mp4
```

## The NGSIM footage

The NGSIM camera video itself could not be fetched in this environment: the proxy blocks
data.transportation.gov. So the cut shows the Vision Lab with stored tracks (and the SAM scene draws the
stored masks) over an empty frame, never the pixels. On a machine with network access, from the repository
root (README "From a fresh clone"):

```bash
uv run --frozen python scripts/fetch_ngsim_data.py --download camera2
uv run --frozen python scripts/render_annotated_video.py --analysis data/sample/analyses/cv-ngsim-int2-15min-study
```

To drop it into the edit, copy the rendered MP4 (a 12 s or longer stretch is enough) to
`video/public/footage/annotated.mp4` and re-render the camera-lane frames (720-1061). The camera-lane scene
then shows the annotated footage instead of the Vision Lab clip, tagged "NGSIM camera · annotated · CC BY-SA
4.0". The footage is CC BY-SA 4.0, so the video keeps the "NGSIM data: U.S. DOT FHWA, CC BY-SA" credit, and
`video/public/footage` is Git-ignored, like every other copy of the pixels in this repository. The Vision Lab
also plays the footage once the server can serve it, so re-capturing after this step shows it in the app as
well.

## Re-capture and re-render

The UI is captured from a running server, so re-capture whenever the app changes.

```bash
# 1. Build the app and start the server (any free port; the capture defaults to 8105).
npm --prefix apps/web ci
npm --prefix apps/web run build
CF_DEMO_PORT=8105 bash scripts/demo_run.sh        # wait for "Uvicorn running"

# 2. In another shell: install the video package once, then capture every clip.
npm --prefix video ci
npm --prefix video run capture                    # every clip, frame-stepped (smooth at any load)
npm --prefix video run capture -- --screencast    # 2D pages in real time: faster, choppier when busy
CAPTURE_URL=http://127.0.0.1:8000 npm --prefix video run capture     # another server
npm --prefix video run capture -- --only=twin,command                # some clips only

# 3. Render, check, preview.
npm --prefix video run render -- --final          # video/out/cairoflow-demo.mp4, slot ids hidden
npm --prefix video run render                     # same, with the Higgsfield slot ids shown
npm --prefix video run render:draft               # half-size check render
node video/scripts/render.mjs --stills=150,2400   # single frames into video/out/stills/
npm --prefix video run sheet                      # video/out/contact-sheet.png, one frame every 6 s
npm --prefix video run studio                     # Remotion Studio, to scrub and edit
```

Remotion pipes JPEG frames, so its H.264 comes out tagged full range (yuvj420p). A full render therefore
ends with one re-encode to limited-range yuv420p (`--keep-range` skips it); patch renders are left as they
are, because the splice re-encodes the whole cut anyway.

What the capture does (`video/capture/capture.mjs`): it opens each page at 1920x1080, finds controls by
their accessible names (so a redesigned UI still captures; a missing control is logged and skipped), and
records:

| Clip | Page | Length | Moves | Used in |
|---|---|---|---|---|
| `twin` | `/twin` | 12.6 s | 4x, Oblique, Play; then Along Lankershim at 4 s, Plan at 8.5 s | Digital twin |
| `command` | `/` | 10 s | The page fits one screen; its camera-tracks replay keeps running | Withheld |
| `evidence` | `/evidence` | 11 s | Glides the gate table up, then slowly toward the decision; the scene uses 4.5 s to 10.9 s | Evidence |
| `analytics` | `/analytics` | 13 s | Glides to "Turning movements"; the scene uses 5 s to 12.4 s | Analytics |
| `vision` | `/vision` | 12 s | 15-minute study: Tracks, Play | Camera lane |
| `sam` | `/vision` | screencast only | The SAM 3.1 window: Masks, Play | Camera lane, if `vision` is missing |

With `--screencast` the 2D clips run 14 to 18 s (the same moves in real time, with pauses).

Frame-stepped means the page runs on virtual time (`performance.now`, `Date.now` and
`requestAnimationFrame`), advanced 1/30 s per screenshot, so the clip plays at true speed and stays smooth
however slow the machine is; on a busy 4-core machine it costs one to three seconds per frame, for the twin
and for the 2D pages alike. `--screencast` records the 2D pages through the DevTools screencast in real time
instead (each clip takes its own length plus encoding), at whatever frame rate the machine manages; the
twin is always frame-stepped. `CAPTURE_OUT` and `CAPTURE_WORK` send the clips and their frames elsewhere,
for a trial capture while a render is reading `video/public/captures`.

Clips land in `video/public/captures/` (Git-ignored) with `manifest.json`; `render.mjs` reads it and passes
the clips to the composition as props. A clip that is missing renders as a labelled empty frame, never a
failure.

Browser: `render.mjs` and the capture use the preinstalled headless Chromium when they find one
(`CHROME_PATH`, or `chromium_headless_shell-*` under `PLAYWRIGHT_BROWSERS_PATH`, default `/opt/pw-browsers`),
passed to Remotion as `--browser-executable`, so nothing is downloaded. Elsewhere Remotion fetches its own
headless shell on first render, and Playwright needs `npx playwright install chromium`. Keep Remotion's
concurrency at 2 on a shared 4-core machine: `REMOTION_CONCURRENCY=2` (the default is half the cores).

If `data/sample` changes, refresh the drawn data first: `node video/scripts/prepare-data.mjs`. If a figure
in README.md changes, change `video/src/facts.ts` and the storyboard above.

### Fix one scene without a full render

A full render takes 10 to 25 minutes on four idle cores, and much longer on a busy machine. To fix scenes
without that, re-render their frames and splice them into the last full render. The frame ranges are in
the storyboard (start time x 30; Remotion Studio shows them too). The app clips feed three ranges:
720-1061 (camera lane), 1440-2021 (twin and analytics) and 2610-3071 (withheld and evidence).

```bash
# After a re-capture: bundle once (the bundle carries its own copy of public/), then render the ranges.
node video/scripts/render.mjs --bundle
node video/scripts/render.mjs --final --entry=video/out/bundle --frames=720-1061  --out=video/out/p1.mp4
node video/scripts/render.mjs --final --entry=video/out/bundle --frames=1440-2021 --out=video/out/p2.mp4
node video/scripts/render.mjs --final --entry=video/out/bundle --frames=2610-3071 --out=video/out/p3.mp4
node video/scripts/splice.mjs video/out/p1.mp4 720 video/out/p2.mp4 1440 video/out/p3.mp4 2610
```

The splice re-encodes `video/out/cairoflow-demo.mp4` in place (`BASE=...` for another file,
`SPLICE_PRESET=veryfast` to re-encode faster). Ranges must not overlap. Each scene runs 12 frames into the
next for the crossfade, so two neighbouring scenes share those frames: render them as one range, as above.

## Honesty notes for whoever edits this next

- Entering flow is never shown as a value. 0.2033 is the gate's entering count error, the number that
  failed, which README.md and the app both print (the app as 20.33%). Keep "Withheld" and its reason
  together.
- SAM's along-road error got worse (0.55 m to 0.58 m) and stays on screen next to the gains. SAM was seeded
  from the detector's tracks, so agreement with them is a consistency check, not confirmation.
- 967 tracks is ungated and approximated, and is marked so.
- Muse output stays pink and labelled "Model output". The decision lane used the published NGSIM
  trajectories, not the camera output, and the SUMO scene says so.
- The gate graphic normalises each limit to one x position so the seven read against one line; the values
  and limits printed beside each bar are the README's.
