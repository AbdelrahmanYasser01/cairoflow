# Drain sensitivity: NGSIM intersection 2 experiment

Diagnostic reruns of the recorded runs with only `--end` changed. Decision support only: nothing here controls real traffic. Every outcome number is simulated by SUMO 1.28.0; the settings are assumed. Window [300, 1200) s, demand over [0, 1200) s, recorded end 1800 s.

## Checks

- Inputs: baseline manifest a48949c8a4dc matches the scenario record; experiment manifest 419b07ca9b3d matches its recorded hash; the experiment manifest's baseline_manifest_sha256 and baseline_dir match.
- At 1800 s every rerun's tripinfo and queue.xml hash as recorded: True.
- At every later end, every output before 1800 s is identical to the recorded run: True.
- All checks passed: **True**.

## Completion per arm and seed

`W_in_2` queue: queue.xml queueing_length at the window end and at the last step an 1800 s run writes (identical at every end, by the check above).

| Arm | Seed | Unfinished @1800 | Unfinished @2400 | Unfinished @4200 | Last arrival @1800 (s) | Last arrival @2400 (s) | Last arrival @4200 (s) | W_in_2 @1200 (m) | W_in_2 @1799 (m) | Checks |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 1 | 0 | 0 | 0 | 1279 | 1279 | 1279 | 6.0 | 0.0 | pass |
| baseline | 2 | 0 | 0 | 0 | 1378 | 1378 | 1378 | 27.8 | 0.0 | pass |
| baseline | 3 | 0 | 0 | 0 | 1279 | 1279 | 1279 | 13.5 | 0.0 | pass |
| baseline | 4 | 0 | 0 | 0 | 1284 | 1284 | 1284 | 13.5 | 0.0 | pass |
| baseline | 5 | 0 | 0 | 0 | 1287 | 1287 | 1287 | 6.0 | 0.0 | pass |
| I1 | 1 | 0 | 0 | 0 | 1315 | 1315 | 1315 | 13.5 | 0.0 | pass |
| I1 | 2 | 0 | 0 | 0 | 1422 | 1422 | 1422 | 36.0 | 0.0 | pass |
| I1 | 3 | 0 | 0 | 0 | 1511 | 1511 | 1511 | 43.6 | 0.0 | pass |
| I1 | 4 | 3 | 0 | 0 | 1714 | 1916 | 1916 | 148.5 | 0.0 | pass |
| I1 | 5 | 2 | 0 | 0 | 1715 | 1816 | 1816 | 51.1 | 0.0 | pass |
| I2 | 1 | 0 | 0 | 0 | 1274 | 1274 | 1274 | 6.0 | 0.0 | pass |
| I2 | 2 | 0 | 0 | 0 | 1339 | 1339 | 1339 | 28.5 | 0.0 | pass |
| I2 | 3 | 0 | 0 | 0 | 1310 | 1310 | 1310 | 21.0 | 0.0 | pass |
| I2 | 4 | 0 | 0 | 0 | 1278 | 1278 | 1278 | 13.5 | 0.0 | pass |
| I2 | 5 | 0 | 0 | 0 | 1276 | 1276 | 1276 | 6.0 | 0.0 | pass |
| I3 | 1 | 0 | 0 | 0 | 1602 | 1602 | 1602 | 58.5 | 0.0 | pass |
| I3 | 2 | 2 | 0 | 0 | 1707 | 1806 | 1806 | 88.5 | 0.0 | pass |
| I3 | 3 | 2 | 0 | 0 | 1705 | 1806 | 1806 | 88.5 | 0.0 | pass |
| I3 | 4 | 12 | 0 | 0 | 1706 | 2317 | 2317 | 96.1 | 28.6 | pass |
| I3 | 5 | 11 | 0 | 0 | 1706 | 2315 | 2315 | 103.7 | 21.0 | pass |

## Window metrics that change with the end

Per arm and seed, `mean_delay_s` (s) at each end; the metrics listed in the last column differ between ends (every other window metric is identical at every end).

| Arm | Seed | mean_delay_s @1800 | mean_delay_s @2400 | mean_delay_s @4200 | Changed |
|---|---|---|---|---|---|
| baseline | 1 | 32.802 | 32.802 | 32.802 | none |
| baseline | 2 | 41.309 | 41.309 | 41.309 | none |
| baseline | 3 | 28.536 | 28.536 | 28.536 | none |
| baseline | 4 | 31.217 | 31.217 | 31.217 | none |
| baseline | 5 | 30.482 | 30.482 | 30.482 | none |
| I1 | 1 | 33.521 | 33.521 | 33.521 | none |
| I1 | 2 | 46.009 | 46.009 | 46.009 | none |
| I1 | 3 | 35.512 | 35.512 | 35.512 | none |
| I1 | 4 | 44.733 | 44.846 | 44.846 | mean_delay_s |
| I1 | 5 | 39.120 | 39.122 | 39.122 | mean_delay_s |
| I2 | 1 | 30.586 | 30.586 | 30.586 | none |
| I2 | 2 | 32.455 | 32.455 | 32.455 | none |
| I2 | 3 | 29.328 | 29.328 | 29.328 | none |
| I2 | 4 | 30.900 | 30.900 | 30.900 | none |
| I2 | 5 | 30.946 | 30.946 | 30.946 | none |
| I3 | 1 | 37.196 | 37.196 | 37.196 | none |
| I3 | 2 | 41.798 | 41.799 | 41.799 | mean_delay_s |
| I3 | 3 | 41.954 | 41.955 | 41.955 | mean_delay_s |
| I3 | 4 | 53.660 | 56.853 | 56.853 | mean_delay_s |
| I3 | 5 | 47.526 | 50.188 | 50.188 | mean_delay_s |

## Paired comparisons with the baseline at each end

Candidate - baseline over paired seeds, 95% t interval (`paired_comparison`). Listed: mean delay, throughput and every metric whose verdict is not inconclusive. Where the candidate left vehicles unfinished at that end, its mean delay is a lower bound: under the eligibility rule it is not compared, and the row says so.

| Candidate | End (s) | Complete | Metric | Mean difference | 95% interval | Verdict |
|---|---|---|---|---|---|---|
| I1 | 1800 | no | mean_delay_s | +6.910 s | [+1.026, +12.794] | worsens (lower bound; not compared under the rule) |
| I1 | 1800 | no | throughput_veh_per_h | -56.000 veh/h | [-116.718, +4.718] | inconclusive |
| I1 | 1800 | no | max_queue_m.W | +51.006 m | [+12.392, +89.620] | worsens |
| I1 | 1800 | no | mean_queue_m.S | -3.073 m | [-5.579, -0.568] | improves |
| I1 | 1800 | no | mean_queue_m.W | +33.019 m | [+13.156, +52.883] | worsens |
| I1 | 2400 | yes | mean_delay_s | +6.933 s | [+1.000, +12.865] | worsens |
| I1 | 2400 | yes | throughput_veh_per_h | -56.000 veh/h | [-116.718, +4.718] | inconclusive |
| I1 | 2400 | yes | max_queue_m.W | +51.006 m | [+12.392, +89.620] | worsens |
| I1 | 2400 | yes | mean_queue_m.S | -3.073 m | [-5.579, -0.568] | improves |
| I1 | 2400 | yes | mean_queue_m.W | +33.019 m | [+13.156, +52.883] | worsens |
| I1 | 4200 | yes | mean_delay_s | +6.933 s | [+1.000, +12.865] | worsens |
| I1 | 4200 | yes | throughput_veh_per_h | -56.000 veh/h | [-116.718, +4.718] | inconclusive |
| I1 | 4200 | yes | max_queue_m.W | +51.006 m | [+12.392, +89.620] | worsens |
| I1 | 4200 | yes | mean_queue_m.S | -3.073 m | [-5.579, -0.568] | improves |
| I1 | 4200 | yes | mean_queue_m.W | +33.019 m | [+13.156, +52.883] | worsens |
| I2 | 1800 | yes | mean_delay_s | -2.026 s | [-6.981, +2.929] | inconclusive |
| I2 | 1800 | yes | throughput_veh_per_h | +6.400 veh/h | [-41.308, +54.108] | inconclusive |
| I2 | 1800 | yes | mean_queue_m.S | +2.106 m | [+0.998, +3.214] | worsens |
| I2 | 2400 | yes | mean_delay_s | -2.026 s | [-6.981, +2.929] | inconclusive |
| I2 | 2400 | yes | throughput_veh_per_h | +6.400 veh/h | [-41.308, +54.108] | inconclusive |
| I2 | 2400 | yes | mean_queue_m.S | +2.106 m | [+0.998, +3.214] | worsens |
| I2 | 4200 | yes | mean_delay_s | -2.026 s | [-6.981, +2.929] | inconclusive |
| I2 | 4200 | yes | throughput_veh_per_h | +6.400 veh/h | [-41.308, +54.108] | inconclusive |
| I2 | 4200 | yes | mean_queue_m.S | +2.106 m | [+0.998, +3.214] | worsens |
| I3 | 1800 | no | mean_delay_s | +11.558 s | [+0.352, +22.763] | worsens (lower bound; not compared under the rule) |
| I3 | 1800 | no | throughput_veh_per_h | -64.000 veh/h | [-159.842, +31.842] | inconclusive |
| I3 | 1800 | no | max_queue_m.W | +82.366 m | [+42.060, +122.672] | worsens |
| I3 | 1800 | no | mean_queue_m.W | +53.071 m | [+32.357, +73.785] | worsens |
| I3 | 2400 | yes | mean_delay_s | +12.729 s | [-0.218, +25.676] | inconclusive |
| I3 | 2400 | yes | throughput_veh_per_h | -64.000 veh/h | [-159.842, +31.842] | inconclusive |
| I3 | 2400 | yes | max_queue_m.W | +82.366 m | [+42.060, +122.672] | worsens |
| I3 | 2400 | yes | mean_queue_m.W | +53.071 m | [+32.357, +73.785] | worsens |
| I3 | 4200 | yes | mean_delay_s | +12.729 s | [-0.218, +25.676] | inconclusive |
| I3 | 4200 | yes | throughput_veh_per_h | -64.000 veh/h | [-159.842, +31.842] | inconclusive |
| I3 | 4200 | yes | max_queue_m.W | +82.366 m | [+42.060, +122.672] | worsens |
| I3 | 4200 | yes | mean_queue_m.W | +53.071 m | [+32.357, +73.785] | worsens |
