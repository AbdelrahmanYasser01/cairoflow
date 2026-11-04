# NGSIM intersection 2: SUMO baseline against the observations

Created 2026-10-28T12:17:23+00:00 by `scripts/validate_ngsim_int2_baseline.py`. Schema `cairoflow-baseline-validation/0.1.0`.

Decision support only. Observed values are **derived**: CairoFlow's fixed rules over the published NGSIM positions (CC BY-SA 3.0, U.S. DOT FHWA NGSIM `8ect-6jqj`). Simulated values come from SUMO output files of the recorded baseline, rerun with FCD. Each quantity has a **status**, not a pass/fail score: *comparable* (same definition and meaning), *comparable-with-caveats*, or *not comparable*.

**Summary.** 93 quantities compared with identical definitions: 13 comparable, 68 comparable-with-caveats, 12 not comparable; 8 listed as not validatable. Decision metrics with any observed counterpart: throughput (circular) and the N and S queues (recomputed with the observed definition, censored lower bounds); mean delay and the E and W queues have none.

## Inputs and identity checks

| Check | OK |
|---|---|
| record geometry.json = processed geometry.json | yes |
| record signal_timeline.json = processed signal_timeline.json | yes |
| trajectories SHA-256 = record input | yes |
| trajectories SHA-256 = SHA256SUMS | yes |
| baseline world model = record world model | yes |
| baseline_manifest.json = record | yes |
| network.net.xml = manifest | yes |
| demand seed_1.rou.xml = manifest | yes |
| demand seed_2.rou.xml = manifest | yes |
| demand seed_3.rou.xml = manifest | yes |
| demand seed_4.rou.xml = manifest | yes |
| demand seed_5.rou.xml = manifest | yes |
| recorded seed 1 tripinfo = manifest | yes |
| recorded seed 2 tripinfo = manifest | yes |
| recorded seed 3 tripinfo = manifest | yes |
| recorded seed 4 tripinfo = manifest | yes |
| recorded seed 5 tripinfo = manifest | yes |
| signal program offset (phase 0 starts at 0 s) | yes |
| frame bearing = record | yes |
| vehicle runs in the study box = record | yes |
| rebuilt world model = record world_model.json | yes |
| simulated through crossings during green or yellow of the rebuilt SUMO program (share >= 0.99) | yes |

| Seed | Recorded tripinfo SHA-256 | Rerun (fcd device entries removed) | Entries removed / trips | Same run |
|---|---|---|---|---|
| 1 | `157e9049188daf1c…` | `157e9049188daf1c…` | 1214 / 1214 | yes |
| 2 | `48b554ba828083d9…` | `48b554ba828083d9…` | 1243 / 1243 | yes |
| 3 | `93cfe01fd8532c71…` | `93cfe01fd8532c71…` | 1261 / 1261 | yes |
| 4 | `4c12f54b9c4f20a9…` | `4c12f54b9c4f20a9…` | 1258 / 1258 | yes |
| 5 | `597574251ecfa186…` | `597574251ecfa186…` | 1223 / 1223 | yes |

Windows: observed 2005-06-16 08:30:00-08:45:00 PDT: window [0, 900) s; simulated [300, 1200) s of each seed (after 300 s warm-up), shifted to [0, 900) s.

Simulated stop lines (observed rule on SUMO's stopped vehicles) against SUMO's lane ends and the observed gates, metres from the centre:

| Arm | Observed gate | SUMO lane end | Simulated counting line | Pooled heads |
|---|---|---|---|---|
| N | 20.472 | 13.98 | 14.48 | 14543 |
| E | 13.368 | 19.32 | 18.92 | 3570 |
| S | 20.226 | 15.15 | 15.65 | 11490 |
| W | 16.521 | 22.06 | 22.56 | 6914 |

## Comparisons

Observed: estimate [95% interval] (n). Simulated: mean ± SD over seeds (range). Discrepancy: simulated minus observed. ≥ marks a censored lower bound.

### Movement counts (demand-generator sanity check)

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Counted vehicles, N left | veh per 900 s | 133.0 [111.4, 157.6] (n=133) | 134.0 ± 9.38 (122.0–142.0) | +1.00 | comparable |
| Counted vehicles, N through | veh per 900 s | 323.0 [288.7, 360.2] (n=323) | 316.0 ± 9.00 (307.0–329.0) | -7.00 | comparable |
| Counted vehicles, N right | veh per 900 s | 28.0 [18.6, 40.5] (n=28) | 28.4 ± 1.14 (27.0–30.0) | +0.40 | comparable |
| Counted vehicles, E left | veh per 900 s | 6.00 [2.20, 13.1] (n=6) | 4.60 ± 1.82 (3.00–7.00) | -1.40 | comparable |
| Counted vehicles, E through | veh per 900 s | 8.00 [3.45, 15.8] (n=8) | 7.40 ± 1.52 (5.00–9.00) | -0.60 | comparable |
| Counted vehicles, E right | veh per 900 s | 37.0 [26.1, 51.0] (n=37) | 35.2 ± 9.01 (28.0–50.0) | -1.80 | comparable |
| Counted vehicles, S left | veh per 900 s | 15.0 [8.40, 24.7] (n=15) | 15.4 ± 3.58 (10.0–20.0) | +0.40 | comparable |
| Counted vehicles, S through | veh per 900 s | 250.0 [220.0, 283.0] (n=250) | 248.6 ± 12.4 (232.0–261.0) | -1.40 | comparable |
| Counted vehicles, S right | veh per 900 s | 82.0 [65.2, 101.8] (n=82) | 75.0 ± 7.11 (69.0–85.0) | -7.00 | comparable |
| Counted vehicles, W left | veh per 900 s | 27.0 [17.8, 39.3] (n=27) | 28.8 ± 6.30 (22.0–38.0) | +1.80 | comparable |
| Counted vehicles, W through | veh per 900 s | 34.0 [23.5, 47.5] (n=34) | 32.0 ± 6.24 (25.0–41.0) | -2.00 | comparable |
| Counted vehicles, W right | veh per 900 s | 3.00 [0.62, 8.77] (n=3) | 3.40 ± 1.14 (2.00–5.00) | +0.40 | comparable |
| Counted vehicles, total | veh per 900 s | 946.0 [886.7, 1,008.3] (n=946) | 928.8 ± 8.07 (919.0–939.0) | -17.20 | comparable |

Method: vehicles whose first stop-line crossing and a later exit-line crossing both fall in the window (world_model.count_movements rule, identical code on both sides).

Limitations:
- circular: SUMO demand is generated from these observed counts, so agreement checks the demand generator and the network's capacity to serve it, not the model
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)

### Lane use at the stop line

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Lane share at the stop line, N approach, lane N11 | share | 10.5% [8.1%, 13.6%] (n=484) | 13.9% ± 0.7% (13.1%–14.6%) | +3.4 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N12 | share | 16.9% [13.9%, 20.5%] (n=484) | 14.1% ± 0.6% (13.4%–14.7%) | -2.9 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N1 | share | 21.1% [17.7%, 24.9%] (n=484) | 22.0% ± 0.8% (20.8%–22.7%) | +1.0 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N2 | share | 21.3% [17.9%, 25.1%] (n=484) | 21.9% ± 0.7% (21.0%–22.6%) | +0.7 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N3 | share | 24.6% [21.0%, 28.6%] (n=484) | 22.1% ± 0.6% (21.6%–22.9%) | -2.5 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N31 | share | 5.6% [3.9%, 8.0%] (n=484) | 5.9% ± 0.3% (5.6%–6.3%) | +0.4 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane E1 | share | 9.8% [4.3%, 21.0%] (n=51) | 10.1% ± 4.5% (4.9%–16.3%) | +0.3 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane E2 | share | 17.6% [9.6%, 30.3%] (n=51) | 16.0% ± 4.5% (11.1%–22.5%) | -1.6 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane E3 | share | 72.5% [59.1%, 82.9%] (n=51) | 73.9% ± 7.0% (65.1%–82.0%) | +1.3 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S11 | share | 4.6% [2.9%, 7.4%] (n=347) | 4.5% ± 1.0% (3.0%–5.7%) | -0.1 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S1 | share | 16.1% [12.6%, 20.4%] (n=347) | 24.2% ± 0.6% (23.1%–24.6%) | +8.1 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S2 | share | 24.8% [20.5%, 29.6%] (n=347) | 24.1% ± 0.8% (23.1%–25.0%) | -0.7 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S3 | share | 31.1% [26.5%, 36.2%] (n=347) | 25.0% ± 0.9% (24.2%–26.1%) | -6.1 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S4 | share | 23.3% [19.2%, 28.1%] (n=347) | 22.1% ± 1.7% (20.2%–24.9%) | -1.2 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane W1 | share | 42.2% [30.9%, 54.4%] (n=64) | 43.7% ± 6.3% (36.2%–52.5%) | +1.5 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane W2 | share | 14.1% [7.6%, 24.6%] (n=64) | 21.5% ± 3.1% (16.4%–24.1%) | +7.5 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane W3 | share | 42.2% [30.9%, 54.4%] (n=64) | 32.3% ± 3.1% (29.5%–36.2%) | -9.9 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane unknown | share | 1.6% [0.3%, 8.3%] (n=64) | 2.5% ± 0.9% (1.6%–3.4%) | +1.0 pp | comparable-with-caveats |

Method: share of the approach's counted vehicles by the lane polygon holding their last position within 2 s before the stop-line crossing (world_model rule); simulated lanes are SUMO's, named after the NGSIM lane they were compiled from.

Limitations:
- SUMO chooses lanes itself (departLane 'best', its lane-change model); the observed choice also reflects upstream conditions outside the study box
- simulated lanes sit up to about 1 m across from the NGSIM lane centres
- vehicles within a platoon are not independent; the interval is too narrow

### Discharge headways

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Mean discharge headway, N through lanes, queue positions k >= 5 | s | 1.81 [1.68, 2.13] (n=14) | 2.02 ± 0.02 (1.98–2.04) | +0.21 | comparable-with-caveats |
| Mean discharge headway, N through lanes, queue positions k = 2-4 | s | 3.20 [2.44, 4.22] (n=33) | 2.70 ± 0.03 (2.66–2.74) | -0.50 | comparable-with-caveats |
| Mean discharge headway, E through lanes, queue positions k >= 5 | s | n/a (n=0) | n/a | n/a | not comparable |
| Mean discharge headway, E through lanes, queue positions k = 2-4 | s | 1.92 (n=1) | 3.18 ± 0.10 (3.03–3.24) | +1.25 | not comparable |
| Mean discharge headway, S through lanes, queue positions k >= 5 | s | 2.03 [1.89, 2.21] (n=56) | 2.02 ± 0.03 (1.98–2.05) | -0.01 | comparable-with-caveats |
| Mean discharge headway, S through lanes, queue positions k = 2-4 | s | 2.30 [2.18, 2.43] (n=65) | 2.69 ± 0.04 (2.63–2.73) | +0.39 | comparable-with-caveats |
| Mean discharge headway, W through lanes, queue positions k >= 5 | s | n/a (n=0) | n/a | n/a | not comparable |
| Mean discharge headway, W through lanes, queue positions k = 2-4 | s | n/a (n=0) | 3.39 ± 0.10 (3.29–3.49) | n/a | not comparable |

Method: per end-of-red queue in a through-only lane: its queued vehicles' stop-line crossings in the green and yellow that follow, in crossing order; headway k = crossing k - crossing k-1 (k >= 2); position 1 (start-up) is left out: the observed green onsets are estimated from these crossings.

Limitations:
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- only vehicles queued at the end of red; vehicles joining the moving queue are left out
- simulated discharge follows SUMO's Krauss defaults (accel 2.6 m/s2, minGap 2.5 m, tau 1 s): uncalibrated
- too few observed queues this long (fewer than 10 headways, or a seed without any)
- Evidence needed where not comparable: longer observed queues on these lanes, or a longer window

### Discharge per green

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Stop-line crossings per green (green + yellow), N all head | veh per green | 39.9 [36.5, 43.6] (n=8) | 36.3 ± 0.99 (34.9–37.2) | -3.60 | comparable-with-caveats |
| Stop-line crossings per green (green + yellow), E all head | veh per green | 2.86 [2.43, 3.29] (n=7) | 2.56 ± 0.34 (2.11–3.00) | -0.30 | not comparable |
| Stop-line crossings per green (green + yellow), S all head | veh per green | 31.9 [28.6, 34.0] (n=8) | 30.5 ± 2.00 (28.4–33.1) | -1.38 | comparable-with-caveats |
| Stop-line crossings per green (green + yellow), W all head | veh per green | 6.83 [4.33, 9.83] (n=6) | 7.04 ± 1.12 (6.33–9.00) | +0.21 | not comparable |

Method: crossings by the movements the arm's 'all' head controls, inside each green that follows a red, up to the end of its yellow.

Limitations:
- SUMO repeats one 99 s cycle cut from the estimate; observed onset spacing ranged 80-201 s (apparently actuated control)
- observed signal states are estimated from the same crossings (no head was read)
- observed green windows are fitted to these crossings (start 2 s before the first, yellow ends just after the last)
- circular: SUMO demand is generated from these observed counts, so agreement checks the demand generator and the network's capacity to serve it, not the model
- an observed green exists only where at least 2 vehicles crossed (sparser windows were dropped), so on this low-volume arm the observed mean is biased upwards by construction
- Evidence needed where not comparable: the signal states of this arm (controller logs)

### Queue at the end of red

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Queued vehicles at the end of red, E all head (mean over red ends) | veh | ≥ 2.00 [1.43, 2.57] (n=7) | ≥ 1.38 ± 0.33 (1.00–1.89) | -0.62 | not comparable |
| Queued vehicles at the end of red, N all head (mean over red ends) | veh | ≥ 8.38 [4.50, 12.9] (n=8) | ≥ 17.6 ± 0.55 (17.1–18.3) | +9.23 | comparable-with-caveats |
| Queued vehicles at the end of red, N left head (mean over red ends) | veh | ≥ 11.8 [9.50, 14.0] (n=8) | ≥ 14.7 ± 3.27 (11.8–18.9) | +2.96 | comparable-with-caveats |
| Queued vehicles at the end of red, S all head (mean over red ends) | veh | ≥ 18.6 [16.0, 21.4] (n=8) | ≥ 17.2 ± 0.94 (16.4–18.8) | -1.47 | comparable-with-caveats |
| Queued vehicles at the end of red, S left head (mean over red ends) | veh | 0.80 [0.40, 1.00] (n=5) | 1.38 ± 0.37 (0.89–1.78) | +0.58 | comparable-with-caveats |
| Queued vehicles at the end of red, W all head (mean over red ends) | veh | ≥ 0.83 [0.33, 1.33] (n=6) | ≥ 4.40 ± 0.52 (3.78–5.22) | +3.57 | not comparable |

Method: per red end, vehicles queued in the lanes following that head, summed (world_model rule: below 1 m/s on a centred 1 s window, chained back from the stop line while each is within 12 m of the previous), in the last sample before green.

Limitations:
- the E approach is observed only 11.6 m beyond its gate (one or two vehicles): queues there are not observable
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored (a lower bound); simulated lanes are cut to the same depth, so both are censored alike
- SUMO repeats one 99 s cycle cut from the estimate; observed onset spacing ranged 80-201 s (apparently actuated control)
- observed signal states are estimated from the same crossings (no head was read)
- the queue at the end of red grows with the red's length; observed reds vary, simulated reds are fixed
- the W approach is observed only 9.7 m beyond its gate (one or two vehicles): queues there are not observable
- Evidence needed where not comparable: trajectories or video covering the E approach upstream
- Evidence needed where not comparable: trajectories or video covering the W approach upstream

### Queue each second (observed definition)

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Max over the window of the arm's longest lane queue each second, N | m | ≥ 83.9 (n=900) | ≥ 74.5 ± 10.1 (60.9–84.3) | -9.40 | comparable-with-caveats |
| Mean over the window of the arm's longest lane queue each second, N | m | ≥ 30.6 [22.7, 44.8] (n=900) | ≥ 32.0 ± 10.9 (22.4–49.6) | +1.39 | comparable-with-caveats |
| Max over the window of the arm's longest lane queue each second, E | m | ≥ 10.0 (n=900) | ≥ 9.35 ± 0.33 (9.04–9.90) | -0.69 | not comparable |
| Mean over the window of the arm's longest lane queue each second, E | m | ≥ 3.04 [2.07, 4.85] (n=900) | ≥ 1.96 ± 0.67 (1.25–2.81) | -1.08 | not comparable |
| Max over the window of the arm's longest lane queue each second, S | m | ≥ 80.1 (n=900) | ≥ 65.0 ± 8.34 (53.1–74.8) | -15.10 | comparable-with-caveats |
| Mean over the window of the arm's longest lane queue each second, S | m | ≥ 21.9 [17.9, 27.8] (n=900) | ≥ 13.8 ± 1.36 (12.0–15.6) | -8.14 | comparable-with-caveats |
| Max over the window of the arm's longest lane queue each second, W | m | ≥ 8.70 (n=900) | ≥ 8.55 ± 0.19 (8.35–8.85) | -0.15 | not comparable |
| Mean over the window of the arm's longest lane queue each second, W | m | ≥ 1.51 [0.66, 2.78] (n=900) | ≥ 3.47 ± 0.45 (2.83–4.06) | +1.96 | not comparable |

Method: per second, the longest approach-lane queue of the arm (world_model rule, as the record's observed queue metrics); the max or mean over the window's seconds.

Limitations:
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored (a lower bound); simulated lanes are cut to the same depth, so both are censored alike
- SUMO repeats one 99 s cycle cut from the estimate; observed onset spacing ranged 80-201 s (apparently actuated control)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- length runs to the front of the last queued vehicle (one vehicle length short of its rear) and excludes the 2-4 m from the gate to where the first vehicle stands
- the E approach is observed only 11.6 m beyond its gate
- the W approach is observed only 9.7 m beyond its gate
- Evidence needed where not comparable: trajectories or video covering the E approach upstream
- Evidence needed where not comparable: trajectories or video covering the W approach upstream

### Segment travel time

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Mean segment travel time, N left | s | 61.86 [53.30, 69.93] (n=121) | 69.85 ± 17.01 (56.68–97.69) | +8.00 | comparable-with-caveats |
| Mean segment travel time, N through | s | 20.30 [15.41, 25.54] (n=323) | 30.40 ± 1.19 (29.46–32.26) | +10.10 | comparable-with-caveats |
| Mean segment travel time, N right | s | 12.38 [10.20, 15.67] (n=28) | 12.05 ± 0.79 (10.88–12.77) | -0.33 | comparable-with-caveats |
| Mean segment travel time, S left | s | 55.72 [41.19, 73.23] (n=13) | 47.52 ± 7.48 (39.97–56.57) | -8.21 | comparable-with-caveats |
| Mean segment travel time, S through | s | 40.48 [36.12, 45.78] (n=246) | 38.37 ± 2.54 (36.58–42.24) | -2.12 | comparable-with-caveats |
| Mean segment travel time, S right | s | 16.59 [14.63, 18.70] (n=82) | 17.11 ± 1.73 (14.87–19.51) | +0.52 | comparable-with-caveats |

Method: from the inbound crossing 100 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides

### Arrivals on green at the segment entry

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Share of N through vehicles crossing the entry line while their head is green | share | 73.7% [68.6%, 78.2%] (n=319) | 45.6% ± 1.9% (43.8%–48.7%) | -28.1 pp | comparable-with-caveats |
| Share of S through vehicles crossing the entry line while their head is green | share | 31.5% [25.8%, 37.7%] (n=232) | 34.5% ± 4.5% (27.2%–38.1%) | +3.1 pp | comparable-with-caveats |

Method: through vehicles' inbound crossings of the 100 m entry line (as in the segment) during their arm's 'all' green, over those with a known state.

Limitations:
- observed signal states are estimated from the same crossings (no head was read)
- SUMO repeats one 99 s cycle cut from the estimate; observed onset spacing ranged 80-201 s (apparently actuated control)
- the entry line is 6-8 s of free driving upstream of the stop line, so this describes the arrival pattern, not arrival at the stop line
- SUMO demand departs as a Poisson process 200 m out; real arrivals come from the upstream signals, outside the study box

### Stopped time in the segment

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Mean time below 1 m/s in the segment, N left | s | 44.33 [36.53, 51.80] (n=121) | 54.29 ± 16.09 (41.72–80.68) | +9.96 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, N through | s | 8.42 [5.00, 12.00] (n=323) | 16.94 ± 1.13 (15.96–18.71) | +8.51 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, N right | s | 1.75 [0.20, 4.27] (n=28) | 1.38 ± 0.56 (0.67–2.14) | -0.37 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, S left | s | 38.62 [24.86, 56.08] (n=13) | 34.24 ± 6.94 (27.29–42.62) | -4.37 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, S through | s | 23.48 [19.39, 28.36] (n=246) | 24.56 ± 2.42 (22.81–28.19) | +1.07 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, S right | s | 3.62 [2.13, 5.40] (n=82) | 5.29 ± 1.39 (3.61–7.26) | +1.67 | comparable-with-caveats |

Method: from the inbound crossing 100 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides

### Stops per vehicle

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Stops per vehicle in the segment, N left | stops per veh | 0.84 [0.72, 0.95] (n=121) | 1.09 ± 0.12 (0.96–1.26) | +0.25 | comparable-with-caveats |
| Stops per vehicle in the segment, N through | stops per veh | 0.33 [0.16, 0.50] (n=323) | 0.65 ± 0.02 (0.63–0.69) | +0.32 | comparable-with-caveats |
| Stops per vehicle in the segment, N right | stops per veh | 0.18 [0.04, 0.36] (n=28) | 0.51 ± 0.15 (0.33–0.72) | +0.33 | comparable-with-caveats |
| Stops per vehicle in the segment, S left | stops per veh | 0.92 [0.64, 1.17] (n=13) | 0.80 ± 0.11 (0.64–0.94) | -0.12 | comparable-with-caveats |
| Stops per vehicle in the segment, S through | stops per veh | 0.75 [0.67, 0.82] (n=246) | 0.73 ± 0.04 (0.70–0.81) | -0.02 | comparable-with-caveats |
| Stops per vehicle in the segment, S right | stops per veh | 0.41 [0.28, 0.54] (n=82) | 1.01 ± 0.13 (0.83–1.18) | +0.59 | comparable-with-caveats |

Method: from the inbound crossing 100 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides

### Stopped fraction

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Share of vehicles stopping at least once in the segment, N left | share | 82.6% [74.9%, 88.4%] (n=121) | 94.6% ± 3.4% (89.7%–98.5%) | +11.9 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, N through | share | 31.9% [27.0%, 37.2%] (n=323) | 64.8% ± 2.5% (62.4%–68.6%) | +32.9 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, N right | share | 14.3% [5.7%, 31.5%] (n=28) | 46.2% ± 13.3% (33.3%–65.5%) | +31.9 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, S left | share | 76.9% [49.7%, 91.8%] (n=13) | 80.1% ± 10.6% (64.3%–93.8%) | +3.2 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, S through | share | 74.8% [69.0%, 79.8%] (n=246) | 73.4% ± 4.4% (70.1%–80.7%) | -1.4 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, S right | share | 41.5% [31.4%, 52.3%] (n=82) | 63.6% ± 3.7% (59.5%–69.1%) | +22.1 pp | comparable-with-caveats |

Method: from the inbound crossing 100 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides

### Right turn on red

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Share of right turners crossing the stop line on red, N | share | 28.6% [15.3%, 47.1%] (n=28) | 46.2% ± 13.3% (33.3%–65.5%) | +17.6 pp | comparable-with-caveats |
| Share of right turners crossing the stop line on red, E | share | 79.4% [63.2%, 89.7%] (n=34) | 68.2% ± 5.9% (58.8%–74.0%) | -11.3 pp | comparable-with-caveats |
| Share of right turners crossing the stop line on red, S | share | 51.9% [41.1%, 62.4%] (n=81) | 56.2% ± 5.8% (48.2%–62.3%) | +4.3 pp | comparable-with-caveats |
| Share of right turners crossing the stop line on red, W | share | 33.3% [6.1%, 79.2%] (n=3) | 22.3% ± 13.7% (0.0%–33.3%) | -11.0 pp | comparable-with-caveats |

Method: right turners' stop-line crossings while their arm's 'all' head shows red, over those with a known state (ngsim_junction.consistency on both sides).

Limitations:
- observed signal states are estimated from the same crossings (no head was read)
- right turn on red is a synthetic switch in SUMO; its gap acceptance is SUMO's
- no pedestrians are simulated
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)

### Jam spacing

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Front-to-front spacing of consecutive queued vehicles at the end of red, N | m per veh | 7.37 [7.08, 7.75] (n=125) | 7.51 ± 0.00 (7.50–7.51) | +0.13 | comparable-with-caveats |
| Front-to-front spacing of consecutive queued vehicles at the end of red, S | m per veh | 7.49 [7.26, 7.75] (n=122) | 7.50 ± 0.00 (7.50–7.50) | +0.01 | comparable-with-caveats |

Method: in every end-of-red lane queue of two or more vehicles, the distance along the arm between consecutive queued vehicles' fronts (vehicle length plus gap).

Limitations:
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO's spacing is its vehicle length plus minGap (passenger 5.0 + 2.5 m, truck 10.0 + 2.5 m: synthetic defaults); it converts a queue in vehicles to metres
- queued means below 1 m/s: a slowly rolling queue counts too

### Window boundaries

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Queued vehicles on the N approach at the window start | veh | 11.0 | 4.00 ± 3.39 (0.00–9.00) | -7.00 | comparable-with-caveats |
| Queued vehicles on the N approach at the window end | veh | 10.0 | 6.20 ± 5.72 (0.00–15.0) | -3.80 | comparable-with-caveats |
| Queued vehicles on the S approach at the window start | veh | 2.00 | 0.80 ± 0.84 (0.00–2.00) | -1.20 | comparable-with-caveats |
| Queued vehicles on the S approach at the window end | veh | 0.00 | 0.60 ± 0.89 (0.00–2.00) | +0.60 | comparable-with-caveats |

Method: queued vehicles summed over the arm's lanes in the first or last sampled second.

Limitations:
- the observed window starts and ends in live traffic; SUMO starts empty at 0 s and is measured after a 300 s warm-up, with demand at the observed rate throughout
- a single observed window: no interval
- observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored (a lower bound); simulated lanes are cut to the same depth, so both are censored alike
- one instant of a cyclic process: depends on the signal phase

## What cannot be validated

- **mean delay (decision metric: timeLoss + departDelay, metrics.py)**: no observed counterpart: delay needs each vehicle's free-flow travel time over its whole route, but the observed tracks cover only the ±110 m box (the cross streets only from about their limit lines) and SUMO's free-flow speed is synthetic. *Evidence needed:* observed free-flow speeds and travel times over a longer corridor (upstream and downstream detectors or tracks); then delay can be defined on both sides.
- **queues on the E and W approaches (decision metrics max/mean_queue_m.E/W)**: the E and W approaches are tracked only from about their limit lines (observed lane depth about 10-12 m beyond the gates); W's stop position is not observable. *Evidence needed:* trajectories or video covering the cross-street approaches upstream.
- **queue length beyond the observed lane ends on N and S**: observed queues are censored at the end of the observed lanes (about 81-86 m beyond the gates); any part beyond is unseen. *Evidence needed:* upstream coverage beyond the ±110 m box (detectors or tracks).
- **signal timing: green onsets, start-up lost time, cycle lengths**: the observed timeline is estimated from the same stop-line crossings (no head was read), and the simulated program is cut from that estimate: an input, not an output. *Evidence needed:* the controller's timing plan or signal-state logs for 08:30-08:45.
- **residual queues after green (cycle failures)**: observed greens are fitted to end just after their last discharge, so the observed residual share is near 0 by construction (values in details, not compared). *Evidence needed:* observed signal states (see signal timing).
- **arrival flows and throughput as validation (decision metric throughput)**: SUMO's demand is generated from the observed discharge counts, so throughput agreement is circular; arrivals into the box were not counted separately. *Evidence needed:* independent counts (another day or upstream detectors) to test the model out of sample.
- **travel time and stops on the E and W approaches**: not tracked from the segment entry (about 25 m only). *Evidence needed:* upstream coverage of the cross streets.
- **pedestrian interaction, vehicle performance and driver parameters**: no pedestrians simulated; accel, decel, minGap and sigma are SUMO defaults, visible here only through headways and travel times. *Evidence needed:* pedestrian counts and phase data; a calibration study.

## Decision metrics

- **mean_delay_s**: no observed counterpart. Segment travel time, stopped time and stops on N and S (same definitions both sides) are partial proxies, comparable with caveats
- **throughput_veh_per_h**: an observed counterpart exists (stop-line crossings in the window) but it is circular: the demand is generated from it
- **max_queue_m / mean_queue_m**: no observed counterpart with metrics.py's definition (SUMO queueing_length from SUMO's stop line). Recomputed with the observed definition: N comparable-with-caveats, E not comparable, S comparable-with-caveats, W not comparable (N and S as censored lower bounds on both sides)

## Charts

![end_of_red_queue.svg](charts/end_of_red_queue.svg)
![headways.svg](charts/headways.svg)
![lane_shares.svg](charts/lane_shares.svg)
![queue_series.svg](charts/queue_series.svg)
![travel_time.svg](charts/travel_time.svg)

Every number above is in `validation.json` with its unit, window, method, label and source files with SHA-256.
