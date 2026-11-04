# video/

The CairoFlow demo video: a [Remotion](https://www.remotion.dev) composition plus a Playwright capture
of the running app. Its own package; nothing here is imported by the app.

```bash
npm --prefix video ci
npm --prefix video run capture     # needs a server: CF_DEMO_PORT=8105 bash scripts/demo_run.sh
npm --prefix video run render      # video/out/cairoflow-demo.mp4
```

Storyboard, voice-over, Higgsfield prompts and every command: [docs/VIDEO.md](../docs/VIDEO.md).

| Path | What's there |
|---|---|
| `src/facts.ts` | Every figure on screen, copied from README.md and `data/sample` |
| `src/script.ts` | Scene order and lengths; the voice-over, which is also the caption track |
| `src/scenes.tsx` | The fourteen scenes |
| `src/primitives.tsx` | Type-on, counters, evidence glyphs, app-capture frame, Higgsfield slot |
| `src/generated/` | SAM 3.1 mask outlines from `data/sample` (`npm run prepare-data`) |
| `capture/capture.mjs` | Records the app's pages into `public/captures/` (Git-ignored) |
| `scripts/` | Render, splice one scene back in, contact sheet |

Remotion is free for individuals and companies of up to three people; larger organisations need a
company licence (see remotion.dev/license).
