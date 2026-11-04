# Submission text

A draft for the hackathon submission form. Every figure here also appears in the [README](../README.md), the source of truth. If one changes, change both.

## Name and tagline

**CairoFlow:** traffic intelligence that shows its working.

## Short description (about 50 words)

CairoFlow turns one overhead traffic video into tracked vehicles, turning counts and a 3D twin of the junction. It uses SAM 3.1 and Muse Spark. Every headline figure is scored against limits fixed in advance, on footage it was never tuned on. When a figure fails its check, the app withholds it and shows why.

## What it does

Traffic engineers are often handed camera analytics as confident numbers, with no way to tell which ones hold up. CairoFlow is built the other way round. It runs a recorded overhead video through detection, tracking, camera placement and analytics. Then a gate compares each headline figure with published NGSIM vehicle trajectories, on held-out blocks of footage, against limits committed before the first read.

On Lankershim Blvd (Los Angeles, 2005), 6 of 7 checks passed: recall 0.761, 0.863 of track rows matched to a published vehicle, across-road error 1.39 m (90th percentile), lane agreement 0.881, turning agreement 0.847 and median speed error 0.48 m/s. The entering count error was 0.2033 against a limit of 0.20. The limit stayed where it was, so the app withholds entering flow and shows the reason in its place.

## How it uses Meta's models

- **SAM 3.1** refines the detector's tracks on a 60-second window. Seeded with box prompts, it agreed with 9,900 of 10,858 detector rows. It replaced 586 interpolated rows with its own boxes, cutting interpolated rows from 7.7% to 2.7%. Its per-vehicle masks corrected a bus the detector had called a truck. We ran a control and report that SAM didn't raise recall itself.
- **Muse Spark** reads the checked evidence and writes hypotheses for an engineer to test. Its reply schema has no numeric field, and code checks that every cited evidence id exists and that every numeral matches an evidence value. In a separate chain, Muse proposes signal-timing changes. Deterministic validators check each one, and SUMO tests them over 20 paired seeds across four signal timelines.

## The decision

Each of the three candidates worsened at least one metric, and no baseline is fully validated. So CairoFlow recommends nothing, and says why. That decision used the published NGSIM inputs, not this camera's output, and the app says so.

## How we built it

Python (FastAPI, Pydantic contracts, SUMO), a Vite + React + TypeScript web app with React Three Fiber for the twin, and about 1,800 automated tests. Model replies are cached by request hash, so the whole demo replays offline with no API key. Meta API spend stayed within a $3.00 project cap, with every paid request recorded in `experiments/records`.

## Challenges

SAM alone, asked for "car", found under half the vehicles. So the detector's tracks seed SAM instead. An early held-out read died with its session and produced no result. A second gate with identical limits took the single read, and both are recorded.

## Honest limits

One site, one camera, 2005. No Cairo data, despite the name. Agreement with NGSIM isn't accuracy against independent labels. The camera placement wasn't held out. See [Limits](../README.md#limits).

## What's next

Counterfactual scenarios (demand growth, lane closures, turn bans), a second site with independent labels, and live control through TraCI.

## Demo video

About two and a half minutes at 1080p, cut from Playwright captures of the real app with Remotion. Captions are burned in, so it works muted. The source is in `video/`, and the storyboard, voice-over script and optional Higgsfield shot prompts are in [VIDEO.md](VIDEO.md).

## Attribution (needed on any video that shows the footage)

> U.S. Department of Transportation Federal Highway Administration. (2016). Next Generation Simulation (NGSIM) Program Lankershim Boulevard Videos. [Dataset]. Provided by ITS DataHub through Data.transportation.gov. Accessed 2026-10-27 from http://doi.org/10.21949/1504477. Licensed CC BY-SA 4.0.
