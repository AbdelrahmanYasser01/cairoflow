# Baseline validity evidence, NGSIM intersection 2 (2026-10-28)

Evidence behind `data/geometry/ngsim-lankershim-int2/baseline_assessment.json` and DATA_REGISTER.md §17. Every file was written by a committed script into a new, Git-ignored run directory and copied here unchanged.

| Folder | Script | Commit | Run directory (Git-ignored) |
|---|---|---|---|
| `validation/` | `scripts/validate_ngsim_int2_baseline.py --out <dir>` | `f0121f9` | `experiments/runs/ngsim-int2-validation-20261028T121654Z/` |
| `signal_sensitivity/` | `scripts/signal_sensitivity_ngsim_int2.py --out <dir> --i2` | `f0121f9` | `experiments/runs/ngsim-int2-signal-sensitivity-20261028T121654Z/` |
| `drain_sensitivity/` | `scripts/drain_sensitivity_ngsim_int2.py --out <dir>` | `2b3a9bc` | `experiments/runs/ngsim-int2-drain-sensitivity-20261028T122109Z/` |

**Reproducibility.**
- **Same results.** Each report is identical to an earlier independent run of the same script (2026-10-28, in scratch directories), except for timestamps and paths.
- **Same runs.** Each rerun reproduces the recorded SUMO runs before it compares anything:
  - recorded tripinfo hashes;
  - pre-1800 s outputs, for later ends;
  - the recorded network for cut 564.

**Inputs.**
- The committed scenario record `data/geometry/ngsim-lankershim-int2/` and the processed world model it names.
- The recorded baseline `experiments/runs/ngsim-int2-baseline-20261028T074304Z/` and the recorded experiment `experiments/runs/ngsim-int2-experiment-20261028T084336Z/`.
- The NGSIM trajectory CSV.

Each input is checked against its recorded SHA-256 before use.

**Labels.**
- **observed:** NGSIM positions;
- **derived:** our fixed rules over them;
- **assumed:** synthetic constants;
- **simulated:** SUMO output.

No number comes from model output, and no API call was made.

**Licence and attribution.** The NGSIM-side values are derived from the published vehicle trajectories, U.S. Department of Transportation, Federal Highway Administration, Next Generation Simulation (NGSIM) Program, Lankershim Boulevard, 2005-06-16 08:30–08:45 (data.transportation.gov dataset `8ect-6jqj`, DOI 10.21949/1504477). They are shared under CC BY-SA 3.0, like `data/geometry/ngsim-lankershim-int2/scenario.json`. The simulated values come from SUMO 1.28.0 runs of CairoFlow's network compiled from those derived values.
