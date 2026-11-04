# Why camera gate v2 failed D (lateral p90 2.206 m against ≤ 1.5 m)

**Status: exploratory diagnosis, 2026-10-30. Gate v2's result is unchanged: no-go.** Every number
below comes from data gate v2 already judged, and some come from analyses chosen after seeing
them. They explain the failure; they do not confirm a fix. Only a new pre-registered gate on new
data can do that. No SAM request was sent, and no gate file, threshold, record or world model was changed.

- Script: `scripts/diagnose_camera_gate.py` (read-only), tested by `tests/test_diagnose_camera_gate.py`.
- Gate v2: `diagnosis.json`, `points.json` (all 870 measured points) and the PNG figures here.
- Gate v1 cross-check (source seconds 370–380): `../camera-gate-v1-20261030T223246Z/`.
- Before it explains anything, the script checks two things:
  - the input hashes, and the gate-file hash, match the record;
  - `compare()` reproduces the recorded metrics exactly (A 0.64, B 0.9251, C 0.8333, D 2.206 for v2; D 1.3244 for v1).

## 1. How D is computed (`packages/perception/cairoflow_perception/camera_world.py`)

| Step | Implementation |
|---|---|
| Camera point | SAM box centre ((x1+x2)/2, (y1+y2)/2) in px (`place_track`, l.108). Mapped through the camera 2 homography to ADR-017 grid metres, then to State Plane ft, then into the junction frame: grid rotated so +y has bearing 25.8215° (`Placement.to_junction`, l.66). |
| Reference | NGSIM `global_x/y` (front centre, ftUS), moved back half `v_length` along the heading from the previous 0.1 s row, in the same junction frame (`reference_at`, l.161). |
| Time | Camera frame k of the excerpt starting at source second S is world second S − 119.7 + k/10. NGSIM tick = 08:30:00.0 + round(that × 10) × 100 ms. |
| Pairing | Per frame: greedy, one-to-one, nearest first, within 3 m (`_pairs`, l.200). A track counts if it has ≥ 5 pairs and ≥ 70 % of them name one vehicle; only that vehicle's pairs are kept (`compare`, l.239). |
| Lateral | dx if the axis is N–S, else dy. The axis comes from the reference's lane arm, else the camera lane's arm, **else `|x| < |y|` of the reference centre** (`_axis_is_y`, l.191; l.252). |
| D | `numpy.percentile(|lateral|, 90)` (linear interpolation) over **all** 870 matched points: in lanes, outside lanes and inside the junction box. |

Checked without finding an error:
- **Units:** US survey foot, m.
- **Transform round trip:** to the image and back, error < 1e-6 m.
- **Heading convention:** grid bearing, rotated by −25.8215°.
- **Rounding:** the reproduced D matches the record exactly.

The only defect is the third axis rule. It applies wherever neither point has a lane, which is mainly inside the junction box.

## 2. Where the error is

Regions follow the reference centre, using existing geometry only:
- approach / departure: the centre lies inside exactly one approach or exit lane polygon;
- junction box: inside the four stop lines and in no lane;
- unknown: everything else.

Coverage = matched points ÷ reference vehicle-frames whose centre projects into the image.

| Region | Points | Tracks | Median | p90 | Max | > 1.5 m | Lane agreement | Coverage |
|---|---|---|---|---|---|---|---|---|
| **All (gate v2's D; p75 0.631)** | 870 | 16 | 0.279 | **2.206** | 2.965 | 14.5 % | 0.925 | |
| Approach | 655 | 12 | 0.256 | 0.722 | 2.634 | 7.3 % | 0.930 | 0.126 |
| Junction box | 160 | 9 | 0.463 | 2.817 | 2.965 | 43.8 % | n/a | 0.055 |
| Departure | 14 | 3 | 0.130 | 2.886 | 2.963 | 35.7 % | 0.692 | 0.014 |
| Unknown | 41 | 3 | 0.406 | 0.963 | 2.069 | 7.3 % | n/a | 0.061 |
| Through movement | 845 | 16 | 0.267 | 2.215 | 2.965 | 14.9 % | 0.923 | |
| Right turn | 25 | 2 | 0.325 | 0.617 | 1.184 | 0 % | 1.000 | |

No left turns were matched. All 126 points over 1.5 m are through movements: 70 in the junction box, 48 on approaches, 5 on departures and 3 unknown. The `diagnosis.json` file has p75 for every group, and the same tables by camera region, NGSIM `int_id/section_id`, speed, image radius and reference lane.

**By track** (`error_by_track.png`):

| Track | Points | p90 (m) | > 1.5 m | Mechanism (§4) |
|---|---|---|---|---|
| t7 | 47 | 2.287 | 47 | adjacent-lane pairing |
| t14 | 31 | 2.852 | 21 | axis fallback |
| t15 | 15 | 2.841 | 15 | axis fallback |
| t9 | 30 | 2.796 | 12 | axis fallback |
| t6 | 89 | 2.648 | 11 | axis fallback |
| t24 | 7 | 2.622 | 7 | adjacent-lane pairing |
| t12 | 12 | 2.829 | 5 | axis fallback |
| t25 | 22 | 0.758 | 2 | axis fallback |
| t4 | 36 | 1.377 | 4 | not attributed |
| t8 | 68 | 0.703 | 2 | not attributed |

Six other tracks (t1, t13, t16, t18, t21, t22; 513 points) have p90 0.04–0.69 m and none over 1.5 m.

Figures:
- `frame_errors.png`: where each point sits in the frame. The orange points (over 1.5 m) have a line to the NGSIM centre.
- `ground_errors.png`: the NGSIM centre against the camera point in the junction frame.
- `error_by_region.png`, `error_by_track.png` and `error_components.png`: the distributions above.

## 3. Junction hypothesis: inconclusive as stated, and the cause is not perception

**Location: supported.** The junction box holds 18 % of the points but 56 % of the exceedances, and its p90 is 2.82 m against 0.72 m on approaches.

**Cause: rejected as perception or turning error.**
- Every exceedance is a through movement.
- Measured across each vehicle's own heading, the junction-box points of tracks t14 and t15 are only 0.27 m and 0.47 m off (medians).
- Their gate-axis "lateral" is 2.6 m. That 2.6 m is along-road error.

**The approach lanes contribute 48 exceedances.** Excluding the junction would therefore not fix D, and an approach-only figure (0.72 m) is a filtered view, not a replacement for 2.206 m.

## 4. What causes the tail

The same 870 error vectors, resolved three ways (p90, m):

| Resolution | Along | Across |
|---|---|---|
| Gate axis | 2.372 | **2.206** (D) |
| Vehicle's own heading | 2.672 | **0.755** |
| Image vertical | 0.759 (vertical) | 2.645 (horizontal) |

Across the vehicle's own heading the camera is within 0.755 m at p90. The tail is along-road error that enters D through two mechanisms.

**(a) Axis fallback: 66 of the 126 exceedances** (t14, t15, t9, t6, t12, t25).
- Southbound vehicles cross the junction box at x ≈ −9.6 m. There they have no lane, so the gate falls back to `|x| < |y|`.
- Once |y| < 9.6 m, that rule picks the east–west axis, and their along-road error is counted as "lateral".
- Points whose gate axis is more than 30° from their heading: 81, with median 2.67 m and 82 % over 1.5 m.
- Points within 10° of their heading: 764, with p90 0.70 m.

**(b) Adjacent-lane pairing: 54 of the 126** (t7, 47 points; t24, 7 points).
- **t7:** SAM sees the car in lane N1. The greedy 3 m pairing took vehicle 537 in lane N2, 2.52 m away. The car in t7's own lane, vehicle 521, was 3.86 m away, almost all of it along the road (−3.86 m along, −0.02 m across).
- **Gate v1 cross-check:** its main tail track, t13 (27 points), shows the same pattern. It paired with a car in the next lane at 2.58 m, while the own-lane car was 4.21 m away along the road.

**What drives the along-road error: the time sync, most likely about 0.3 s off.** This is the strongest supported explanation, and it is still a hypothesis.
- **Shift test:** the NGSIM reference was read at shifted instants, keeping the gate's own pairs (`sync_shifts_s`).
  - v2: moving vehicles' median along-heading error goes from −1.98 m at 0 s to +0.04 m at −0.3 s.
  - The three directions cross zero together: at −0.3 s, EB −0.30, NB +0.22 and SB −0.09 m.
  - Gate v1, a separate 10 s window, crosses zero between −0.3 s and −0.2 s, again in all three directions.
- **Why that points to timing:** a timing error moves each moving vehicle by speed × offset along its heading, so every direction, speed and location returns to zero at the same shift. A calibration error would have to scale with each vehicle's speed to do the same.
- **Size:** at 8 m/s, 0.3 s is 2.4 m, which matches the junction tracks.
- **Caveat:** within one direction, the error does not scale cleanly with speed (SB slope +0.08 s, `along_heading_vs_speed_moving`), because location and the stopped-vehicle offset below confound it.
- **The value it implies:** −0.3 s means 08:30:00.0 = source second 120.0, not 119.7. DATA_REGISTER §15 already recorded 120.0 on 2026-10-28, before any gate: NGSIM's `frame_id` counts from 08:28:00.0. §15 judged the 0.3 s gap to be "within the method's resolution", and said it matters only for moving vehicles.
- **The whole gate procedure re-run at −0.3 s** (exploratory; the shift was chosen on these data, so this is **not a gate result**):
  - 24 of 25 tracks matched, 1,759 points;
  - p90 |lateral| 0.608 m; junction box p90 0.603 m (566 points); departure p90 0.339 m (255 points);
  - lane agreement 0.998;
  - t7 pairs with vehicle 521 in its own lane, p90 0.24 m.

  The re-run D is unstable across shifts: 0.66 m at −0.8 s, 1.79 m at −0.6 s, 0.61 m at −0.3 s. Which points the greedy pairing admits decides it, so D as defined is fragile.

**Other explanations tested:**

| Explanation | Finding |
|---|---|
| Turning | Not supported. No v2 exceedance is a turn. In v1, 18 of 45 exceedances are left turns; their track (t1) has none over 1.5 m at −0.3 s. |
| Calibration extrapolation (44 of 50 control points are on approaches, 6 in the junction box, 0 on departures) | Not supported at the level of D. At −0.3 s every region has p90 ≤ 0.64 m. The banding by distance to the nearest control point follows region, not distance. |
| Box centre vs ground contact (height parallax) | Small. Along the image vertical, p90 is 0.76 m. NB approach points carry a steady −0.61 m across-heading offset (198 points, along the image vertical): a bias worth recording, within the limit. |
| Box truncated at the image edge (14 points), track ends (78), box size changes as an occlusion or ID-switch proxy (25) | Minor: p90 of 1.72 m, 2.11 m and 0.68 / 2.88 m on small counts. No vehicle was matched by two tracks. |
| Lane geometry | Does not enter lateral error, except through the axis rule. Lane agreement is 0.93 on approaches. |
| Timestamp rounding, units | Ruled out by exact reproduction and the round trip. |

**Unexplained, and recorded as open:**
- **Stopped vehicles carry an along-heading offset that timing cannot cause:**
  - v2: NB +0.50 m, SB −1.41 m (381 points);
  - v1: NB −2.25 m, SB −1.85 m.

  It is not consistent between excerpts. It lies along the road, where ADR-018 already does not accept the calibration, and it does not enter D on a correct axis.
- **Gate v1 track t13 is not resolved by the shift:** its own-lane car is ahead of the camera point (+4.15 m along heading), so the cause there is still open.
- **Six v2 exceedances** (t4 and t8) are not attributed to either mechanism.

## 5. Recommended correction

The defects are in the measurement, not in SAM's lateral placement. Two of the three corrections are needed whatever the sync turns out to be:

1. **Measure lateral error across the reference vehicle's own heading.** The NGSIM heading is already computed for every reference. This removes the arm and `|x| < |y|` fallback. Keep the v2 axis value as a reported, non-deciding figure.
2. **Pair whole tracks, not frames.** Assign each camera track to one NGSIM vehicle, one-to-one, using a cost that weights across-road distance more than along-road distance. Then an along-road offset cannot hand a track to the adjacent lane.
   - **Risk:** pairing on across-road distance favours small lateral errors.
   - **Mitigation:** report the v2 isotropic pairing as a sensitivity check, and the share of tracks whose partner differs between the two.
3. **Choose the time sync before any new gate data, by a rule independent of camera-gate results.** Either adopt the `frame_id` value (120.0 s) as an ADR, or re-estimate the offset at finer resolution on the d300 calibration clip.

New code must sit beside the v1/v2 code path, not replace it, so that the v1 and v2 records still reproduce bit for bit; `tests/test_diagnose_camera_gate.py` guards that for v2.

## 6–7. Gate v3: warranted, with a proposed pre-registration (not implemented)

Gate v3 is warranted, because the v2 failure is mostly a measurement artefact that a pre-registered fix can test. To be written and committed **before** any SAM request on its data:

- **Data:**
  - one new 30 s camera 2 excerpt, proposed by rule: source seconds 600–630;
  - it overlaps neither gate excerpt (370–380, 390–420) nor the d300 calibration and sync clip (60–360);
  - one SAM request, at about $0.06, sent only with your approval;
  - the v2 data are exploration only and are not pooled.
- **Sync:** the value fixed by correction 3. The other candidate is reported as a sensitivity check and decides nothing.
- **Measures and thresholds:** unchanged from v1/v2 (A ≥ 0.60; B ≥ 0.85 over ≥ 100 points; C ≥ 0.80 over ≥ 8 tracks; D ≤ 1.5 m), with D redefined as correction 1, over **all** matched points.
- **Inclusion and exclusion:** every matched point counts, in or out of lanes and in the junction box. No new exclusions. Truncated boxes and track ends are reported separately but stay in.
- **Junction:**
  - D is also reported per region (approach, junction box, departure, unknown);
  - the junction box must also meet D ≤ 1.5 m if it has ≥ 50 points from ≥ 3 tracks, and is reported as "insufficient" otherwise;
  - the overall D still decides; no region may replace it.
- **Reported, not deciding:**
  - D as v2 computed it;
  - 2-D error p90;
  - along-heading median for moving and for stopped vehicles;
  - per-track tables;
  - D under v2's isotropic pairing.
- **Predictions that would refute the timing explanation, stated in advance:**
  1. At the registered sync, the median along-heading error of moving vehicles lies within ±0.75 m in every direction with ≥ 30 points.
  2. At the other sync candidate, that median lies beyond ±1.0 m.

  If either fails, the timing explanation is wrong, whatever the go result.
- **Preservation:** v1 and v2 gate files, code paths and records stay as they are; v3 is a new file and a new record.

## 8. Confirmation

- No SAM request and no Muse request were sent.
- No gate file, threshold, gate result, record, world model or camera-derived World Model entity was changed or added.
- The only new files are this folder, the v1 cross-check folder, the read-only script and its tests.
