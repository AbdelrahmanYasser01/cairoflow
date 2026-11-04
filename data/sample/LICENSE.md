# Licence of the sample data

Everything under `data/sample/` is derived data, shared under the Creative Commons
Attribution-ShareAlike 4.0 International licence (CC BY-SA 4.0):
https://creativecommons.org/licenses/by-sa/4.0/legalcode

The MIT licence in `LICENSE` covers CairoFlow's source code only, not this data.

## Attribution

U.S. Department of Transportation Federal Highway Administration. (2016). Next Generation
Simulation (NGSIM) Program Lankershim Boulevard Videos. [Dataset]. Provided by ITS DataHub through
Data.transportation.gov. Accessed 2026-10-27 from http://doi.org/10.21949/1504477. Licensed
CC BY-SA 4.0. More: https://ops.fhwa.dot.gov/trafficanalysistools/ngsim.htm

The vehicle trajectories come from the same programme: U.S. Department of Transportation, Federal
Highway Administration, Next Generation Simulation (NGSIM) Lankershim Boulevard vehicle
trajectories, data.transportation.gov dataset 8ect-6jqj (DOI 10.21949/1504477).

## Why 4.0

The source trajectories are licensed CC BY-SA 3.0 Unported and the source videos CC BY-SA 4.0.
Section 4(b) of CC BY-SA 3.0 permits an adaptation to be distributed under a later version of the
licence with the same licence elements, so what is derived from either source is shared here under
CC BY-SA 4.0. This is the project's own reading of those terms, not legal advice.

## Changes made

Nothing here is the published data as it was released. Compared with the sources:

- `analyses/ref-ngsim-int2-*/`: the published trajectory rows of intersection 2, cut to a time
  window, moved from State Plane feet to local metres around the junction, with speed and heading
  computed from consecutive frames, vehicle classes inferred from published lengths, values
  rounded to the fixed-point columns of `trajectories.bin`, and counts per movement and per minute
  computed by fixed rules (`scripts/build_reference_analysis.py`).
- `corridor_summary.json`: per-intersection row and vehicle counts, centroids and mean moving
  speeds counted from the published trajectories (`scripts/build_corridor_summary.py`).
- `decision_summary.json`: figures copied by code from the project's records of its simulations
  of intersection 2 (`scripts/build_decision_summary.py`).
- `checksums.json`: a checksum of each analysis's decoded trajectories.
- `analyses/cv-ngsim-int2-15min-study/`: tracks computed from the camera 2 video, 120-1020 s after
  08:30, by the stages its record names, placed on the ground through a camera homography, then
  reduced to what the gate `cv-int2-v2` allows: a value that failed a check is removed. Its
  detector was the optional AGPL-3.0 model named in its record (see `NOTICE`); neither that model
  nor its weights are distributed here. Its annotated video is not distributed either.
- `analyses/cv-hero-sam/`: tracks computed the same way from 960-1020 s, with the same detector,
  then refined with SAM 3.1. No gate has scored it. `sam_outlines.json` holds SAM's masks reduced
  to simplified polygons in image coordinates: model output, and no pixels.

## Model-generated content

Output of Meta's SAM 3.1 and Muse Spark included in this data or in the records it cites is
model-generated: it was produced through the Meta Model API by CairoFlow's requests. The hypotheses
and candidates in `decision_summary.json` and everything in `diagnoses/` but the cited evidence are
Muse Spark output and are labelled as such. Model output is not ground truth and is never presented
as a measured result. SAM output is acknowledged as SAM 3.1 output wherever it is published.

## No footage

No video, frame, overlay or other image of the footage is distributed in this repository. To fetch
the source video from its publisher:

    uv run --frozen python scripts/fetch_ngsim_data.py --download camera2
