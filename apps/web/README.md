# CairoFlow web

The instrument for reading one intersection: Vite, React, TypeScript strict, Tailwind CSS 4,
motion. It holds no metrics of its own. Until an analysis is loaded, every page shows a deliberate
empty state, and every number on screen is counted from data.

## Run

Node 24.

```sh
cd apps/web
npm ci --ignore-scripts      # exact versions from package-lock.json
npm run dev                  # http://localhost:5173, /api proxied to http://localhost:8787
uv run --frozen python -m cairoflow_api.serve --port 8787   # the API, from the repo root
npm run typecheck
npm test
npm run build                # writes dist/
```

No package here needs an install script, so `--ignore-scripts` is the normal way to install.

`dist/` uses relative asset URLs and external module scripts only, so `script-src 'self'` holds.
Serve it with a fallback to `index.html` for the routes below. Routes are one level deep, which
is what keeps the relative asset URLs valid on a deep link.

Fonts come from `@fontsource` packages and are bundled, so the app makes no CDN requests.

## Routes

| Path | Page |
| --- | --- |
| `/` | Command Center |
| `/intersections` | Intersections |
| `/vision` | Vision Lab |
| `/twin` | Digital Twin |
| `/analytics` | Traffic Analytics |
| `/evidence` | Evidence & Reports |
| `/_design` | Design sheet, development builds only: every primitive and the evidence grammar |

In development, add `?state=none`, `?state=loading` or `?state=error` to a page URL to review its
data states.

## Live analysis

Vision Lab's Live analysis mode runs a real job. The API needs a vision environment, and the worker
needs a detector:

```sh
CAIROFLOW_VISION_PYTHON=$HOME/.venvs/cairoflow-vision/bin/python CAIROFLOW_DETECTOR=visdrone-yolo26s \
  uv run --frozen python -m cairoflow_api.serve --port 8787
```

Pick a source or upload a video, set a window and a frame stride, and start. Progress, the annotated
frame and every figure beside it come from the worker's event stream; nothing moves between events.
A camera 2 window that reaches an odd 60-second block is refused by design, and the form says which
windows can run. On success the new analysis loads across the app. A run from an upload is
uncalibrated: it stays in image pixels and the twin can't place it.

When an analysis's video isn't on the machine, Vision Lab shows the command that rebuilds it and
keeps playing the stored tracks on the shared clock.

## Vision Lab views

Footage and Geospatial (`/vision?layer=geo`) read one clock and one analysis. In Geospatial the 3D
layer takes the stage and the footage stays mounted beside it, so the one transport moves both. An
uncalibrated run has no ground, so it is not offered. Every run opens on its busiest frame inside a
measured window, chosen from the track spans (`openingFrame` in `src/domain/footage.ts`).

An analysis with a `sam_outlines.json` artifact adds a Masks overlay. Outlines exist on sampled frames
only: a frame shows the nearest sample within half the interval and names it, or shows nothing.

`src/domain/samMeasured.json` holds the few figures that only the measured records have: neither the
analysis record nor the API serves them. `sam.test.ts` holds it to the source files by hash and by
value. If a figure reaches the record, read it there and drop it from the extract.

## Recording

Nothing here scales past 1440: at a 1920x1080 window the pages end well above the fold. Record at a
1440x810 viewport with a device pixel ratio of 1.333, which outputs 1920x1080 with all type a third
larger and no code change.

## Layout

- `src/design/`: tokens (`tokens.css`), the Tailwind mapping (`theme.css`), the evidence grammar
  (`grammar.ts`) and the primitives. Start at `index.ts`.
- `src/shell/`: rail, bottom bar, context strip, route table.
- `src/state/analysis.tsx`: the active analysis. It opens on the shipped 15-minute study, or the one
  remembered this session, and the strip, pages and twin all follow it. Reference analyses stay
  selectable, listed last.
- `src/state/resources.ts`: the site-wide records beside it: decision, corridor, footage, health.
- `src/state/live.tsx`: the live job. It sits above the routes, so a job is still followed, and its
  analysis still adopted, whichever page is open.
- `src/domain/`: the site register, plan geometry, the provenance chain, the held-out rule, and
  vocabulary copied from `packages/contracts`.
- `src/instruments/`: the instruments that read data: site plan, movement ribbons, flow chart,
  provenance chain, clip and track stages, live analysis, pipeline receipts, model hypotheses.
- `src/pages/`: one composed layout per route. Data blocks go through `DataSlot`, which owns the
  empty, loading and error states; pass the ready view as its children.
- `src/twin/`: the 3D twin. `TwinStage` fills its parent and places its own instruments;
  pass `chrome={false}` only when a page places them itself.

## Rules for this app

- No number is typed into a component. Count it from data, or show `Absent` with a reason.
  `domain/truth.test.ts` fails on a hard-coded metric in a page or instrument.
- A value that failed its gate is withheld, not missing: `instruments/Withheld.tsx` puts the word
  where the number would be, with the measured value, the limit and that the limit came first. It
  never shows a substitute, and a reference count never stands in for this analysis's value.
- Each evidence kind keeps its one glyph, colour and label. `grammar.test.ts` checks the kinds
  against `EvidenceKind` in the contracts.
- Green and red mean a gate result and nothing else. Amber is the one interface accent.
- The plan is drawn from the site's lanes and carries `approximated` and `assumed` badges. Where
  no site geometry loaded, the schematic stands in and carries an `assumed` badge alone.
- Only one site exists: NGSIM Lankershim Blvd at Universal Hollywood Dr, Los Angeles, 2005.
  Nothing here may imply Cairo data.
