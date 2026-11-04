# Replay baseline validation with discharge-anchored cross streets (2026-10-28)

`validation/` was written by `scripts/validate_ngsim_int2_baseline.py --baseline experiments/runs/ngsim-int2-realism-baseline-20261028T132709Z --out <dir>` at commit `1da71bf`. It was copied here unchanged from `experiments/runs/ngsim-int2-realism-validation-20261028T132709Z/` (Git-ignored).

**What it validates.** The replay baseline of ADR-022, with cross-street entries anchored to their observed discharge (DATA_REGISTER.md §19). Its summary is `experiments/records/ngsim-int2-realism-baseline-20261028T132709Z.json`. It supersedes `ngsim-int2-realism-validity-20261028T130401Z/` as the current replay validation; that record is kept as the evidence for §18.

**Windows and checks.**
- The observed window is clip [118, 751) s and the simulated window [0, 633) s. Both sides use the same world-model rules.
- Each simulated vehicle is paired with the track it replays.
- The replay program, the demand files and the reruns are checked before anything is compared.

**Labels.**
- **observed:** NGSIM positions;
- **derived:** our fixed rules over them;
- **assumed:** synthetic constants;
- **simulated:** SUMO output.

No number comes from model output, and no API call was made.

**Licence and attribution.** The NGSIM-side values are derived from the published vehicle trajectories, U.S. Department of Transportation, Federal Highway Administration, Next Generation Simulation (NGSIM) Program, Lankershim Boulevard, 2005-06-16 08:30–08:45 (data.transportation.gov dataset `8ect-6jqj`, DOI 10.21949/1504477). They are shared under CC BY-SA 3.0. The simulated values come from SUMO 1.28.0.
