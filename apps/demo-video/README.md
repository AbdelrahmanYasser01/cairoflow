# CairoFlow demo film

The three-minute submission film, as a [Remotion](https://www.remotion.dev) project. It is cut to its
own voiceover: each scene lasts its voice track plus a hold, so a new read re-times the edit.
The treatment, script and the claim behind every line are in
[the design doc](../../docs/superpowers/specs/2026-11-04-demo-video-design.md).

Every product shot is the real app on its committed records, and every figure on screen is the
product's own. No footage is shown: the repository holds none, and the film uses the app's track
playback and twin, as a fresh clone does.

## What is committed

Source only: `src/` (the composition), `scripts/` (capture, voice, score, captions) and
`scripts/script.json` (the narration, with what is said and what is captioned). Captures, audio
and renders go to `public/`, `.frames/` and `out/`, which Git ignores: `scripts/release_scan.py`
allows no pixel files outside `apps/web/public/`.

## Rebuild the film

From the repository root, with the web app built (`npm --prefix apps/web run build`):

```bash
bash scripts/demo_run.sh
```

Then, in `apps/demo-video`:

```bash
npm install
node scripts/capture-stills.cjs
node scripts/capture-motion.cjs
```

The twin renders at about one frame per second under software GL, so the motion captures run the
page in slow motion and retime the frames to 30 fps; the four twin shots take about 15 minutes.

Voice and score need Python 3.12 with `kokoro-onnx`, `soundfile`, `numpy` and `scipy`, and the
Kokoro v1.0 model files (`kokoro-v1.0.onnx`, `voices-v1.0.bin`, from the kokoro-onnx releases):

```bash
python scripts/voice.py --model kokoro-v1.0.onnx --voices voices-v1.0.bin --out public/audio
python scripts/score.py --out public/audio
python3 scripts/srt.py
```

Render, pointing Remotion at a Chrome headless shell:

```bash
npx remotion render CairoFlowDemo out/CairoFlow-demo.mp4 --codec=h264 --crf=17 --audio-bitrate=320k --browser-executable=/path/to/chrome-headless-shell
```

Then master the sound: `scripts/master.py` rebuilds the mix in floating point from the stems on the
same timeline (so coincident voice and score peaks can't clip), normalises it to -16 LUFS with a
-1.5 dBTP ceiling, and copies the picture stream:

```bash
python scripts/master.py --video out/CairoFlow-demo.mp4 --out out/CairoFlow-demo-final.mp4
```

`out/CairoFlow-demo-final.mp4` is the submission file; `out/CairoFlow-demo.srt` carries its captions.

## Credits and licences

- Derived tracks and analytics shown: adapted from the NGSIM Lankershim Boulevard data, U.S.
  Department of Transportation Federal Highway Administration, CC BY-SA 4.0
  (`data/sample/LICENSE.md`). The film is shared under CC BY-SA 4.0 for that reason.
- Voice: synthetic, Kokoro v1.0 (Apache-2.0 weights), voice `af_heart`.
- Score and effects: original, synthesised by `scripts/score.py`.
- Fonts: Barlow, Barlow Semi Condensed (OFL) and IBM Plex Mono (OFL), as in the web app.
