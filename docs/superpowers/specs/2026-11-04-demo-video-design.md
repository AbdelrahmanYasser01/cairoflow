# CairoFlow demo video: treatment, script and production design

Date: 2026-11-04. Deadline: submission closes Wed 4 Nov, 9 PM ET (per the event guide copy we
could read; the official Cerebral Valley page was unreachable from the build machine).

## Intent

A three-minute film that wins on the rubric judges score, without saying one thing the
[claims register](../../CLAIMS.md) does not support. The project's edge is epistemic: it is the
traffic AI that withholds a number when its own pre-registered check fails. The film is built
around that moment, because no other entry will have it.

## Who watches and what they score

| Criterion (weight) | What the judge needs to see | Where the film delivers it |
|---|---|---|
| Impact (30%) | Who benefits, why it matters, could people use it | Problem beat (cities act on counts; models don't flag their own errors); close: decision support for traffic engineers, runs offline from a fresh clone, no API key |
| Use of Meta AI tooling (25%) | Creative use beyond a basic integration, surprising capabilities | SAM 3.1 seeded with detector tracks as box prompts to get past its 16-object, first-frame limit; per-vehicle masks; Muse Spark with a reply schema that has no numeric field, citations checked by code; Muse proposals judged by validators and SUMO |
| Demo (25%) | Working, impressive, holds up live, video quality | Every product shot is the real app on committed records, captured frame-exact; cinema-grade motion design, VO, captions, sound |
| Technical depth (20%) | Sound engineering, real craft | Pre-registered gate, alternating held-out blocks, one blind read, typed contracts where a failed metric cannot carry a value, 20 paired SUMO seeds |

## Goals, non-goals, anti-goals

- Goals: hook in the first five seconds; one clear story; every Meta model shown doing real work;
  legible with the sound off (burned-in captions); 1080p, at most 3:00.
- Non-goals: explaining every check, the bake-off, the World Model rebuild, live analysis.
- Anti-goals (a failure even if it looks good): any overclaim. Never "accuracy" (say
  "agreement"), never "real-time" or "live" about the replayed records, nothing about Cairo
  traffic, no claim that SAM raised recall, no recommendation, no footage pixels (none are in the
  repository), no AI-generated imagery passed off as the product or the site.

## Constraints found in research

- Footage cannot be fetched on the build machine (the NGSIM host is blocked) and is not in the
  repository by policy. The film uses the product's own track playback and twin, which is also
  what a fresh clone shows a judge.
- `scripts/release_scan.py` allows no pixel files outside `apps/web/public/` and no new top-level
  directories, so the video project lives in `apps/demo-video/` as source only; captures, audio
  and renders are generated into Git-ignored folders.
- Remotion renders with the pre-installed Chromium; the twin renders under SwiftShader, so
  product shots are captured frame by frame with Playwright's fake clock, not screen-recorded.

## Approaches considered

1. **Screen walkthrough with voiceover.** Record the app in real time, narrate over it. Fast and
   honest, but software-GL playback stutters, and it looks like every other entry.
2. **Cinematic hybrid (chosen).** Frame-exact captures of the real app, cut with motion graphics
   built from the same records (the gate's actual values, the actual counts), composed in
   Remotion, with synthetic voice, an original score and sound design. Every pixel is the product
   or a chart of its own numbers. Costs more build time, which the deadline allows if scoped.
3. **AI-generated b-roll (Higgsfield or similar).** Striking, but invented streets and cars in a
   film about refusing to show unsupported numbers would undercut the thesis, and no Higgsfield
   access exists on the build machine. Not used.

## Story

Cold open on the withheld number, then rewind through how it was earned, land on "recommends
nothing" as a feature. Three acts: the refusal (0:00 to 0:20), the machine (0:20 to 2:36), the
verdict (2:36 to 3:00).

## Script and shot list

Timings are targets; the edit is cut to the generated voiceover. Every line maps to a claims row.

| # | Time | Picture | Voiceover | Claim it rests on |
|---|---|---|---|---|
| 0 | 0:00 | The app's twin, full-bleed under a gradient; CairoFlow wordmark, the double amber centreline, "Meta Global AI Developer Hackathon 2026 · SAM 3.1 · Muse Spark" | CairoFlow turns one traffic camera into tracked vehicles, turning counts, and a 3D twin of the junction. | README first paragraph (the decision is not claimed as camera output) |
| 1 | 0:08 | Black. A counter climbs to 20.33 %, a limit line at 20 %, the lamp goes red. The number dissolves; "Entering flow: Withheld" | Twenty point three three percent. That's the error in our entering counts. Our limit was twenty, and we fixed it before we looked. So CairoFlow refuses to show you those counts. | Gate row, entering count error 0.2033, limit 0.20, failed; what it covers (entering flow and per-arm entering counts) is withheld. The error itself is shown, so the line must not say the 20.33 % is hidden |
| 3 | 0:18 | Command page, slow push on "Lankershim Blvd" | Cities retime signals and rebuild junctions from traffic counts, and computer vision can now take those counts from ordinary video. But a model won't tell you when it's wrong. So we built one where every figure has to earn its place. | Framing, no product claim |
| 4 | 0:36 | Vision Lab track playback; tracks table; SAM window with masks | One overhead camera: Lankershim Boulevard, Los Angeles, 2005. A local detector and tracker follow the vehicles through fifteen minutes. SAM 3.1, asked for "car", followed at most sixteen, and none that entered after the first frame. So on one sixty-second window, we seeded it with our tracks as box prompts. It agreed with 9,900 of 10,858 detector rows, a consistency check, not proof, and drew masks for the vehicles it followed. | SAM rows; README "How the Meta models are used" (909 rows were not reached, so not "a mask for each vehicle") |
| 5 | 1:02 | Digital Twin, Along Lankershim, select a vehicle, Plan and Geo | The same record drives a 3D twin of the junction, on one shared clock. Click any vehicle to read its record. Every approximation is labelled on screen. | DEMO.md 1:00 to 1:15; twin limits |
| 6 | 1:16 | Analytics: counted tracks, turning diagram, movement table | 967 counted tracks become turning movements, set beside the published reference. North to south: 302 against 323. | Analytics page values |
| 7 | 1:28 | Gate sequence: alternating blocks graphic, then the gate table, rows lighting green, one red | Then comes the part most demos skip. Before the test, we committed seven limits. We tuned only on alternating minutes of footage, then scored the held-out minutes in one blind read, against the published NGSIM trajectories. That's agreement, not accuracy. Six checks passed. Entering counts came in at twenty point three three percent error. The limit was twenty. | Gate row; footnote on screen: an earlier read died with no result, gate v2 kept identical limits; placement not held out |
| 8 | 1:58 | Withheld panel and its reason | A miss of 0.0033. We didn't move the line. So the fifteen-minute study shows no entering flow at all, only the reason. A withheld value is absent, not zero: the data contract won't let a failed metric carry a number. | Metric contract row; README (SAM window keeps its own ungated entering figures, so "the study", not "anywhere") |
| 9 | 2:16 | Muse's reading, hypotheses | Muse Spark reads that evidence and writes hypotheses for a person to test. Its reply schema has no numeric field, and code checks every citation. All three replies passed. None cited the withheld counts. | Muse rows |
| 10 | 2:34 | Decision: candidates, "Partially validated", "Nothing is recommended" | For the decision, SUMO tested three of Muse's proposed signal changes over twenty paired seeds. In simulation, every one made something worse, and no baseline is validated. So CairoFlow recommends nothing, and shows you why. | "Recommends an intervention: NOT CLAIMED"; footnote: decision lane uses published NGSIM data, not this camera |
| 11 | 2:50 | End card: logo, tagline, open source, offline from a fresh clone, Meta models used, NGSIM credit | CairoFlow. It tells you what it knows, and what it doesn't. | DEMO.md close |

## Visual language

The app's own design tokens (`apps/web/src/design/tokens.css`): asphalt near-black, lane-paint
white type, signal amber as the one accent, green and red only for gate meaning, the evidence
colours only for evidence. Barlow Semi Condensed for display, IBM Plex Mono for data. Product
shots sit in a thin-ruled frame on the asphalt ground with slow, eased camera moves (no shake, no
3D flips). Callouts are hairline boxes and amber underlines, the app's own grammar. Captions sit
low, in Barlow, on a translucent asphalt band.

## Sound

- Voice: Kokoro (local, Apache-2.0 weights), voice `af_heart`, calm documentary pace.
- Score: original, synthesised in code (no licensing risk): a slow D-minor pad, sub-bass and a
  soft pulse that builds into the gate, cuts to near silence on the red lamp, and resolves under
  the decision and close. Ducked under the voice.
- Effects, synthesised: soft ticks as gate rows pass, a low hit on the failed row, airy
  transitions.

## Deliverables

- `CairoFlow-demo.mp4`, 1920x1080, 30 fps, H.264 and AAC, at most 3:00, captions burned in.
- `CairoFlow-demo.srt` captions for the upload platform.
- `apps/demo-video/`: the Remotion project and the scripts that regenerate every capture, voice
  and score track, so the film can be rebuilt from a clone.

## QA checklist

- Runtime at most 3:00; no black or frozen frames; audio peaks below -1 dBFS; voice intelligible
  over the score.
- Every spoken and on-screen figure matches the app and the claims register.
- No "accuracy", "real-time", "live", Cairo traffic claim or recommendation.
- Credits: Meta SAM 3.1 and Muse Spark; NGSIM data credit (U.S. DOT FHWA, CC BY-SA 4.0) for the
  derived tracks shown; voice model credit.

## What the claims pass changed

A line-by-line check of the first cut against the claims register changed three things: the cold
open said CairoFlow "refuses to show you this number" over the 20.33 % error, which the app does
show, so it now names the withheld entering counts; "a mask for each vehicle" became "masks for the
vehicles it followed"; and the Muse chip now says code flags unsupported numbers rather than that
they "must match". The contract code on screen is an exact excerpt with elisions marked.

## What the research pass changed

Devpost's demo-video guidance asks for the product working on screen within the first 10 to 15
seconds, a statement of what the app does in the first few seconds, and no title screens. The
first cut showed no product until 0:20 and spent five seconds on a title card. The cut now opens
on the app's twin with the wordmark over it and a line saying what CairoFlow does, then the cold
open; the title card is gone. Higgsfield was checked rather than assumed: no Higgsfield tool or
connector exists in the build session, no credentials are set, and every Higgsfield host fails
DNS from the build machine, so it could not be used even through its published SDKs.

## What the judge review and the claims verification changed

An independent reviewer, given only dense frames, the transcript and the rubric, scored the first
cut 5.85/10 (Impact 5, Meta tooling 5, Demo 6, Technical depth 8) and listed unclear and
unsupported lines. A verification workflow then had four refuters check every line against the
README, this register and the stored Muse replies. Changes that came out of the two passes:

- The six passing checks are said to be pooled, with the block-by-block misses on screen; the
  reason it is agreement rather than accuracy is said aloud; "tuned only" became "tuned the
  detector and tracker", since the placement was not held out; "the part most demos skip" became
  "Then comes the gate".
- The decision line says it runs on published NGSIM data, not this camera; the cold open says how
  far the count was from the published reference, and that the withholding is in this study.
- The SAM agreement uses the register's own wording ("9,900 of 10,858 observed rows ... not
  independent confirmation"); SAM's 586 replaced rows are shown on the app's own panel with
  "542 of 586 either way"; one Muse hypothesis is shown word for word, labelled model-generated
  with its own confidence; the weak "none cited the withheld counts" line was dropped.
- The analytics line uses the team's vetted "801 against 911" across all twelve movements, since
  only 801 of the 967 counted tracks are in the turning matrix; the 967 chip says "ungated".
- The opening says "recorded traffic video" and "approximate 3D twin"; the close names the user,
  decision support for traffic engineers.

The workflow's completeness critic then caught four more, all fixed: the film narrates the error
in percent, so the miss is "a third of a percentage point", not a spoken "0.0033"; the limit was
fixed "before the held-out read", not "before we looked", since held-out frames were used to fit
the placement; the study shows "no entering-flow figure", since turning-matrix row sums stay
visible; and "every figure has to earn its place" became "every headline figure shows its gate
status", since some figures on screen are ungated. The decision line says "rather than our camera
output" (the NGSIM data comes from the same video), the seeds are said to vary simulator noise,
not demand, and the pull-quote notes that "validated" is the model's word.

The final cut runs 2:58, with the voice at 1.08x.

## Calls made without the user (the brief asked for autonomy)

- Hybrid approach and no AI-generated b-roll, for the reasons in Approaches.
- Synthetic voice rather than a human read, given the deadline; a human re-read can replace the
  track without re-editing, because every scene is timed from its own audio file.
- English only; 1080p30.
