# NGSIM intersection 2: SUMO baseline against the observations

Created 2026-10-28T13:28:16+00:00 by `scripts/validate_ngsim_int2_baseline.py`. Schema `cairoflow-baseline-validation/0.1.0`.

Decision support only. Observed values are **derived**: CairoFlow's fixed rules over the published NGSIM positions (CC BY-SA 3.0, U.S. DOT FHWA NGSIM `8ect-6jqj`). Simulated values come from SUMO output files of the validated baseline, rerun with FCD. Each quantity has a **status**, not a pass/fail score: *comparable* (same definition and meaning), *comparable-with-caveats*, or *not comparable*.

Baseline: **replay** (ADR-022) over clip [118, 751) s = simulated [0, 633) s. What changed against the repeated-cycle baseline:

- signals: SUMO replays the estimated timeline second by second over the window instead of repeating one cut (no head was read; yellow 4 s, start-up 2 s and clearance 1 s are synthetic); after it, 4 s yellow, 1 s all-red and the 99 s cut drain the network;
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty;
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed;
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car);
- movement counts are circular by construction, one for one: every vehicle counted in the observed window enters SUMO as itself (trace demand); a difference only shows vehicles SUMO moves across the window's ends or onto another movement;
- the segment entry is 80 m from the centre (100 m for the repeated-cycle baseline): simulated vehicles enter at their first whole-second observed state, up to 14 m inside where they were first tracked, and 5-6.5 m nearer the centre on Lankershim (distance kept to SUMO's stop line).

**Summary.** 94 quantities compared with identical definitions: 13 comparable, 69 comparable-with-caveats, 12 not comparable; 8 listed as not validatable. Decision metrics with any observed counterpart: throughput (circular) and the N and S queues (recomputed with the observed definition, censored lower bounds); mean delay and the E and W queues have none.

## Inputs and identity checks

| Check | OK |
|---|---|
| record geometry.json = processed geometry.json | yes |
| record signal_timeline.json = processed signal_timeline.json | yes |
| trajectories SHA-256 = record input | yes |
| trajectories SHA-256 = SHA256SUMS | yes |
| baseline world model = record world model | yes |
| network.net.xml = manifest | yes |
| demand seed_1.rou.xml = manifest | yes |
| demand seed_2.rou.xml = manifest | yes |
| demand seed_3.rou.xml = manifest | yes |
| demand seed_4.rou.xml = manifest | yes |
| demand seed_5.rou.xml = manifest | yes |
| demand seed_6.rou.xml = manifest | yes |
| demand seed_7.rou.xml = manifest | yes |
| demand seed_8.rou.xml = manifest | yes |
| demand seed_9.rou.xml = manifest | yes |
| demand seed_10.rou.xml = manifest | yes |
| demand seed_11.rou.xml = manifest | yes |
| demand seed_12.rou.xml = manifest | yes |
| demand seed_13.rou.xml = manifest | yes |
| demand seed_14.rou.xml = manifest | yes |
| demand seed_15.rou.xml = manifest | yes |
| demand seed_16.rou.xml = manifest | yes |
| demand seed_17.rou.xml = manifest | yes |
| demand seed_18.rou.xml = manifest | yes |
| demand seed_19.rou.xml = manifest | yes |
| demand seed_20.rou.xml = manifest | yes |
| recorded seed 1 tripinfo = manifest | yes |
| recorded seed 2 tripinfo = manifest | yes |
| recorded seed 3 tripinfo = manifest | yes |
| recorded seed 4 tripinfo = manifest | yes |
| recorded seed 5 tripinfo = manifest | yes |
| recorded seed 6 tripinfo = manifest | yes |
| recorded seed 7 tripinfo = manifest | yes |
| recorded seed 8 tripinfo = manifest | yes |
| recorded seed 9 tripinfo = manifest | yes |
| recorded seed 10 tripinfo = manifest | yes |
| recorded seed 11 tripinfo = manifest | yes |
| recorded seed 12 tripinfo = manifest | yes |
| recorded seed 13 tripinfo = manifest | yes |
| recorded seed 14 tripinfo = manifest | yes |
| recorded seed 15 tripinfo = manifest | yes |
| recorded seed 16 tripinfo = manifest | yes |
| recorded seed 17 tripinfo = manifest | yes |
| recorded seed 18 tripinfo = manifest | yes |
| recorded seed 19 tripinfo = manifest | yes |
| recorded seed 20 tripinfo = manifest | yes |
| signal program offset (phase 0 starts at 0 s) | yes |
| replay window [118, 751) s: every head of the record's timeline known | yes |
| seconds where the replay program differs from the record's timeline | yes |
| trace_vehicles.json = demand notes | yes |
| frame bearing = record | yes |
| vehicle runs in the study box = record | yes |
| rebuilt world model = record world_model.json | yes |
| simulated through crossings during green or yellow of the rebuilt SUMO program (share >= 0.99) | yes |

| Seed | Recorded tripinfo SHA-256 | Rerun (fcd device entries removed) | Entries removed / trips | Same run |
|---|---|---|---|---|
| 1 | `291a9194508e3d99…` | `291a9194508e3d99…` | 731 / 731 | yes |
| 2 | `4edc0df164f39acf…` | `4edc0df164f39acf…` | 731 / 731 | yes |
| 3 | `fd2c53703f0fc224…` | `fd2c53703f0fc224…` | 731 / 731 | yes |
| 4 | `544b8367c3fb49fa…` | `544b8367c3fb49fa…` | 731 / 731 | yes |
| 5 | `da1af10d4ead1eeb…` | `da1af10d4ead1eeb…` | 731 / 731 | yes |
| 6 | `869efcadc502ac19…` | `869efcadc502ac19…` | 731 / 731 | yes |
| 7 | `4d2d389ccf5bf9c3…` | `4d2d389ccf5bf9c3…` | 731 / 731 | yes |
| 8 | `119a966c411d0d60…` | `119a966c411d0d60…` | 731 / 731 | yes |
| 9 | `e9cce2219f73d9a8…` | `e9cce2219f73d9a8…` | 731 / 731 | yes |
| 10 | `7a66a85e170e48f7…` | `7a66a85e170e48f7…` | 731 / 731 | yes |
| 11 | `bdd680c67e9722a7…` | `bdd680c67e9722a7…` | 731 / 731 | yes |
| 12 | `0289141a0416d920…` | `0289141a0416d920…` | 731 / 731 | yes |
| 13 | `cadf5e0a76efb2ea…` | `cadf5e0a76efb2ea…` | 731 / 731 | yes |
| 14 | `959f4b5fec63f550…` | `959f4b5fec63f550…` | 731 / 731 | yes |
| 15 | `ee25772473286fbe…` | `ee25772473286fbe…` | 731 / 731 | yes |
| 16 | `705e938d86459450…` | `705e938d86459450…` | 731 / 731 | yes |
| 17 | `7b6e9255601253e0…` | `7b6e9255601253e0…` | 731 / 731 | yes |
| 18 | `64c1494b5170d03e…` | `64c1494b5170d03e…` | 731 / 731 | yes |
| 19 | `7124e36fe2f5bb68…` | `7124e36fe2f5bb68…` | 731 / 731 | yes |
| 20 | `ad730a72ccafad67…` | `ad730a72ccafad67…` | 731 / 731 | yes |

Windows: observed 2005-06-16 08:30:00 PDT + [118, 751) s (clip seconds), shifted to [0, 633) s; simulated [0, 633) s of each seed: the replay itself (no warm-up).

Simulated stop lines (observed rule on SUMO's stopped vehicles) against SUMO's lane ends and the observed gates, metres from the centre:

| Arm | Observed gate | SUMO lane end | Simulated counting line | Pooled heads |
|---|---|---|---|---|
| N | 20.472 | 13.98 | 14.48 | 39029 |
| E | 13.368 | 19.32 | 18.92 | 2589 |
| S | 20.226 | 15.15 | 15.65 | 37953 |
| W | 16.521 | 22.06 | 22.56 | 20953 |

## Comparisons

Observed: estimate [95% interval] (n). Simulated: mean ± SD over seeds (range). Discrepancy: simulated minus observed. ≥ marks a censored lower bound.

### Movement counts (demand-generator sanity check)

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Counted vehicles, N left | veh per 633 s | 97.0 [78.7, 118.3] (n=97) | 100.2 ± 0.77 (99.0–101.0) | +3.20 | comparable |
| Counted vehicles, N through | veh per 633 s | 231.0 [202.2, 262.8] (n=231) | 225.9 ± 0.83 (224.0–228.0) | -5.05 | comparable |
| Counted vehicles, N right | veh per 633 s | 18.0 [10.7, 28.4] (n=18) | 18.3 ± 0.47 (18.0–19.0) | +0.30 | comparable |
| Counted vehicles, E left | veh per 633 s | 4.00 [1.09, 10.2] (n=4) | 4.00 ± 0.00 (4.00–4.00) | +0.00 | comparable |
| Counted vehicles, E through | veh per 633 s | 5.00 [1.62, 11.7] (n=5) | 5.00 ± 0.00 (5.00–5.00) | +0.00 | comparable |
| Counted vehicles, E right | veh per 633 s | 28.0 [18.6, 40.5] (n=28) | 29.0 ± 0.00 (29.0–29.0) | +1.00 | comparable |
| Counted vehicles, S left | veh per 633 s | 12.0 [6.20, 21.0] (n=12) | 12.0 ± 0.00 (12.0–12.0) | +0.00 | comparable |
| Counted vehicles, S through | veh per 633 s | 166.0 [141.7, 193.3] (n=166) | 164.0 ± 0.00 (164.0–164.0) | -2.00 | comparable |
| Counted vehicles, S right | veh per 633 s | 58.0 [44.0, 75.0] (n=58) | 57.5 ± 0.51 (57.0–58.0) | -0.45 | comparable |
| Counted vehicles, W left | veh per 633 s | 23.0 [14.6, 34.5] (n=23) | 22.0 ± 0.00 (22.0–22.0) | -1.00 | comparable |
| Counted vehicles, W through | veh per 633 s | 30.0 [20.2, 42.8] (n=30) | 29.0 ± 0.00 (29.0–29.0) | -1.00 | comparable |
| Counted vehicles, W right | veh per 633 s | 3.00 [0.62, 8.77] (n=3) | 3.00 ± 0.00 (3.00–3.00) | +0.00 | comparable |
| Counted vehicles, total | veh per 633 s | 675.0 [625.0, 727.9] (n=675) | 670.0 ± 1.30 (667.0–673.0) | -5.00 | comparable |

Method: vehicles whose first stop-line crossing and a later exit-line crossing both fall in the window (world_model.count_movements rule, identical code on both sides).

Limitations:
- circular by construction, one for one: every vehicle counted in the observed window enters SUMO as itself (trace demand); a difference only shows vehicles SUMO moves across the window's ends or onto another movement
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Lane use at the stop line

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Lane share at the stop line, N approach, lane N11 | share | 11.3% [8.4%, 15.0%] (n=346) | 14.2% ± 0.3% (13.7%–14.8%) | +2.9 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N12 | share | 16.8% [13.2%, 21.1%] (n=346) | 14.9% ± 0.4% (14.2%–15.4%) | -1.9 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N1 | share | 21.1% [17.1%, 25.7%] (n=346) | 21.5% ± 0.4% (20.5%–22.0%) | +0.4 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N2 | share | 21.1% [17.1%, 25.7%] (n=346) | 21.7% ± 0.6% (20.3%–22.6%) | +0.6 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N3 | share | 24.9% [20.6%, 29.7%] (n=346) | 22.5% ± 0.5% (21.5%–23.5%) | -2.4 pp | comparable-with-caveats |
| Lane share at the stop line, N approach, lane N31 | share | 4.9% [3.1%, 7.7%] (n=346) | 5.3% ± 0.1% (5.2%–5.6%) | +0.4 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane E1 | share | 8.1% [2.8%, 21.3%] (n=37) | 10.5% ± 0.0% (10.5%–10.5%) | +2.4 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane E2 | share | 16.2% [7.7%, 31.1%] (n=37) | 13.0% ± 0.6% (10.5%–13.2%) | -3.2 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane E3 | share | 75.7% [59.9%, 86.6%] (n=37) | 76.3% ± 0.0% (76.3%–76.3%) | +0.6 pp | comparable-with-caveats |
| Lane share at the stop line, E approach, lane unknown | share | 0.0% [0.0%, 9.4%] (n=37) | 0.1% ± 0.6% (0.0%–2.6%) | +0.1 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S11 | share | 5.1% [2.9%, 8.7%] (n=236) | 5.1% ± 0.0% (5.1%–5.2%) | +0.1 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S1 | share | 15.3% [11.2%, 20.4%] (n=236) | 23.3% ± 0.7% (22.2%–24.9%) | +8.0 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S2 | share | 24.2% [19.1%, 30.0%] (n=236) | 23.2% ± 0.6% (21.8%–24.0%) | -0.9 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S3 | share | 31.4% [25.8%, 37.5%] (n=236) | 23.7% ± 0.4% (22.7%–24.5%) | -7.6 pp | comparable-with-caveats |
| Lane share at the stop line, S approach, lane S4 | share | 24.2% [19.1%, 30.0%] (n=236) | 24.6% ± 0.2% (24.5%–24.8%) | +0.5 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane W1 | share | 41.1% [29.2%, 54.1%] (n=56) | 40.2% ± 0.9% (38.9%–40.7%) | -0.9 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane W2 | share | 16.1% [8.7%, 27.8%] (n=56) | 28.7% ± 1.1% (25.9%–29.6%) | +12.6 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane W3 | share | 41.1% [29.2%, 54.1%] (n=56) | 29.3% ± 1.3% (24.1%–29.6%) | -11.8 pp | comparable-with-caveats |
| Lane share at the stop line, W approach, lane unknown | share | 1.8% [0.3%, 9.4%] (n=56) | 1.9% ± 1.9% (0.0%–7.4%) | +0.1 pp | comparable-with-caveats |

Method: share of the approach's counted vehicles by the lane polygon holding their last position within 2 s before the stop-line crossing (world_model rule); simulated lanes are SUMO's, named after the NGSIM lane they were compiled from.

Limitations:
- SUMO chooses lanes itself (departLane 'best', its lane-change model); the observed choice also reflects upstream conditions outside the study box
- simulated lanes sit up to about 1 m across from the NGSIM lane centres
- vehicles within a platoon are not independent; the interval is too narrow
- near-circular: each simulated vehicle enters in the lane it used at the observed stop line where SUMO connects that lane to its exit ('best' otherwise), so this tests SUMO's lane changes over about 100 m
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Discharge headways

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Mean discharge headway, N through lanes, queue positions k >= 5 | s | 1.69 [1.53, 2.06] (n=10) | 2.02 ± 0.06 (1.79–2.15) | +0.32 | comparable-with-caveats |
| Mean discharge headway, N through lanes, queue positions k = 2-4 | s | 3.61 [2.45, 5.08] (n=21) | 2.80 ± 0.05 (2.68–2.87) | -0.80 | comparable-with-caveats |
| Mean discharge headway, E through lanes, queue positions k >= 5 | s | n/a (n=0) | n/a | n/a | not comparable |
| Mean discharge headway, E through lanes, queue positions k = 2-4 | s | 1.92 (n=1) | n/a | n/a | not comparable |
| Mean discharge headway, S through lanes, queue positions k >= 5 | s | 2.06 [1.91, 2.27] (n=46) | 1.98 ± 0.02 (1.95–2.04) | -0.08 | comparable-with-caveats |
| Mean discharge headway, S through lanes, queue positions k = 2-4 | s | 2.31 [2.17, 2.49] (n=47) | 2.68 ± 0.02 (2.64–2.73) | +0.37 | comparable-with-caveats |
| Mean discharge headway, W through lanes, queue positions k >= 5 | s | n/a (n=0) | n/a | n/a | not comparable |
| Mean discharge headway, W through lanes, queue positions k = 2-4 | s | n/a (n=0) | 3.37 ± 0.09 (3.20–3.53) | n/a | not comparable |

Method: per end-of-red queue in a through-only lane: its queued vehicles' stop-line crossings in the green and yellow that follow, in crossing order; headway k = crossing k - crossing k-1 (k >= 2); position 1 (start-up) is left out: the observed green onsets are estimated from these crossings.

Limitations:
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- only vehicles queued at the end of red; vehicles joining the moving queue are left out
- simulated discharge follows SUMO's Krauss defaults (accel 2.6 m/s2, minGap 2.5 m, tau 1 s): uncalibrated
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)
- too few observed queues this long (fewer than 10 headways, or a seed without any)
- Evidence needed where not comparable: longer observed queues on these lanes, or a longer window

### Discharge per green

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Stop-line crossings per green (green + yellow), N all head | veh per green | 40.3 [35.8, 44.8] (n=6) | 39.3 ± 0.15 (39.0–39.7) | -1.03 | comparable-with-caveats |
| Stop-line crossings per green (green + yellow), E all head | veh per green | 3.00 [2.40, 3.60] (n=5) | 2.69 ± 0.18 (2.40–3.00) | -0.31 | not comparable |
| Stop-line crossings per green (green + yellow), S all head | veh per green | 31.8 [27.8, 34.3] (n=6) | 32.1 ± 0.19 (31.7–32.5) | +0.23 | comparable-with-caveats |
| Stop-line crossings per green (green + yellow), W all head | veh per green | 6.80 [4.20, 10.4] (n=5) | 10.2 ± 0.06 (10.0–10.2) | +3.38 | not comparable |

Method: crossings by the movements the arm's 'all' head controls, inside each green that follows a red, up to the end of its yellow.

Limitations:
- SUMO replays the estimated timeline second by second over the window instead of repeating one cut (no head was read; yellow 4 s, start-up 2 s and clearance 1 s are synthetic); after it, 4 s yellow, 1 s all-red and the 99 s cut drain the network
- observed signal states are estimated from the same crossings (no head was read)
- observed green windows are fitted to these crossings (start 2 s before the first, yellow ends just after the last)
- circular by construction, one for one: every vehicle counted in the observed window enters SUMO as itself (trace demand); a difference only shows vehicles SUMO moves across the window's ends or onto another movement
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)
- an observed green exists only where at least 2 vehicles crossed (sparser windows were dropped), so on this low-volume arm the observed mean is biased upwards by construction
- Evidence needed where not comparable: the signal states of this arm (controller logs)

### Queue at the end of red

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Queued vehicles at the end of red, E all head (mean over red ends) | veh | ≥ 2.20 [1.60, 2.80] (n=5) | ≥ 1.22 ± 0.28 (0.60–1.80) | -0.98 | not comparable |
| Queued vehicles at the end of red, N all head (mean over red ends) | veh | ≥ 7.50 [3.33, 12.5] (n=6) | 10.5 ± 0.21 (10.2–10.8) | +2.99 | comparable-with-caveats |
| Queued vehicles at the end of red, N left head (mean over red ends) | veh | ≥ 12.4 [10.4, 14.4] (n=7) | ≥ 14.4 ± 0.21 (14.1–14.9) | +1.98 | comparable-with-caveats |
| Queued vehicles at the end of red, S all head (mean over red ends) | veh | ≥ 19.0 [15.8, 22.5] (n=6) | 19.4 ± 0.70 (18.8–20.7) | +0.38 | comparable-with-caveats |
| Queued vehicles at the end of red, S left head (mean over red ends) | veh | 0.80 [0.40, 1.00] (n=5) | 2.00 ± 0.00 (2.00–2.00) | +1.20 | comparable-with-caveats |
| Queued vehicles at the end of red, W all head (mean over red ends) | veh | ≥ 0.80 [0.20, 1.40] (n=5) | ≥ 4.86 ± 0.15 (4.60–5.00) | +4.06 | not comparable |

Method: per red end, vehicles queued in the lanes following that head, summed (world_model rule: below 1 m/s on a centred 1 s window, chained back from the stop line while each is within 12 m of the previous), in the last sample before green.

Limitations:
- the E approach is observed only 11.6 m beyond its gate (one or two vehicles): queues there are not observable
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored (a lower bound); simulated lanes are cut to the same depth, so both are censored alike
- SUMO replays the estimated timeline second by second over the window instead of repeating one cut (no head was read; yellow 4 s, start-up 2 s and clearance 1 s are synthetic); after it, 4 s yellow, 1 s all-red and the 99 s cut drain the network
- observed signal states are estimated from the same crossings (no head was read)
- the queue at the end of red grows with the red's length; observed reds vary, simulated ones replay them
- the W approach is observed only 9.7 m beyond its gate (one or two vehicles): queues there are not observable
- Evidence needed where not comparable: trajectories or video covering the E approach upstream
- Evidence needed where not comparable: trajectories or video covering the W approach upstream

### Queue each second (observed definition)

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Max over the window of the arm's longest lane queue each second, N | m | ≥ 83.9 (n=633) | ≥ 75.1 ± 7.55 (68.0–84.2) | -8.84 | comparable-with-caveats |
| Mean over the window of the arm's longest lane queue each second, N | m | ≥ 34.8 [20.7, 45.8] (n=633) | ≥ 28.3 ± 0.66 (27.0–29.4) | -6.53 | comparable-with-caveats |
| Max over the window of the arm's longest lane queue each second, E | m | ≥ 10.0 (n=633) | ≥ 9.14 ± 0.44 (8.22–9.82) | -0.89 | not comparable |
| Mean over the window of the arm's longest lane queue each second, E | m | ≥ 3.79 [2.28, 5.61] (n=633) | ≥ 0.30 ± 0.07 (0.20–0.54) | -3.49 | not comparable |
| Max over the window of the arm's longest lane queue each second, S | m | ≥ 80.1 (n=633) | ≥ 69.4 ± 2.35 (68.0–75.7) | -10.71 | comparable-with-caveats |
| Mean over the window of the arm's longest lane queue each second, S | m | ≥ 24.8 [18.9, 30.8] (n=633) | ≥ 18.1 ± 0.20 (17.8–18.6) | -6.69 | comparable-with-caveats |
| Max over the window of the arm's longest lane queue each second, W | m | ≥ 8.70 (n=633) | ≥ 8.83 ± 0.25 (8.44–9.35) | +0.13 | not comparable |
| Mean over the window of the arm's longest lane queue each second, W | m | ≥ 0.97 [0.48, 1.64] (n=633) | ≥ 3.76 ± 0.02 (3.72–3.82) | +2.80 | not comparable |

Method: per second, the longest approach-lane queue of the arm (world_model rule, as the record's observed queue metrics); the max or mean over the window's seconds.

Limitations:
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored (a lower bound); simulated lanes are cut to the same depth, so both are censored alike
- SUMO replays the estimated timeline second by second over the window instead of repeating one cut (no head was read; yellow 4 s, start-up 2 s and clearance 1 s are synthetic); after it, 4 s yellow, 1 s all-red and the 99 s cut drain the network
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- length runs to the front of the last queued vehicle (one vehicle length short of its rear) and excludes the 2-4 m from the gate to where the first vehicle stands
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)
- the E approach is observed only 11.6 m beyond its gate
- the W approach is observed only 9.7 m beyond its gate
- Evidence needed where not comparable: trajectories or video covering the E approach upstream
- Evidence needed where not comparable: trajectories or video covering the W approach upstream

### Segment travel time

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Mean segment travel time, N left | s | 56.28 [49.33, 63.00] (n=83) | 64.62 ± 1.30 (61.83–66.72) | +8.34 | comparable-with-caveats |
| Mean segment travel time, N through | s | 17.16 [12.26, 23.48] (n=224) | 18.90 ± 0.40 (18.23–19.82) | +1.73 | comparable-with-caveats |
| Mean segment travel time, N right | s | 11.01 [8.62, 14.77] (n=18) | 10.07 ± 0.34 (9.29–10.94) | -0.94 | comparable-with-caveats |
| Mean segment travel time, S left | s | 46.41 [34.52, 64.66] (n=11) | 70.09 ± 0.14 (69.90–70.47) | +23.68 | comparable-with-caveats |
| Mean segment travel time, S through | s | 37.03 [31.00, 40.56] (n=160) | 40.73 ± 0.46 (39.71–41.65) | +3.70 | comparable-with-caveats |
| Mean segment travel time, S right | s | 13.73 [11.24, 15.69] (n=57) | 14.02 ± 0.60 (12.91–15.03) | +0.29 | comparable-with-caveats |

Method: from the inbound crossing 80 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Arrivals on green at the segment entry

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Share of N through vehicles crossing the entry line while their head is green | share | 81.2% [75.6%, 85.8%] (n=224) | 76.2% ± 0.2% (75.7%–76.7%) | -5.0 pp | comparable-with-caveats |
| Share of S through vehicles crossing the entry line while their head is green | share | 35.6% [28.6%, 43.3%] (n=160) | 28.1% ± 0.6% (27.2%–28.5%) | -7.5 pp | comparable-with-caveats |

Method: through vehicles' inbound crossings of the 80 m entry line (as in the segment) during their arm's 'all' green, over those with a known state.

Limitations:
- observed signal states are estimated from the same crossings (no head was read)
- SUMO replays the estimated timeline second by second over the window instead of repeating one cut (no head was read; yellow 4 s, start-up 2 s and clearance 1 s are synthetic); after it, 4 s yellow, 1 s all-red and the 99 s cut drain the network
- the entry line is 6-8 s of free driving upstream of the stop line, so this describes the arrival pattern, not arrival at the stop line
- simulated vehicles enter where and when they were first observed (Lankershim about 105-110 m out, at SUMO's safe speed, not the observed one), so arrivals at the entry line are largely an input check, not a test
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Stopped time in the segment

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Mean time below 1 m/s in the segment, N left | s | 41.27 [35.00, 47.53] (n=83) | 51.56 ± 1.20 (48.78–53.41) | +10.30 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, N through | s | 7.42 [3.41, 12.67] (n=224) | 8.61 ± 0.36 (8.06–9.44) | +1.19 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, N right | s | 1.89 [0.00, 5.00] (n=18) | 1.25 ± 0.24 (0.83–1.79) | -0.64 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, S left | s | 31.09 [20.00, 47.71] (n=11) | 58.26 ± 0.19 (57.91–58.55) | +27.17 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, S through | s | 23.00 [17.46, 26.24] (n=160) | 28.07 ± 0.44 (27.17–28.93) | +5.07 | comparable-with-caveats |
| Mean time below 1 m/s in the segment, S right | s | 3.00 [1.53, 4.19] (n=57) | 4.09 ± 0.49 (3.20–5.11) | +1.09 | comparable-with-caveats |

Method: from the inbound crossing 80 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Stops per vehicle

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Stops per vehicle in the segment, N left | stops per veh | 0.78 [0.70, 0.89] (n=83) | 0.93 ± 0.01 (0.91–0.95) | +0.15 | comparable-with-caveats |
| Stops per vehicle in the segment, N through | stops per veh | 0.25 [0.14, 0.37] (n=224) | 0.32 ± 0.01 (0.30–0.35) | +0.07 | comparable-with-caveats |
| Stops per vehicle in the segment, N right | stops per veh | 0.17 [0.00, 0.38] (n=18) | 0.44 ± 0.03 (0.37–0.53) | +0.27 | comparable-with-caveats |
| Stops per vehicle in the segment, S left | stops per veh | 0.91 [0.60, 1.29] (n=11) | 0.82 ± 0.00 (0.82–0.82) | -0.09 | comparable-with-caveats |
| Stops per vehicle in the segment, S through | stops per veh | 0.67 [0.62, 0.72] (n=160) | 0.80 ± 0.01 (0.78–0.81) | +0.13 | comparable-with-caveats |
| Stops per vehicle in the segment, S right | stops per veh | 0.37 [0.20, 0.49] (n=57) | 0.77 ± 0.05 (0.68–0.86) | +0.41 | comparable-with-caveats |

Method: from the inbound crossing 80 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Stopped fraction

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Share of vehicles stopping at least once in the segment, N left | share | 78.3% [68.3%, 85.8%] (n=83) | 92.7% ± 1.0% (90.8%–94.3%) | +14.4 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, N through | share | 24.1% [19.0%, 30.1%] (n=224) | 30.6% ± 1.0% (28.6%–33.5%) | +6.5 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, N right | share | 11.1% [3.1%, 32.8%] (n=18) | 44.0% ± 3.3% (36.8%–52.6%) | +32.9 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, S left | share | 72.7% [43.4%, 90.3%] (n=11) | 81.8% ± 0.0% (81.8%–81.8%) | +9.1 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, S through | share | 66.9% [59.3%, 73.7%] (n=160) | 79.6% ± 0.8% (78.5%–81.0%) | +12.7 pp | comparable-with-caveats |
| Share of vehicles stopping at least once in the segment, S right | share | 36.8% [25.5%, 49.8%] (n=57) | 59.5% ± 1.6% (56.1%–62.5%) | +22.7 pp | comparable-with-caveats |

Method: from the inbound crossing 80 m from the centre on the approach arm to the exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must enter and leave inside the window on both sides.

Limitations:
- SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street is about 5.6 deg off perpendicular
- observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated linearly, exact for SUMO's constant speed within a step)
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free speeds are not
- queues reaching past the entry line hide part of the delay on both sides
- vehicles inside the entry line at the window start, or not across the exit line at its end, are left out on both sides
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Right turn on red

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Share of right turners crossing the stop line on red, N | share | 33.3% [16.3%, 56.3%] (n=18) | 45.9% ± 2.3% (44.4%–52.6%) | +12.5 pp | comparable-with-caveats |
| Share of right turners crossing the stop line on red, E | share | 78.6% [60.5%, 89.8%] (n=28) | 84.7% ± 3.1% (79.3%–89.7%) | +6.1 pp | comparable-with-caveats |
| Share of right turners crossing the stop line on red, S | share | 53.4% [40.8%, 65.7%] (n=58) | 50.7% ± 2.0% (46.6%–54.4%) | -2.8 pp | comparable-with-caveats |
| Share of right turners crossing the stop line on red, W | share | 33.3% [6.1%, 79.2%] (n=3) | 0.0% ± 0.0% (0.0%–0.0%) | -33.3 pp | comparable-with-caveats |

Method: right turners' stop-line crossings while their arm's 'all' head shows red, over those with a known state (ngsim_junction.consistency on both sides).

Limitations:
- observed signal states are estimated from the same crossings (no head was read)
- right turn on red is a synthetic switch in SUMO; its gap acceptance is SUMO's
- no pedestrians are simulated
- each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Jam spacing

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Front-to-front spacing of consecutive queued vehicles at the end of red, N | m per veh | 7.35 [7.04, 7.76] (n=104) | 7.51 ± 0.01 (7.50–7.52) | +0.16 | comparable-with-caveats |
| Front-to-front spacing of consecutive queued vehicles at the end of red, S | m per veh | 7.50 [7.22, 7.81] (n=94) | 7.50 ± 0.00 (7.50–7.51) | -0.00 | comparable-with-caveats |

Method: in every end-of-red lane queue of two or more vehicles, the distance along the arm between consecutive queued vehicles' fronts (vehicle length plus gap).

Limitations:
- NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)
- SUMO's spacing is its vehicle length plus minGap (passenger 5.0 + 2.5 m, truck 10.0 + 2.5 m: synthetic defaults); it converts a queue in vehicles to metres
- queued means below 1 m/s: a slowly rolling queue counts too
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Window boundaries

| Quantity | Unit | Observed | Simulated | Discrepancy | Status |
|---|---|---|---|---|---|
| Queued vehicles on the N approach at the window start | veh | 22.0 | 7.10 ± 0.31 (7.00–8.00) | -14.90 | comparable-with-caveats |
| Queued vehicles on the N approach at the window end | veh | 16.0 | 22.8 ± 1.06 (20.0–25.0) | +6.80 | comparable-with-caveats |
| Queued vehicles on the S approach at the window start | veh | 6.00 | 4.20 ± 0.70 (3.00–5.00) | -1.80 | comparable-with-caveats |
| Queued vehicles on the S approach at the window end | veh | 9.00 | 14.4 ± 0.60 (13.0–15.0) | +5.40 | comparable-with-caveats |

Method: queued vehicles summed over the arm's lanes in the first or last sampled second.

Limitations:
- the observed window starts and ends in live traffic; SUMO starts with the vehicles observed on the approaches placed in their lanes (re-packed at its spacing)
- a single observed window: no interval
- observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored (a lower bound); simulated lanes are cut to the same depth, so both are censored alike
- one instant of a cyclic process: depends on the signal phase
- observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's microscopic noise for one observed realisation, not demand uncertainty
- assumed: cross-street vehicles first seen within 40 m of the stop line (inside their queues) enter at the arm start, timed to reach that spot at the speed limit; their arrival times are not observed
- vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed per lane at SUMO's spacing (7.5 m per car)

### Same vehicles: stop-line crossing, simulated less observed

vehicles counted in the window on both sides, paired through trace_vehicles.json (SUMO vehicle id -> observed track id); lag = simulated stop-line crossing time less the observed one, both from the window start.

| Movement | Paired vehicles per seed | Median lag per seed (s) | Within ±5 s (pooled) |
|---|---|---|---|
| E left | 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4 | -2.8, -2.8, -2.9, -2.9, -2.9, -2.7, -2.8, -2.7, -2.7, -2.8, -3.0, -2.9, -2.8, -2.9, -3.0, -2.9, -2.7, -3.0, -2.9, -2.8 | 100% |
| E right | 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28 | +1.4, +3.1, +2.6, +2.8, +1.5, +2.4, +2.3, +3.4, +2.9, +2.4, +1.1, +2.8, +1.4, +3.2, +3.5, +2.4, +3.2, +1.3, +1.0, +1.4 | 54% |
| E through | 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5 | -3.4, -3.2, -3.2, -3.2, -3.2, -3.3, -2.7, -2.9, -3.2, -2.9, -3.2, -3.3, -3.2, -3.3, -3.2, -3.3, -3.2, -2.9, -2.9, -3.3 | 66% |
| N left | 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96, 96 | -1.8, -2.3, -2.3, -2.3, -1.7, -2.3, -2.1, -1.4, -1.5, -2.3, -2.0, -1.6, -2.2, -1.7, -1.8, -2.6, -1.9, -2.4, -2.3, -1.4 | 57% |
| N right | 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18, 18 | -0.5, -1.0, -1.3, -1.1, -1.1, -0.4, -1.3, -0.8, -1.3, -1.5, -1.1, -1.1, -1.5, -1.1, -0.7, -1.0, -0.9, -0.8, -0.8, -0.9 | 82% |
| N through | 226, 224, 227, 226, 226, 226, 226, 226, 226, 226, 228, 226, 225, 226, 226, 225, 226, 225, 226, 227 | +0.2, +0.3, +0.1, +0.1, +0.2, +0.0, +0.1, +0.2, +0.2, +0.1, +0.0, +0.2, +0.2, +0.2, +0.1, +0.3, +0.2, +0.2, +0.2, +0.2 | 74% |
| S left | 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12 | +1.7, +2.1, +1.6, +2.0, +1.9, +1.8, +2.2, +1.8, +1.8, +2.0, +1.9, +1.9, +2.2, +2.0, +2.0, +2.0, +2.2, +2.0, +2.3, +2.1 | 42% |
| S right | 57, 57, 58, 58, 57, 58, 57, 58, 57, 57, 58, 58, 57, 57, 57, 58, 58, 58, 58, 58 | -3.6, -2.8, -3.6, -3.1, -2.8, -3.0, -2.8, -2.5, -2.9, -2.2, -3.2, -2.9, -3.9, -2.9, -2.4, -2.5, -3.1, -2.5, -2.7, -2.5 | 57% |
| S through | 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164, 164 | -3.4, -3.8, -3.8, -3.7, -3.6, -3.6, -3.2, -3.8, -3.3, -3.5, -3.2, -3.1, -3.5, -3.3, -3.6, -3.7, -3.7, -3.4, -3.8, -3.4 | 59% |
| W left | 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22, 22 | +12.0, +11.8, +12.1, +12.1, +11.9, +12.9, +12.6, +12.8, +13.2, +12.4, +6.6, +11.8, +6.2, +12.1, +12.0, +12.8, +11.9, +12.1, +12.8, +12.5 | 46% |
| W right | 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3 | +168.1, +167.8, +168.2, +167.7, +167.9, +167.5, +167.9, +167.4, +167.6, +167.7, +167.7, +168.5, +168.3, +168.1, +167.9, +167.4, +167.9, +168.1, +168.1, +167.9 | 33% |
| W through | 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29, 29 | -0.9, -0.6, -0.5, -0.8, -0.2, -1.0, -1.2, -1.0, -0.8, -0.5, -0.7, -0.5, -0.8, -0.9, -0.7, -0.6, -0.7, -1.0, -0.9, -0.8 | 41% |

## What cannot be validated

- **mean delay (decision metric: timeLoss + departDelay, metrics.py)**: no observed counterpart: delay needs each vehicle's free-flow travel time over its whole route, but the observed tracks cover only the ±110 m box (the cross streets only from about their limit lines) and SUMO's free-flow speed is synthetic. *Evidence needed:* observed free-flow speeds and travel times over a longer corridor (upstream and downstream detectors or tracks); then delay can be defined on both sides.
- **queues on the E and W approaches (decision metrics max/mean_queue_m.E/W)**: the E and W approaches are tracked only from about their limit lines (observed lane depth about 10-12 m beyond the gates); W's stop position is not observable. *Evidence needed:* trajectories or video covering the cross-street approaches upstream.
- **queue length beyond the observed lane ends on N and S**: observed queues are censored at the end of the observed lanes (about 81-86 m beyond the gates); any part beyond is unseen. *Evidence needed:* upstream coverage beyond the ±110 m box (detectors or tracks).
- **signal timing: green onsets, start-up lost time, cycle lengths**: the observed timeline is estimated from the same stop-line crossings (no head was read), and the replay program is that estimate itself: an input, not an output. *Evidence needed:* the controller's timing plan or signal-state logs for 08:30-08:45.
- **residual queues after green (cycle failures)**: observed greens are fitted to end just after their last discharge, so the observed residual share is near 0 by construction (values in details, not compared). *Evidence needed:* observed signal states (see signal timing).
- **arrival flows and throughput as validation (decision metric throughput)**: the replay's vehicles are the observed ones, entering at their observed states, so counts and throughput agree one for one by construction. *Evidence needed:* independent counts (another day or upstream detectors) to test the model out of sample.
- **travel time and stops on the E and W approaches**: not tracked from the segment entry (about 25 m only). *Evidence needed:* upstream coverage of the cross streets.
- **pedestrian interaction, vehicle performance and driver parameters**: no pedestrians simulated; accel, decel, minGap and sigma are SUMO defaults, visible here only through headways and travel times. *Evidence needed:* pedestrian counts and phase data; a calibration study.

## Decision metrics

- **mean_delay_s**: no observed counterpart. Segment travel time, stopped time and stops on N and S (same definitions both sides) are partial proxies, comparable with caveats
- **throughput_veh_per_h**: an observed counterpart exists (stop-line crossings in the window) but it is circular one for one: the demand is the observed vehicles
- **max_queue_m / mean_queue_m**: no observed counterpart with metrics.py's definition (SUMO queueing_length from SUMO's stop line). Recomputed with the observed definition: N comparable-with-caveats, E not comparable, S comparable-with-caveats, W not comparable (N and S as censored lower bounds on both sides)

## Charts

![end_of_red_queue.svg](charts/end_of_red_queue.svg)
![headways.svg](charts/headways.svg)
![lane_shares.svg](charts/lane_shares.svg)
![queue_series.svg](charts/queue_series.svg)
![travel_time.svg](charts/travel_time.svg)

Every number above is in `validation.json` with its unit, window, method, label and source files with SHA-256.
