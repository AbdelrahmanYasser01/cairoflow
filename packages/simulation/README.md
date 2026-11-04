# cairoflow_simulation

SUMO network, scenario compiler, runner and result extraction. Simulated outcomes only: nothing here controls real infrastructure.

## Baseline four-way network (`networks/baseline_4way/`)
Hand-authored SUMO plain XML (ADR-006), built by `cairoflow_simulation.network.build_network`.

| Item | Value | Status |
|---|---|---|
| Coordinates | SUMO network metres, x east, y north, origin at junction centre `C` | convention |
| Arms | N, E, S, W, 200 m each | **assumption** (placeholder) |
| Approach lanes | 3: lane 0 right, lane 1 through, lane 2 left (SUMO numbers from the right kerb) | **assumption** |
| Exit lanes | 2; right turns use lane 0, through and left use lane 1 | **assumption** |
| Lane width / speed | 3.2 m / 13.89 m/s (50 km/h) | **assumption** |
| Traffic | right-hand, no U-turns, no pedestrians | **assumption** |
| Signal plan | fixed-time protected 4-phase: green 30 s through/right, 12 s left, 3 s yellow, 2 s all-red, 104 s cycle | **assumption**, not observed |

None of the geometry or signal values are measured from a video. They must be replaced or confirmed against the chosen clip before any claim that the simulation resembles a real intersection.

## Build
```python
from pathlib import Path
from cairoflow_simulation.network import SignalPlan, build_network

build = build_network(Path("data/processed/networks/baseline"), SignalPlan())
build.net_path  # baseline.net.xml, signal program embedded
build.manifest  # SUMO version, input hashes, net hash (comments stripped), plan, link count
```
`build_network` runs `netconvert` twice: pass one yields the signal link order, the program is generated from that table, pass two embeds it. It fails if the link order changes or the program is missing.

## Validation
- `find_conflicting_greens(net_path, phases)` lists any green link pairs that are foes. A signal-plan intervention must return an empty list before it is simulated.
- `SignalPlan` bounds every duration (green 5–90 s, left 5–40 s, yellow 3–6 s, all-red 1–6 s).

## Known limits
- SUMO tools echo absolute paths into XML comments; a path containing a double dash makes those comments invalid. The builder runs tools from the build directory with relative paths. Keep the repo path free of double dashes.
- Hashes ignore XML comments because `netconvert` writes a generation timestamp.

## Network from the clip's geometry (`geometry_compiler`, `demand`, `runner`)
`run_baseline(world_model, geometry, signals, out_dir, RunSettings())` compiles the drawn `IntersectionGeometry` and one cycle of the `SignalTimeline` into `out_dir/network/`, writes `out_dir/demand/seed_<n>.rou.xml` per seed, runs `sumo` for every seed in `out_dir/runs/seed_<n>/` (and again in `seed_<n>_rerun/`), and writes `out_dir/baseline_manifest.json`. `out_dir` must not exist.

| Item | Source | Label |
|---|---|---|
| Approach lanes, order from the kerb, turns, widths | drawn lane polygons | the polygons' labels |
| Exit lanes | drawn exit polygons, else 2 × 3.2 m | drawn labels, else synthetic |
| Arm length (200 m), speed limit, straight N/E/S/W arms | `CompileOptions` | synthetic |
| Stop line | drawn distance and SUMO's actual distance, both recorded | drawn label / synthetic |
| Signal program | first complete cycle in the timeline, or the one starting at `CompileOptions.cycle_start_s` | from the timeline's label (`trajectory-derived` -> approximated) |
| Protected left | an arm's left links follow its `left` head, where the timeline has one | the timeline's label |
| Right turn on red | `CompileOptions.right_turn_on_red`: junction type `traffic_light_right_on_red`, right links `s` (stop, then go if no prioritised foe) while their head is red | synthetic |
| Demand | Poisson per movement at the world model's flow (point estimate) | its interval is recorded; counted U-turns are noted, not simulated |

Parallel left-turn lanes connect innermost to leftmost, so they never cross (until 2026-10-28 they were connected crosswise, which made them foes; only networks with two left lanes were affected).

A run is valid only with no teleports, no collisions and every loaded vehicle arrived before warm-up + horizon + drain. Reruns are compared on `tripinfo.xml` with XML comments stripped (`statistics.xml` holds wall-clock timings). SUMO coordinates equal ground metres (`--offset.disable-normalization`).

## The real intersection (NGSIM intersection 2)
`scripts/build_ngsim_int2_scenario.py` builds the geometry, world model and signal timeline from the published NGSIM trajectories (`cairoflow_perception.ngsim_junction`, ADR-019; no camera input) and runs the baseline with right turn on red. The result and its limits are in DATA_REGISTER.md §16.

## Tests
`pytest tests/test_network.py`, which includes a headless `libsumo` run with one vehicle per movement that must finish with every vehicle arrived, no teleports and no collisions. `pytest tests/test_simulation_baseline.py` compiles the synthetic junction, runs 5 seeds twice and checks validity, rerun identity, labels, demand reproducibility and that a forced teleport invalidates a run. `pytest tests/test_ngsim_junction.py` checks protected-left phases, parallel left connections, right turn on red, U-turn demand notes and the committed intersection 2 record.
