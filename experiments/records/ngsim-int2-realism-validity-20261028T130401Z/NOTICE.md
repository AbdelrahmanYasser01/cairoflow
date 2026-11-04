# Replay baseline validation, NGSIM intersection 2 (2026-10-28)

`validation/` was written by `scripts/validate_ngsim_int2_baseline.py --baseline experiments/runs/ngsim-int2-realism-baseline-20261028T130401Z --out <dir>` at commit `7b75e1a`. It was copied here unchanged from `experiments/runs/ngsim-int2-realism-validation-20261028T130401Z/` (Git-ignored).

**What it validates.** The replay baseline of ADR-022 (summary: `experiments/records/ngsim-int2-realism-baseline-20261028T130401Z.json`):
- signals replayed over clip 118–751 s;
- observed vehicles as demand;
- 20 seeds.

The observed window is clip [118, 751) s and the simulated window [0, 633) s. Both sides use the same world-model rules. Each simulated vehicle is paired with the observed track it replays (`demand/trace_vehicles.json`).

**Same runs.** Before comparing, the script checks:
- the replay program against the record's timeline, second by second;
- the demand files and `trace_vehicles.json` against the manifest;
- every seed's rerun.

**Labels.**
- **observed:** NGSIM positions;
- **derived:** our fixed rules over them;
- **assumed:** synthetic constants;
- **simulated:** SUMO output.

No number comes from model output, and no API call was made.

**Licence and attribution.** The NGSIM-side values are derived from the published vehicle trajectories, U.S. Department of Transportation, Federal Highway Administration, Next Generation Simulation (NGSIM) Program, Lankershim Boulevard, 2005-06-16 08:30–08:45 (data.transportation.gov dataset `8ect-6jqj`, DOI 10.21949/1504477). They are shared under CC BY-SA 3.0. The simulated values come from SUMO 1.28.0.
