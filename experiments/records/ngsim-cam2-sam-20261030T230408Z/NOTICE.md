# SAM 3.1 review overlay on NGSIM camera 2 (CC BY-SA 4.0)

`overlay.mp4` (SHA-256 `7d3c31f9f322beedefa4178431df5372ff533ec11ed834951485616fc963eed4`) is an adaptation of the NGSIM Lankershim camera 2 raw video, 08:30-08:45, source seconds 970-1000.

**Source and attribution.** U.S. Department of Transportation Federal Highway Administration. (2016). Next Generation Simulation (NGSIM) Program Lankershim Boulevard Videos. [Dataset]. Provided by ITS DataHub through Data.transportation.gov. Accessed 2026-10-27 from http://doi.org/10.21949/1504477. Licensed CC BY-SA 4.0. More: https://ops.fhwa.dot.gov/trafficanalysistools/ngsim.htm

**Licence.** Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0), https://creativecommons.org/licenses/by-sa/4.0/legalcode. This overlay is shared under the same licence, as the account holder decided on 2026-10-28 (D3).

**Changes.** Trimmed to 10 s, re-encoded to H.264 at 10 fps (scripts/ingest_clip.py), then drawn over: boxes, track ids, trails and captions from SAM 3.1 output turned into tracks by CairoFlow's procedure (scripts/run_sam_cam2_excerpt.py). The drawing is model output, not ground truth, and it is not used by the intersection 2 world model (ADR-018, ADR-019).
