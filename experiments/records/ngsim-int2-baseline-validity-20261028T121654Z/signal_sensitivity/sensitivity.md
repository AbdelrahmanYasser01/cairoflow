# Signal-timing sensitivity, NGSIM intersection 2 (baseline validity, investigation C)

Created 2026-10-28T12:17:24+00:00 by `scripts/signal_sensitivity_ngsim_int2.py` at commit `f0121f9ac40f43b0812e7da2830222e8f4d48cf2`; SUMO 1.28.0, numpy 2.5.4. Labels: **observed** (NGSIM trajectories), **derived** (fixed rule over observed values), **assumed** (synthetic), **simulated** (SUMO).

## Inputs checked against the committed record

- data/geometry/ngsim-lankershim-int2/scenario.json: SHA-256 as recorded
- geometry.json: SHA-256 as the record's
- signal_timeline.json: SHA-256 as the record's
- world_model.json: SHA-256 as the record's
- processed timeline equals the committed signal_timeline.json
- baseline_manifest.json: SHA-256 as the record's
- demand/seed_1.rou.xml: SHA-256 as the baseline manifest's
- demand/seed_2.rou.xml: SHA-256 as the baseline manifest's
- demand/seed_3.rou.xml: SHA-256 as the baseline manifest's
- demand/seed_4.rou.xml: SHA-256 as the baseline manifest's
- demand/seed_5.rou.xml: SHA-256 as the baseline manifest's
- baseline tripinfo hashes as the record's
- experiment ran against the recorded baseline
- committed experiment record names this scenario record
- I2 tripinfo hashes as the committed experiment record's
- I2 comparisons as the committed experiment record's

## Candidate cuts (derived; counts observed)

25 candidate cuts, 7 qualifying; the record's choice (564 s) is reproduced: yes. Onset spacing (all arms): 80-201 s, median 99 s.

| start | arm | length | N all | N left | S all | S left | E | W | all-red | qualifies / problems |
|---|---|---|---|---|---|---|---|---|---|---|
| 103 | E | 99 | 31 | 21 | 28 | 19 | 5 | 11 | 10 | W all: unknown inside |
| 164 | N | 80 | 31 | 1 | 28 | 0 | 5 | 0 | 35 | S left: no green; W all: no green |
| 166 | S | 100 | 51 | 18 | 28 | 0 | 5 | 0 | 35 | S left: no green; W all: no green |
| 202 | E | 98 | 45 | 18 | 26 | 0 | 5 | 0 | 36 | S left: no green; W all: no green |
| 244 | N | 102 | 45 | 29 | 26 | 7 | 5 | 5 | 20 | **yes** |
| 266 | S | 94 | 37 | 21 | 26 | 7 | 5 | 5 | 20 | **yes** |
| 300 | E | 201 | 100 | 40 | 69 | 11 | 5 | 31 | 24 | **yes** |
| 313 | W | 89 | 51 | 21 | 35 | 7 | 0 | 5 | 13 | E all: no green |
| 346 | N | 99 | 51 | 18 | 35 | 4 | 0 | 26 | 5 | E all: no green |
| 360 | S | 100 | 52 | 19 | 35 | 4 | 0 | 26 | 5 | E all: no green |
| 402 | W | 115 | 49 | 19 | 34 | 4 | 5 | 26 | 14 | **yes** |
| 445 | N | 88 | 49 | 10 | 34 | 0 | 5 | 5 | 17 | S left: no green |
| 460 | S | 104 | 65 | 25 | 34 | 0 | 5 | 5 | 17 | S left: no green |
| 501 | E | 100 | 62 | 25 | 32 | 0 | 5 | 5 | 15 | S left: no green |
| 517 | W | 98 | 62 | 25 | 32 | 0 | 5 | 5 | 13 | S left: no green |
| 533 | N | 116 | 62 | 40 | 32 | 10 | 5 | 12 | 9 | N left: does not close (green to red) |
| 564 | S | 99 | 45 | 17 | 32 | 10 | 5 | 12 | 9 | **yes** (recorded choice) |
| 601 | E | 101 | 46 | 17 | 26 | 10 | 5 | 12 | 11 | **yes** |
| 615 | W | 104 | 46 | 17 | 26 | 10 | 5 | 12 | 14 | **yes** |
| 649 | N | 103 | 46 | 15 | 26 | 8 | 5 | 9 | 18 | S left: unknown inside |
| 663 | S | 103 | 46 | 22 | 26 | 8 | 5 | 9 | 18 | S left: unknown inside |
| 702 | E | 99 | 36 | 22 | 29 | 8 | 5 | 9 | 15 | S left: unknown inside |
| 719 | W | 97 | 36 | 22 | 29 | 8 | 5 | 9 | 7 | E all: unknown inside; S left: unknown inside |
| 752 | N | 82 | 36 | 11 | 29 | 0 | 5 | 5 | 0 | E all: unknown inside; S left: unknown inside; W all: unknown inside |
| 766 | S | 96 | 50 | 14 | 29 | 0 | 5 | 5 | 0 | N left: unknown inside; E all: unknown inside; S left: unknown inside; W all: unknown inside |

## Physical cycles: S-through onsets (derived greens; observed crossings and queues)

| cycle | length | onset order | N thr / left crossings | S thr / left | E thr+left | W thr+left | N queue before N onset | N-left queue | S queue |
|---|---|---|---|---|---|---|---|---|---|
| 166-266 | 100 | S all > E all > N left > N all | 42 / 19 | 30 / 2 | 1 | 5 | 13 | 17+ | 20 |
| 266-360 | 94 | S all > E all > W all > N left > S left > N all | 47 / 11 | 18 / 1 | 1 | 5 | 2 | 11 | 13 |
| 360-460 | 100 | S all > W all > N left > S left > N all | 42 / 14 | 27 / 1 | 0 | 7 | 2 | 11 | 14 |
| 460-564 | 104 | S all > E all > W all > N all > N left | 38 / 15 | 32 / 1 | 2 | 5 | 4 | 9 | 22+ |
| 564-663 | 99 | S all > E all > W all > N left > S left > N all | 28 / 14 | 31 / 3 | 2 | 5 | 6 | 11 | 25+ |
| 663-766 | 103 | S all > E all > W all > N left > S left > N all | 49 / 19 | 28 / 2 | 3 | 13 | 17 | 11 | 15 |
| 766-862 | 96 | S all > E all > W all > N left > N all | 31 / 8 | 26 / 0 | 2 | 8 | 6 | 7 | 15 |

Queues: observed queued vehicles in the head's lanes 1 s before its estimated onset; '+' marks a lower bound (queue reached the end of the observed lane).

## Baseline runs per qualifying cut (simulated)

| cut | cycle | net as recorded | valid | unfinished | teleports | collisions | emergency braking | reruns identical | tripinfo as recorded |
|---|---|---|---|---|---|---|---|---|---|
| cut_244s | 102 | no | no | 27 | 0 | 0 | 0 | yes | no |
| cut_266s | 94 | no | no | 17 | 0 | 0 | 0 | yes | no |
| cut_300s | 201 | no | yes | 0 | 0 | 0 | 0 | yes | no |
| cut_402s | 115 | no | no | 2 | 0 | 0 | 0 | yes | no |
| cut_564s | 99 | yes | yes | 0 | 0 | 0 | 0 | yes | yes |
| cut_601s | 101 | no | yes | 0 | 0 | 0 | 0 | yes | no |
| cut_615s | 104 | no | yes | 0 | 0 | 0 | 0 | yes | no |

## Metric means over seeds 1-5 per cut (simulated), spread and I2's recorded effect

| metric | cut_244s | cut_266s | cut_300s | cut_402s | cut_564s | cut_601s | cut_615s | range (valid cuts) | seed SD (recorded cut) | I2 recorded mean diff [95% CI] | range / abs(I2 diff) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mean_delay_s | 46.7* | 45.1* | 31.5 | 42.7* | 32.9 | 36.3 | 39.0 | 7.5 | 5.0 | -2.0 [-7.0, 2.9] inconclusive | 3.70 |
| throughput_veh_per_h | 3728.0* | 3688.0* | 3724.0 | 3664.8* | 3744.8 | 3673.6 | 3601.6 | 143.2 | 31.7 | 6.4 [-41.3, 54.1] inconclusive | 22.38 |
| max_queue_m.N | 88.5* | 87.0* | 85.5 | 113.0* | 99.5 | 103.0 | 110.5 | 25.0 | 30.3 | -11.5 [-45.7, 22.7] inconclusive | 2.18 |
| max_queue_m.E | 26.7* | 20.5* | 35.0 | 32.0* | 28.8 | 27.5 | 26.3 | 8.7 | 12.7 | -1.7 [-11.6, 8.2] inconclusive | 5.13 |
| max_queue_m.S | 85.5* | 77.0* | 75.0 | 85.5* | 79.0 | 86.0 | 89.5 | 14.5 | 8.9 | 5.0 [-2.3, 12.3] inconclusive | 2.91 |
| max_queue_m.W | 127.4* | 118.5* | 55.5 | 43.5* | 43.5 | 45.0 | 45.0 | 12.0 | 11.9 | -1.5 [-5.6, 2.7] inconclusive | 8.08 |
| mean_queue_m.N | 26.0* | 29.1* | 29.7 | 49.2* | 43.1 | 47.6 | 53.4 | 23.7 | 15.7 | -10.7 [-26.5, 5.2] inconclusive | 2.22 |
| mean_queue_m.E | 4.1* | 4.4* | 13.0 | 5.7* | 4.8 | 4.7 | 4.7 | 8.3 | 1.3 | -0.0 [-0.9, 0.8] inconclusive | 220.78 |
| mean_queue_m.S | 28.0* | 23.5* | 23.6 | 35.8* | 21.0 | 26.3 | 28.1 | 7.1 | 1.6 | 2.1 [1.0, 3.2] worsens | 3.37 |
| mean_queue_m.W | 66.2* | 61.4* | 13.3 | 9.3* | 10.3 | 11.1 | 11.2 | 3.0 | 2.4 | 0.0 [-1.4, 1.4] inconclusive | 1065.16 |

\* the cut's baseline is not valid; excluded from the range.

## Paired difference of each valid cut against the recorded cut (same seeds and demand)

| metric | cut_244s | cut_266s | cut_300s | cut_402s | cut_601s | cut_615s |
|---|---|---|---|---|---|---|
| mean_delay_s | n/a | n/a | -1.4 | n/a | 3.4 (CI excludes 0) | 6.1 (CI excludes 0) |
| throughput_veh_per_h | n/a | n/a | -20.8 | n/a | -71.2 (CI excludes 0) | -143.2 (CI excludes 0) |
| max_queue_m.N | n/a | n/a | -14.0 | n/a | 3.5 | 11.0 |
| max_queue_m.E | n/a | n/a | 6.2 (CI excludes 0) | n/a | -1.3 | -2.5 |
| max_queue_m.S | n/a | n/a | -4.0 | n/a | 7.0 | 10.5 |
| max_queue_m.W | n/a | n/a | 12.0 (CI excludes 0) | n/a | 1.5 | 1.5 |
| mean_queue_m.N | n/a | n/a | -13.4 (CI excludes 0) | n/a | 4.6 | 10.3 (CI excludes 0) |
| mean_queue_m.E | n/a | n/a | 8.2 (CI excludes 0) | n/a | -0.1 | -0.1 |
| mean_queue_m.S | n/a | n/a | 2.6 | n/a | 5.3 (CI excludes 0) | 7.1 (CI excludes 0) |
| mean_queue_m.W | n/a | n/a | 3.0 | n/a | 0.8 | 0.9 |

## I2 re-applied per cut: robustness check of the existing validated candidate

Not a new candidate: the recorded I2 per-phase change on each cut's own baseline, validated by `cairoflow_simulation.validation`, same seeds and demand.

| cut | structure | greens set | validation | valid runs | mean_delay_s | throughput_veh_per_h | max_queue_m.N | max_queue_m.E | max_queue_m.S | max_queue_m.W | mean_queue_m.N | mean_queue_m.E | mean_queue_m.S | mean_queue_m.W |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cut_244s | same phase structure, rotated | n/a | this cut's baseline is not valid, so no comparison is meaningful | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| cut_266s | identical phase structure | n/a | this cut's baseline is not valid, so no comparison is meaningful | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| cut_300s | phase Ng_Er_Sg_Wr_NLr_SLr occurs 2 times in this cut | n/a | phase Ng_Er_Sg_Wr_NLr_SLr occurs 2 times in this cut | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| cut_402s | each changed phase occurs once; other phases (clearance order) differ | n/a | this cut's baseline is not valid, so no comparison is meaningful | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| cut_564s | identical phase structure | p0 26 s, p11 15 s | accepted | yes | -2.0 inconclusive | 6.4 inconclusive | -11.5 inconclusive | -1.7 inconclusive | 5.0 inconclusive | -1.5 inconclusive | -10.7 inconclusive | -0.0 inconclusive | 2.1 worsens | 0.0 inconclusive |
| cut_601s | each changed phase occurs once; other phases (clearance order) differ | p6 15 s, p12 21 s | accepted | yes | -0.7 inconclusive | -5.6 inconclusive | -13.5 inconclusive | -3.0 inconclusive | 8.0 inconclusive | 0.0 inconclusive | -13.9 inconclusive | -0.3 inconclusive | 8.1 worsens | -0.0 inconclusive |
| cut_615s | each changed phase occurs once; other phases (clearance order) differ | p3 15 s, p9 21 s | accepted | yes | -0.1 inconclusive | -16.0 inconclusive | -22.0 inconclusive | -2.8 inconclusive | 15.0 inconclusive | 0.0 inconclusive | -17.4 inconclusive | -0.2 improves | 12.7 worsens | 0.0 inconclusive |

Recorded I2 (cut cut_564s): mean_delay_s -2.0 inconclusive; throughput_veh_per_h 6.4 inconclusive; max_queue_m.N -11.5 inconclusive; max_queue_m.E -1.7 inconclusive; max_queue_m.S 5.0 inconclusive; max_queue_m.W -1.5 inconclusive; mean_queue_m.N -10.7 inconclusive; mean_queue_m.E -0.0 inconclusive; mean_queue_m.S 2.1 worsens; mean_queue_m.W 0.0 inconclusive

## Limitations

- Every cut is a slice of one trajectory-derived timeline; no published timing plan or signal head was read. Yellow 4 s, start-up 2 s, clearance 1 s and minimum green 5 s are assumed.
- Each cut is repeated as fixed time; none represents actuated or coordinated behaviour.
- Only qualifying cuts can be compiled; non-qualifying cuts are listed with the reason, not repaired.
- Five seeds per cut: seed SDs and 95% intervals are wide; differences are simulated, not observed.
- Right turn on red stays on (assumed, as recorded).
