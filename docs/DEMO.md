# Demo walkthrough

Two paths. **Primary:** precomputed analysis, fully offline. **Secondary:** a new analysis of a video file on the machine, with the vision extra and the source video installed.

Start the app with `bash scripts/demo_run.sh`, then follow the script. It runs about three minutes.

The produced submission film tells the same story and is built from this app in [`apps/demo-video`](../apps/demo-video/README.md); its treatment and script are in [the film's design doc](superpowers/specs/2026-11-04-demo-video-design.md).

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00 | Title | | One recorded camera. What can it actually prove? |
| 0:10 | Vision Lab, Footage | Play the annotated video at a busy moment | This is real footage: Lankershim Boulevard in Los Angeles, filmed in 2005. Thick boxes are detector output, thin ones are interpolated across missed detections, and every number is a track. |
| 0:25 | Vision Lab | Switch overlay to Tracks | We started with SAM alone, and it found under half the vehicles. So a local detector and tracker find and follow them, and on one 60-second window SAM refines those tracks. |
| 0:40 | Vision Lab, hero analysis | Pick the SAM analysis, show Masks | Seeded with those boxes, SAM agreed with 9,900 detector rows and put its own box on 586 interpolated ones. It didn't improve recall itself. We ran a control and we say so. |
| 0:55 | Vision Lab | Switch back to the 15-minute study | Now back to the full fifteen minutes. |
| 1:00 | Digital Twin | Same moment, Along Lankershim view | The same clock drives this twin, built from the same record, with the approximations labelled. |
| 1:15 | Digital Twin | Select a vehicle, then switch to Geo | Click any vehicle and read its record. Here are fifteen minutes of occupancy, about a thousand tracks, in approximate ground coordinates. |
| 1:35 | Traffic Analytics | Show the turning matrix | Camera counts beside the published reference, movement by movement. North to south, 302 against 323. Across all twelve movements, 801 against 911. |
| 1:50 | Evidence & Reports, gate table | Pause on the failed row | We committed seven limits, then scored the held-out blocks once. An earlier read died with no result, so a second gate with identical limits took the only score. The detector and tracker were tuned on other footage, but the camera placement wasn't held out. Six passed. Entering counts came in at 0.2033 against a limit of 0.20. |
| 2:12 | Command Center | Show Withheld | We didn't move the limit. So there's no entering flow figure here, only the reason. That's a miss of 0.0033, and the product treats it as a miss. |
| 2:25 | Evidence & Reports, model reading | Show Muse hypotheses | Muse Spark reads the evidence and proposes hypotheses. Code checks that every citation exists and that numbers in the hypotheses come from the evidence. All three replies passed, and none cited the withheld counts, which were blank and off limits in the prompt. |
| 2:42 | Evidence & Reports, decision | Show the decision | SUMO tested three interventions over 20 seeds. In simulation, every one made something worse, and no baseline is validated, so CairoFlow recommends nothing. That decision came from published data, not this camera. |
| 2:56 | Title | | CairoFlow. It tells you what it knows, and what it doesn't. |

## Live run

Use this if there's time, or if a judge asks whether it's real.

1. In Vision Lab, open Live analysis.
2. Choose the camera-2 source, a window such as 970 to 985 seconds, and stride 1.
3. Run it. Progress and the preview image come straight from the worker: frames, detections, tracks. This is a batch job on a file, not a stream, and it runs neither SAM nor the gate.
4. When it finishes, the whole app switches to the new analysis, with every figure ungated.

Typing a window inside a held-out block shows why it's refused and offers valid ones. That's the rule behind the gate, working in the product.

## If something goes wrong

- **No GPU or vision extra:** stay on the precomputed path, which needs neither.
- **No footage:** the player explains how to fetch it and keeps playing tracks on a canvas.
- **Twin looks blank:** the browser tab must be visible, because a hidden tab stops drawing.
- **Network down:** nothing in the primary path needs it.

## What the footage needs

Any recording that shows the footage is an adaptation of the NGSIM video, so it must carry this credit, and be shared under CC BY-SA 4.0:

> U.S. Department of Transportation Federal Highway Administration. (2016). Next Generation Simulation (NGSIM) Program Lankershim Boulevard Videos. [Dataset]. Provided by ITS DataHub through Data.transportation.gov. Accessed 2026-10-27 from http://doi.org/10.21949/1504477. Licensed CC BY-SA 4.0. More: https://ops.fhwa.dot.gov/trafficanalysistools/ngsim.htm

Submission form text: [SUBMISSION.md](SUBMISSION.md).
