# Replay baseline validation on signal timeline version 2 (2026-10-28)

`validation/` was written by `scripts/validate_ngsim_int2_baseline.py --baseline experiments/runs/ngsim-int2-realism-baseline-20261028T141919Z --timeline data/geometry/ngsim-lankershim-int2/signal_timeline_v2.json --out <dir>` with the code of commit `ed20dfa`. It was copied here unchanged from `experiments/runs/ngsim-int2-realism-validation-20261028T141919Z/` (Git-ignored).

**What it validates.** The replay baseline of ADR-022 with the cross-street rules of DATA_REGISTER.md §§19 and 21. It replays signal timeline version 2 (`signal_timeline_v2.json`, §22) instead of the record's version 1. Its summary is `experiments/records/ngsim-int2-realism-baseline-20261028T141919Z.json`. It supersedes `ngsim-int2-realism-validity-20261028T135241Z/` as the current replay validation; that record is kept as the evidence for §21.

**Windows and checks.**
- The observed window is clip [118, 751) s and the simulated window [0, 633) s. Both sides use the same world-model rules.
- **One timeline on both sides.** Every observed signal state is taken from timeline version 2: end-of-red queues, headways, discharge per green, arrivals on green and right turns on red. The record's world model is still rebuilt with version 1 and must hash to the record.
- Each simulated vehicle is paired with the track it replays.
- The checks run before anything is compared:
  - the replay program against version 2;
  - version 2 against its estimate record;
  - the demand files;
  - the reruns.

**In-sample.** Both timeline versions are estimated from the same stop-line crossings that the paired lags compare. Agreement shows that the reconstruction is internally consistent. It does not validate the signal timing independently.

**Labels.**
- **observed:** NGSIM positions;
- **derived:** our fixed rules over them;
- **assumed:** synthetic constants;
- **simulated:** SUMO output.

No number comes from model output, and no API call was made.

**Licence and attribution.** The NGSIM-side values are derived from the published vehicle trajectories, U.S. Department of Transportation, Federal Highway Administration, Next Generation Simulation (NGSIM) Program, Lankershim Boulevard, 2005-06-16 08:30–08:45 (data.transportation.gov dataset `8ect-6jqj`, DOI 10.21949/1504477). They are shared under CC BY-SA 3.0. The simulated values come from SUMO 1.28.0.
