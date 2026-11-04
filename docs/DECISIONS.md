# Architecture Decision Log

> This log was kept while the project was built. Some working notes and records it cites are not in
> this repository. Commit hashes and branch names refer to the project's earlier history, and
> M numbers are its milestones.

Record date, decision, alternatives, rationale, consequences, and evidence for each decision.

## ADR-001 — Hosted Meta Model API first
Decision: use hosted SAM 3.1 and Muse Spark for the initial prototype.
Reason: reduce GPU setup risk and prioritize model integration and the full decision loop.
Revisit if API quotas, latency, cost, or rules make hosted inference unsuitable.

## ADR-002 — SUMO as the source of simulated outcomes
Decision: use SUMO with TraCI/libsumo for intervention testing.
Reason: controllable microscopic simulation and Python integration.
Consequence: simulation calibration and assumptions must be documented; outcomes are not real-world guarantees.

## ADR-003 — Modular monolith
Decision: FastAPI application with well-separated Python packages, not microservices.
Reason: lower integration and deployment risk within ten days.
Revisit only if a concrete performance or deployment constraint requires it.

## ADR-004 — Decision support only
Decision: no real traffic-signal actuation or direct infrastructure control.
Reason: safety, authorization, and hackathon prototype scope.

## ADR-005 — Python SAM parser, not the TypeScript package (2026-10-26)
Decision: use the official dependency-free Python port `meta-sam-parser` for SAM output. Node is only for the web app.
Alternatives: `@meta-sam/parser` (TypeScript) behind a Node subprocess; hand-parsing the grammar.
Reason: the pipeline is Python; both parsers are official and share conformance fixtures; the grammar uses base85 payloads that break naive splitting.
Consequence: attribute names on mask records must be confirmed against the installed package before the adapter is written. The SAM License must be read before cached outputs are redistributed.
Evidence: docs/research/meta-model-api.md.

## ADR-006 — Hand-authored SUMO plain XML plus netconvert (2026-10-26)
Decision: build the intersection from plain XML nodes, edges, connections and tllogic through `netconvert`.
Alternatives: `netgenerate` grid; OpenStreetMap import.
Reason: exact control of lanes, turn pockets and signal programs, and an auditable network. No verified `netgenerate` single-intersection recipe was found. OSM import is already on the scope-cut list.
Consequence: lane geometry must be measured or estimated from the clip and documented.
Evidence: docs/research/sumo-and-geometry.md.
Implemented: packages/simulation (see packages/simulation/README.md). Geometry and the signal plan are placeholders, not measurements. The signal program is generated from the link table that the first netconvert pass produces, and `find_conflicting_greens` is the deterministic validator for future signal-plan interventions.

## ADR-007 — TraCI first, libsumo later (2026-10-26)
Decision: use TraCI while building the baseline; switch to libsumo for multi-seed batches once stable.
Reason: libsumo has no `sumo-gui` on Windows and no multiple clients; the GUI is the fastest debugging tool for a new network.
Consequence: scripts import through one module so the switch is a one-line change.
Evidence: https://sumo.dlr.de/docs/Libsumo.html (accessed 2026-10-26).

## ADR-008 — Explicit per-seed demand files for paired experiments (2026-10-26)
Decision: generate vehicle files with fixed departure times and `speedFactor` per seed, shared by baseline and every candidate.
Alternatives: probabilistic flows with `--seed` only.
Reason: SUMO randomises speed factors, driver imperfection and probabilistic flows; an intervention that changes dynamics can shift random draws and break the pairing.
Consequence: runs also use `--tripinfo-output.write-unfinished` and a drain period, and treat teleports as a validity failure.
Evidence: https://sumo.dlr.de/docs/Simulation/Randomness.html and https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html (accessed 2026-10-26).

## ADR-009 — Cache raw model streams and replay from cache (2026-10-26)
Decision: the SAM and Muse adapters write every raw response or event stream to `cache/` with request hash, model, parameters and timestamp. Parsers read from the cache.
Reason: reproducibility, offline judge replay, and no repeat spend while developing downstream stages.
Consequence: cached model content must be labelled as cached and model-generated in the UI and report. `cache/` is ignored by Git until a small sample is explicitly approved.

## ADR-010 — Packaging and tooling (2026-10-26) — SUPERSEDED by ADR-011
Decision: one root `pyproject.toml` with an editable install of `packages/*`, a `.venv` created with the standard library, and `pip`. `uv` is deferred.
Reason: avoids a workspace layer and an extra tool download; `uv` can replace pip later without code changes.
Consequence: `uv` and `ffmpeg` are not installed; ffmpeg is needed before clip trimming or re-encoding.
Outcome: the "replace pip later without code changes" path was taken on 2026-10-27; the single root `pyproject.toml` and editable install of `packages/*` are unchanged.

## ADR-011 — uv on WSL2 Ubuntu 24.04 is the development target (2026-10-27)
Decision: develop in WSL2 (Ubuntu 24.04 LTS, Python 3.12) under `~/projects/cairoflow` on the Linux filesystem. `uv` (installed with `pipx`) owns the interpreter pin (`.python-version`), the `.venv` and the lock. `uv.lock` is resolved for Linux x86_64 only (`[tool.uv] environments`) and installed with `uv sync --frozen`. Dev tools live in `[dependency-groups] dev`. This supersedes ADR-010.
Alternatives: keep pip and a standard-library venv (ADR-010); develop natively on Windows; Ubuntu 26.04 LTS; Docker.
Reason: one command yields a hash-locked, reproducible environment; Ubuntu 24.04's system Python is 3.12, matching `requires-python`; SUMO, FFmpeg and the later FastAPI/Next.js stack are first-class on Linux; Windows 10 build 19045 supports WSL2 and CUDA on WSL. Ubuntu 26.04 was offered by `wsl --list --online` but no compatibility evidence favoured it. No container is needed for a single-developer modular monolith (ADR-003).
Consequence: `requirements.lock.txt` is the superseded Windows snapshot, kept for history only. The Windows checkout is the preserved original, not the working copy, and the Linux repo has no remote. `opencv-python-headless` replaces `opencv-python` (no GUI is needed) and `numpy` is declared explicitly. No PyTorch or CUDA wheels are installed because models are hosted (ADR-001); if local inference is ever needed, choose the build from PyTorch's official selector. The Windows NVIDIA driver provides the GPU to WSL; no Linux display driver or CUDA toolkit is installed.
Evidence: `scripts/verify_env.py`.

## ADR-012 — SUMO from pinned PyPI wheels, FFmpeg from apt (2026-10-27)
Decision: SUMO comes from the `eclipse-sumo`, `libsumo`, `traci` and `sumolib` wheels pinned in `uv.lock` (1.28.0, the version validated in M0). The one extra system package is `libatomic1`. FFmpeg and ffprobe (6.1.1) come from Ubuntu's `ffmpeg` package.
Alternatives: apt or PPA `sumo`; building SUMO from source.
Reason: `cairoflow_simulation.network` finds binaries through `sumo.SUMO_HOME`, so the wheel keeps code and lock in step and reproduces the Windows-validated SUMO version without trusting a PPA.
Consequence: the wheels install without `libatomic1`, but every SUMO binary and the `libsumo` extension then fail to load, so a successful package install does not prove SUMO works. `scripts/verify_env.py` runs real TraCI and libsumo simulations. `SUMO_HOME` need not be set. Ubuntu's FFmpeg reaches NVENC and NVDEC through the driver libraries WSL mounts at `/usr/lib/wsl/lib` (verified 2026-10-27); where that is unavailable the CPU path (libx264, software decode) applies.
Evidence: observed on 2026-10-27.

## ADR-013 — WSL memory cap lowered from 8 GB to 6 GB (2026-10-27)
Decision: `%UserProfile%\.wslconfig` uses `memory=6GB` (was 8GB), with `processors=6` and `swap=4GB` unchanged.
Alternatives: keep 8 GB and close applications; stop the other virtual machine; enlarge the page file; raise the cap again later.
Reason: after setup, every WSL start failed with `HCS/ERROR_NO_SYSTEM_RESOURCES`. Free commit was 7.5 GB against an 8 GB cap, and an unrelated VM (created 2026-10-26 13:05, 4.1 GB private commit) survived `wsl --shutdown`. With 6 GB the VM started immediately. The cause is inferred from that behaviour; the Hyper-V compute event log needs admin and was not read. The other VM is not part of this project and was not touched. Stopping it or changing the page file would need your decision or admin rights.
Consequence: Linux sees about 5.9 GB; idle use was about 0.6 GB during setup. Raise the cap again if commit headroom returns, then `wsl --shutdown` and re-test.
Evidence: observed on 2026-10-27.

## ADR-014 — Every geometry value carries a provenance label (2026-10-27)
Decision: each intersection-geometry and signal-timing value is stored as a `GeometryValue` with a unit and one label: `measured` (authoritative drawings, surveys, published dataset geometry), `approximated` (derived by our procedure from the clip, with a stated error), or `synthetic` (chosen by us with no observation). The label is shown in the network manifest, the report and the UI.
Alternatives: a free-text note per network; a single label for the whole network.
Reason: The project's evidence rules forbid presenting assumptions as measurements, and a real network will mix all three kinds (lane counts read from video, arm lengths beyond the camera chosen by us).
Consequence: the existing `baseline_4way` network is `synthetic` throughout and remains a test fixture. The geometry compiler must refuse a value without a label.
Evidence: ## ADR-015 — MVP intervention allowlist is `signal_timing` only (ACCEPTED, 2026-10-27; confirmed by the user)
Decision: the validator accepts only `signal_timing` interventions in the MVP; at least two distinct candidates are simulated. A `signal_timing` proposal changes green durations of the compiled cycle only; yellow and all-red clearance intervals and the phase order stay as compiled (`cairoflow_simulation.validation`). `turn_restriction` is a stretch goal; `route_guidance` and `demand_stress_test` are rejected with "not in MVP allowlist".
Alternatives: implement two intervention types (adds demand re-routing and a second validator path); keep all four types from ARCHITECTURE.md.
Reason: `SignalPlan` bounds and `find_conflicting_greens` already exist and are tested, so this is the shortest path to real, validated, paired simulations within 8 days. The brief asks for at least two valid interventions, not two types; the Day 6 plan asked for two types.
Consequence: the demo shows timing alternatives, not structural changes. If time allows, `turn_restriction` is added as a second type.
Evidence: packages/simulation/README.md.

## ADR-016 — SAM adapter: cache-first, spend-limited, masks by reference (2026-10-27)
Decision: `SamClient` keys each request by SHA-256 of (model, prompt, clip SHA-256, metadata). A cached completed stream is always returned instead of sending, in live and replay modes alike; replay never opens a connection. Live sends exactly one request with no retries, and only if the estimated cost (published per-frame price, 50-frame minimum) fits a spend limit that defaults to $0. Failed attempts are written to `cache/sam/failed/` for audit and are never replayed. Observations reference masks as `cache://sam/<request_key>/<record order>` instead of copying payloads. Video goes as a base64 data URL, as verified by the `sam-video` smoke test.
Alternatives: retries with backoff; the Files API (`file_id`); decoding and storing every mask as an image.
Reason: no accidental or repeated spend (the user approves paid calls per action); offline replay for judges (ADR-009); masks are large and rarely needed, and the cached stream is already the source of truth.
Consequence: a caller must pass an explicit spend limit for a real run. A connection error or timeout mid-stream raises without an audit record and may still have been billed. Clips too large for a data URL (limit unknown) will need the Files API.
Evidence: packages/perception/cairoflow_perception/sam_adapter.py; tests/test_sam.py.1.

## ADR-017 — Camera 2 ground frame from NGSIM trajectories; no calibration from time-independent evidence (2026-10-27)

**Decision:** the ground frame for camera 2 (NGSIM intersection 2) is local metres from State Plane point (6452083, 1873176) US survey feet, in NAD83 California zone 5 (EPSG:2229). x runs along grid east and y along grid north; grid north is 0.21° off true north, and the difference is stated, not corrected. Lane positions come from the published trajectories, labelled `approximated` under ADR-014. No calibration spec is written from time-independent evidence. A calibration needs along-road check points from a time sync or a survey.

**Alternatives:**
- keep the synthetic vertical-view frame of v1;
- build points from crossings of Lankershim and cross-street lines, as tried first: the cross-street "lines" were a zebra stripe and an arrow shaft, and an over-extrapolated dotted guide;
- use the ECC registration matrix as the calibration.

**Reason:** the trajectories are the only measured ground positions available locally (the ortho photo is truncated). Across Lankershim they agree with the paint to lane level (0.09 m and 0.17 m rms). Along the road, however, they cannot fix anything:
- the cross streets are tracked only from their limit lines inward;
- published through-lane stop positions sit 4.2–5.7 m (median) behind the painted limit lines, whereas the video shows lead vehicles about 1 m from them (DATA_REGISTER.md §14.6).

**Consequence:**
- The world model still refuses camera 2 until §13 step 5 (time sync) or surveyed points provide check points.
- Along-road quantities from the trajectories (queue lengths, stop-line positions) carried the §14.6 discrepancy. The time sync of 2026-10-28 (DATA_REGISTER.md §15) traced it to the registration's along-road scale (×1.156); NGSIM's stopped positions match the video to about 0.5 m. Do not use the ECC registration for along-road distances.

**Evidence:** scripts/analyse_ngsim_cam2.py; data/geometry/ngsim-lankershim-cam2/v2/control_points.json; tests/test_analyse_ngsim_cam2.py.

## ADR-018 — Camera 2 calibration check target is 1.25 m (2026-10-28; decided by the user)

**Decision:** camera 2's calibration is checked against 1.25 m instead of the default 1.0 m (`fit_homography(..., target_max_check_error_m=1.25)` in `scripts/checkpoints_ngsim_cam2.py`). Acceptance still needs both of these within the target:
- every held-out vehicle check point;
- every painted lane line.

**Reason:** the check points' ground positions come from NGSIM, whose stated accuracy is about 2 ft across the road and 4 ft (1.22 m) along it (metadata PDF). A check target tighter than the reference's own error cannot separate calibration error from reference error.

**Alternatives:**
- keep 1.0 m;
- drop or re-read the one failing frame after seeing the result. Rejected: that is post-hoc data selection.

**Consequence:** on 2026-10-28 the refit is still **not accepted**. One held-out point, east frame 8557's left lane, is off by 1.38 m. The other 22 are within 1.25 m (median 0.42 m), and the lane lines are within 0.69 m. The world model still refuses camera 2 (DATA_REGISTER.md §15).

**Amendment (2026-10-28, decided by the user):** the held-out check must pass on **both** complementary splits: alternate frames, and the same split swapped. An acceptance must not hinge on which frames happen to be held out.

With 50 pairs, the alternate split passes (worst 1.16 m) and the swapped split fails (worst 1.47 m, east frame 8557). The camera 2 calibration is therefore left **not accepted**, and work moves on. Camera 2 stays out of the world model until new evidence passes both splits.

**Evidence:** data/geometry/ngsim-lankershim-cam2/v2/sync_checkpoints.json; tests/test_sync_ngsim_cam2.py.

## ADR-019 — The real-intersection baseline comes from the published NGSIM trajectories, not camera 2 (2026-10-28)

**Decision:** M4 on the real intersection (NGSIM intersection 2) is built only from the published trajectories (`8ect-6jqj`, CC BY-SA 3.0) by fixed rules in `cairoflow_perception.ngsim_junction`:
- the ground frame (ADR-017, rotated to Lankershim);
- lanes, gates and allowed turns;
- turning-movement counts and queues;
- a signal timeline estimated from stop-line discharge times.

No camera input is used: no pixels, no calibration and no image registration. The world model's frame is anchored on the dataset's SHA-256 (`ground_source_sha256`), not on a calibration.

**Alternatives:**
- wait for an accepted camera 2 calibration (ADR-018; blocked);
- keep the synthetic network (it says nothing about a real junction);
- read the signal timing from the published timing sheets (missing from the truncated site ZIP, DATA_REGISTER.md §7);
- annotate signal heads in the video (lamps are not readable from the near-vertical view, §9).

**Reason:**
- M4's gate needs a real network and observed demand, and the trajectories give both in ground metres, independently of any camera.
- The ground positions themselves are published measurements. Our rules only aggregate them, so the values are labelled `approximated` (ADR-014).
- Nothing about camera 2 changes: it stays uncalibrated and outside this world model.

**Consequence:**
- Contracts gain a second frame anchor: `IntersectionGeometry` and `TrafficWorldModel` take exactly one of `calibration_spec_sha256` or `ground_source_sha256`.
- Signal timelines gain an optional protected-left head per arm and the label `trajectory-derived` (mapped to `approximated`). Conflict rules: crossing arms, and a left head against the opposite arm's `all` head.
- The compiler gains three things:
  - a chosen cycle start;
  - protected-left link states;
  - an opt-in right turn on red (SUMO state `s`, junction type `traffic_light_right_on_red`; labelled `synthetic`).
- The compiler also gets a fix: parallel left-turn lanes were connected crosswise, which made them foes. This only showed with two left lanes.
- Demand lists counted U-turns instead of failing.
- Independent reviews of the first version led to these further changes, all on the common path:
  - A phase with a yellow on any head is a clearance interval, fixed for the validator and shown to Muse as such. Before, a "green" phase could stretch another head's yellow.
  - A turn facing a yellow foe gets permissive green.
  - The cut cycle must close legally when repeated, and the embedded program is checked state by state.
  - A run refuses phase durations that are not multiples of the step, so SUMO runs the recorded timing.
  - Runs also check collisions inside the junction.
  - `QueueSample.censored` marks a queue that reaches the end of the observed lane (a lower bound).
- The signal program is one observed cycle, not a published plan: the qualifying cut whose head greens and length are nearest their medians. Yellow (4 s), start-up lost time (2 s), clearance (1 s) and minimum green (5 s) are synthetic, and the timeline's `source` says so. The `trajectory-derived` label maps to `approximated` even though those constants are inside it; the record lists them.
- Any later candidate is compared with this simulated baseline, not with the real junction. It is a decision-support baseline, not a measured one.
- The demo's video (camera 2) and its simulation (from the trajectories) are separate sources of the same junction and must be presented as such.

**Evidence:** DATA_REGISTER.md §16; data/geometry/ngsim-lankershim-int2/; scripts/build_ngsim_int2_scenario.py; tests/test_ngsim_junction.py.

## ADR-020 — A fresh clone can replay the intersection 2 experiment: pinned build time, one committed Muse reply (2026-10-28; approved by the user)

**Decision:**
- **Build time:** `scripts/build_ngsim_int2_scenario.py --as-of <time>` sets the time written into every output and used in the two directory names. `--check-record` compares the outputs with the committed record's hashes and directory names.
- **One committed reply:** the Muse Spark reply behind the intersection 2 experiment is committed at `cache/muse/ddfb8029….json`, with `cache/muse/NOTICE.md`. This is the first approved exception to ADR-009. It is the raw record except for one change: the four `x-ratelimit-*` header values are replaced with `[removed]` (option B).
- **Cache key check:** replay accepts a cached file only when its stored request body hashes to its key.

**Alternatives:**
- **Let the experiment script read rebuilt directories under new names:** insufficient. The world model carries the build time, and the bundle carries the world model's hash, so a rebuild at another time asks a new request with a new key, and the cache misses.
- **Drop the time from the hashed bundle:** that would change the recorded request key and orphan the recorded reply.
- **Commit the raw reply as received (option A):** it exposes the account's rate limits, which replay does not need.
- **Publish the reply outside Git (option C):** the same exposure, with more steps for a reviewer.

**Reason:**
- The demo has to be reproducible by a reviewer, not only on the machine that recorded it.
- **Tested before committing.** I built at the recorded time (2026-10-28T07:43:04Z) in a fresh clone in another folder, with the same locked packages:
  - geometry, signal timeline, world model and baseline manifest were byte-identical to the record;
  - the bundle was identical (`94d8ba7f75925ed4`), as was the request key (`ddfb8029…`);
  - with the reply in place and no API key, the replay gave 0 trace problems and all 15 tripinfo hashes identical.
- Meta's API terms (§4.1) assign inputs and outputs to the user.

**Consequence:**
- **Public model text.** The reply's text becomes public with the repository: 2 hypotheses and 3 proposals with rationales and trade-offs, including the "northbound" naming slip. The report and the app already label it as model-generated.
- **Licence.** The request body holds evidence derived from the NGSIM trajectories, so the file carries their CC BY-SA 3.0 attribution and licence (`cache/muse/NOTICE.md`).
- **The rest of `cache/` stays ignored.** That includes `cache/muse-raw/` (the local raw record) and `cache/muse/failed/`. Any further committed reply needs its own approval and notice.
- **Replay strictness.** It now refuses a cached file whose stored request was edited, even if the file name and stored key are unchanged.
- **Not verified:** a rebuild on a different machine. Determinism rests on the locked numpy and SUMO versions.

**Evidence:** cache/muse/NOTICE.md; tests/test_int2_replay_inputs.py; tests/test_reasoning.py (edited request refused); README, "From a fresh clone"; DATA_REGISTER.md §16.

## ADR-021 — Experiment eligibility, Holm-confirmed improvements and a baseline-assessment gate (rules 0.2.0, 2026-10-28)

**Decision.** The principle stays: a candidate is eligible only if no metric worsens and at least one improves, with no weighted score. What changes is how runs that did not finish are treated, what "improves" needs, and when a selection may be called a recommendation.

1. **One termination policy for every arm (unchanged settings).** Demand over [0, warm-up + horizon), metrics over [warm-up, warm-up + horizon), then a drain with no new demand, then a fixed end. For intersection 2 that is [0, 1200), [300, 1200), 600 s and 1800 s, as recorded. Each run is classed (`cairoflow_evaluation.eligibility`):
   - **integrity failure:** a teleport or a collision. The run is not a valid simulation of the plan. A collision is a model artefact, but a teleport after a long standstill is often a jam the plan caused, so such runs need inspecting rather than being read as harmless;
   - **incomplete:** nothing broke, but vehicles were still in the network or waiting to enter at the end, so the demand did not clear within the drain;
   - **complete:** everything else.
2. **Candidate status.** A candidate is `invalid_simulation` if any seed failed integrity, `incomplete` if any seed is incomplete, and `compared` only if every seed is complete. Only `compared` candidates can be selected.
   - An incomplete candidate is still compared, on every seed, on throughput and the window queues, so its harm stays visible. A later end does not change those metrics (scripts/drain_sensitivity_ngsim_int2.py).
   - Its mean delay is not compared: an unfinished trip's timeLoss is only a lower bound.
   - Its unfinished vehicles are listed per movement.
   - A queue that comes within one car (7.5 m) of its lane's upstream end, in any arm (the baseline included), is listed as censored. queue.xml cannot show it growing further, and vehicles still waiting to enter are not in it, so that arm's queue values are lower bounds.
   - Every run's tripinfo.xml is checked against its RunRecord.
3. **Per-metric verdicts are unchanged.** A metric worsens or improves when the paired 95% t interval excludes zero, with no correction. A worsening therefore blocks readily, which errs toward safety.
4. **An improvement must be confirmed.**
   - It counts toward selection only if its paired t test survives Holm's step-down procedure at a family-wise 5%, and points in the better direction (`cairoflow_evaluation.significance`).
   - **The family is fixed by the design before any result:** every simulated candidate with every improvement metric. A planned test that cannot be run still counts and is never rejected, which makes the other tests stricter. That covers:
     - a broken candidate;
     - an incomplete candidate's mean delay;
     - fewer than 2 seeds;
     - a zero standard deviation;
     - a censored queue.
   - Decisions use the unrounded p-value.
   - **Only mean delay and the mean queues are improvement metrics.**
     - Window throughput counts arrivals of a fixed demand, so a plan mostly moves arrivals across the window's edges. A drop can show vehicles held back; a rise does not show more vehicles served.
     - The maximum queue moves in whole vehicles.
     - Both may still worsen.
5. **Recommendation gate.** A selected candidate is *recommended* only when the baseline it was compared with is assessed as `validated`.
   - The assessment is a committed record, `data/geometry/ngsim-lankershim-int2/baseline_assessment.json`, tied to the baseline manifest's SHA-256 and to each evidence file's SHA-256.
   - The experiment and re-evaluation scripts refuse an assessment of another baseline or with changed evidence. The report and the app show the status.
   - A9 checks three things:
     - the recommendation follows the rule;
     - the assessment names the experiment's baseline, and its evidence still hashes as recorded;
     - an evaluation from before these rules carries no recommendation.
   - The first assessment (2026-10-28) is **not validated** (DATA_REGISTER.md §17.5).

**Alternatives.**
- **Keep the uncorrected rule.** By Monte Carlo at n = 5, using the recorded difference SDs and correlations, a candidate with no effect at all becomes eligible with probability 0.11–0.18, and at least one of three such candidates with 0.24–0.44 (Investigation D).
- **Holm within each candidate only:** 0.05–0.07 across three null candidates.
- **Holm over the selectable candidates only** (the first version of this rule). Rejected after the independent review: which candidates enter the family would then depend on the outcomes.
- **Extend every arm's drain to a 3000 s cap and compare delay on finished trips** (Investigation A's T1). Deferred: it changes the recorded RunSettings and baseline. The diagnostic shows window metrics unchanged, and only the delay of incomplete runs moves (+0.1 to +3.2 s).
- **Compare an incomplete candidate on its complete seeds only.** Rejected: it keeps the seeds that favour it.
- **Equivalence margins** (for example 0.5 m, 0.5 s) to discard negligible differences. Deferred: the values would be our judgement, not agreed.
- **A weighted score.** Rejected: no weights have been agreed.

**Reason.**
- The I1 and I3 vehicles left at 1800 s are west-to-north left turners queued on the protected-only left lane. Their shorter west green leaves that movement oversaturated: degree of saturation about 1.2 and 1.5, against 0.56 in the baseline. That is an outcome of the intervention, not a simulator fault, and "not compared" hid it.
- Without multiplicity control, a null candidate would often be selected.
- The baseline itself is not validated (DATA_REGISTER.md §17), so no selection on it may be presented as a recommendation.

**Consequences.**
- **The recorded decision is unchanged:** none selected, nothing recommended (`scripts/reevaluate_ngsim_int2_experiment.py`; record `experiments/records/ngsim-int2-reevaluation-20261028T122501Z.json`). The reason now names every candidate:
  - I1 and I3 are incomplete and worsen the west queues: max +51 and +82 m, mean +33 and +53 m. I3's are lower bounds, because the west left lane filled on seed 4.
  - I2 worsens the south mean queue.
  - Nothing survives Holm over the 15 planned tests. The smallest p is I2's south queue worsening, 0.0062 against 0.0033.
- **Low power.** At n = 5, Holm leaves little power: the minimum detectable effect at 80% power is 1.68 SD at α = 0.05 and 3.16 SD at α = 0.005. Experiments meant to find improvements need a pre-registered seed count of about 20 or more.
- **Zero variance.** A constant difference with zero standard deviation still yields `worsens` (for example +0.01 m on a maximum queue). That errs toward blocking.
- **Older records still load.** `rules_version` is None in evaluations before 0.2.0, and their selections are recomputed only from what they hold.
- **Not changed:** RunSettings, the recorded baseline, the Muse request (bundle and key) and every tripinfo hash.
- **Known gaps:**
  - The queue.xml files of I1 and I3 have no recorded hash anywhere, because the 0.1.0 evaluation never read them. They are read as found.
  - The delay cohort uses actual insertion time, a latent bias for congested entries. It has no effect on the recorded runs (DATA_REGISTER.md §17.1).
  - The run list in the app shows the selection but not the recommendation.

**Evidence:**
- packages/evaluation/cairoflow_evaluation/{eligibility,significance,compare,report}.py
- tests/test_eligibility.py, test_significance.py, test_experiment.py, test_pipeline.py, test_reevaluate.py
- scripts/drain_sensitivity_ngsim_int2.py and scripts/reevaluate_ngsim_int2_experiment.py
- DATA_REGISTER.md §17
- An independent review (2026-10-28) whose seven should-fix findings were all applied before the records were written.

## ADR-022 — A replay baseline: observed signals and observed vehicles (2026-10-28)

**Decision.** Add a second baseline, alongside the repeated-cycle one (ADR-019, which stays the recorded baseline):
- **Signals.** SUMO replays the trajectory-derived timeline over the span where every head is known: clip 118–751 s, 633 s or about 6.4 cycles. Nothing is filled in. After that come 4 s of yellow, 1 s of all-red (both synthetic) and the cut cycle repeated for the drain only. Code: `compile_network(..., replay=ReplayOptions)`.
- **Demand.** Every counted vehicle enters from an observed state (`cairoflow_simulation.trace_demand`):
  - vehicles present at 118 s enter in place, each lane re-packed at SUMO's spacing;
  - Lankershim vehicles enter where first seen, about 105 m out, at SUMO's safe speed;
  - cross-street vehicles first seen within 40 m of the stop line enter at the arm start, timed to reach that spot at the speed limit.

  Lanes are the observed stop-line lanes.
- **Measurement and seeds.** The window is the replay span itself (warm-up 0), with a 600 s drain. Seeds 1–20 were pre-registered in the build script, from the §17.3 power table.
- **Candidates.** A validated cut-cycle change is applied to every replayed phase with the same head states, clamped at the 5 s minimum green and counted (`replay_candidates`). No candidate is new.

**Alternatives.**
- **Fill the unknown edges of the timeline** to replay all 900 s. Rejected: the states would be invented.
- **Keep Poisson demand on a replayed timeline.** Rejected: it loses the platoons that were the main mismatch.
- **Cross-street vehicles entering where first seen.** Tried. Their insertion is blocked near the stop line for up to 202 s. The arm-start rule keeps every vehicle in the network, insertion delay at most 8.8 s, but lags west vehicles by about one cycle. Neither rule reproduces cross streets observed only 10–25 m out.
- **Bootstrap or perturb the observed arrivals** to represent demand uncertainty. Deferred.

**Reason.** The §17.4 validation traced the north mismatch to missing platoon arrivals. The paired comparison on the replay baseline (DATA_REGISTER.md §18) confirms the fix: north through crossings lag their observed times by only +0.2 s (median), and north stops are 30.2% against 24.1% observed (previously 64.8% against 31.9%).

**Consequences.**
- **One realisation only.** Every seed replays the same vehicles, so seed spread is SUMO's microscopic noise. With 20 seeds the intervals are very narrow, and verdicts are conditional on that one 10.5-minute realisation; they say nothing about other days or demand levels. The report says so.
- **Cross streets.** West vehicles cross one to two cycles late, so west and east results are not trusted, and neither is any verdict that rests on them alone.
- **Not yet assessed.** The replay baseline has no committed baseline assessment, so nothing on it can be recommended (ADR-021).
- **Unchanged:** the recorded baseline, network hash, experiment, Muse request and records. The default compile path is byte-identical.

**Evidence:**
- packages/simulation/cairoflow_simulation/{geometry_compiler,trace_demand,replay_candidates,candidates,runner}.py
- scripts/build_ngsim_int2_realism.py, scripts/run_ngsim_int2_realism_experiment.py, scripts/validate_ngsim_int2_baseline.py (replay branch)
- tests/test_replay.py, tests/test_baseline_validation.py
- DATA_REGISTER.md §18

**Amendment (2026-10-28): cross streets anchored to their observed discharge.** A cross-street entry whose observed stop-line crossing falls in a green joins the queue of the red before that green, in observed order, 2 s apart (assumed gap). The paired lag of west through vehicles falls from about +86 s to under 1 s. West lefts still lag about 12 s, and the 3 west right turns, which mostly go on red, are unchanged. South start-up is not calibrated: the observed start-up headways err in opposite directions on N and S, so no global parameter fits both (DATA_REGISTER.md §19).

**Amendment (2026-10-28): window-start queues, and the first replay assessments.**
- **Queued at the start.** A cross-street vehicle whose anchored arrival falls at or before the window start now enters queued at the stop line at 0 s (`queued_at_start`). The west through, left and right paired lags are now within 3 s in median, with 74%, 53% and 67% of vehicles within 5 s.
- **Still late.** The 8 west lefts that crossed in signal windows the estimated timeline drops or ends early still lag. Fixing them needs a new timeline version.
- **Assessed.** Each replay baseline now has its own assessment (`replay_assessments/<baseline>.json`). Both recorded ones are partially validated, so nothing simulated on them is recommended. This replaces the "Not yet assessed" consequence above.
- **Correction to the first amendment.**
  - The west through lag was under 1 s in median, but only 41% of those vehicles were within 5 s.
  - Only 1 of the 3 west right turns went on red.
  - The west-left lag came mostly from the timeline and the window start, not from the queue (DATA_REGISTER.md §§20–21).

## ADR-023 — Signal timeline version 2 for the replay baseline (2026-10-28)

**Decision.** Add a second version of the trajectory-derived signal timeline, and replay it in the current replay baseline. Version 1 stays the scenario record's timeline (ADR-019) and is unchanged.

Version 2 (`ngsim_junction.SignalRevision`) changes two rules:
- **Permissive lefts count.** An arm with no left head of its own counts its permissive left turners as green evidence.
- **Yellow starts at the last crossing.** A window's yellow starts at its last crossing instead of ending there. The existing conflict rules still trim it.

Both rules and five acceptance criteria were fixed before any version 2 result was seen. The version 2 script refuses to run unless version 1's rules reproduce the committed timeline exactly.

**Alternatives.**
- **Fill every red gap up to the next conflicting onset.** Rejected: this invents green time in which nobody crossed.
- **Tune the rules until the paired lags are smallest.** Rejected: the lags are measured on the same crossings the timeline is estimated from, so this would only fit the noise.
- **Keep version 1.** Rejected: west lefts cross while it shows every head red.

**Reason** (DATA_REGISTER.md §§20, 22). Version 1 drops west greens served mostly by permissive lefts, and its greens are lower bounds. On version 2:
- every west left crosses in its own green;
- 87% of west throughs are within 5 s of their observed crossing (74% on version 1);
- north through stopping falls inside its observed interval;
- all five pre-registered criteria pass.

**Consequences.**
- **Verdicts depend on the timeline estimate.** The same candidate, I1, worsens mean delay by 5.40 s on version 1 and improves it by 0.75 s on version 2. Each interval is narrow and they exclude each other. Seed intervals do not include timeline uncertainty, so no verdict on either version can be trusted until that uncertainty is propagated, for example through an ensemble of defensible timelines. The assessment records this.
- **Nothing recommended.** The replay baseline on version 2 is partially validated, so nothing on it is recommended. No candidate is selected.
- **In-sample.** The agreement is internal consistency, not an independent check of the timing; no head or controller log was read.
- **Unchanged:** the scenario record, its version 1 timeline and world model, the recorded baselines, and every earlier record.

**Evidence:**
- packages/perception/cairoflow_perception/ngsim_junction.py (`SignalRevision`), scripts/build_ngsim_int2_timeline_v2.py, and the `--timeline` options of scripts/build_ngsim_int2_realism.py and scripts/validate_ngsim_int2_baseline.py
- data/geometry/ngsim-lankershim-int2/signal_timeline_v2.json, signal_estimate_v2.json and replay_assessments/ngsim-int2-realism-baseline-20261028T141919Z.json
- experiments/records/ngsim-int2-realism-{baseline,validity}-20261028T141919Z and ngsim-int2-realism-experiment-20261028T142330Z.json
- tests/test_ngsim_junction.py, tests/test_replay_records.py

## ADR-024 — Queue metrics that cannot inform a decision: uninformative maxima and entry censoring (rules 0.3.0, 2026-10-29)

**Problem** (DATA_REGISTER.md §24). On the intersection 2 replay, the north and west maximum queues were 105.46 m and 54.33 m in every run: baseline, every candidate, every seed. They are not a lane-length cap; the lanes are 186.02 m and 177.94 m long. The replay's warm-up is 0 s (ADR-022), so each maximum is the constructed start queue at t = 1 s, before any plan acts. The paired differences were zero by construction, and the verdicts read "inconclusive", as if the plans had been compared there.

A second, real cap: replay vehicles enter mid-lane, where they were first observed. When the queue reaches that point, new vehicles wait off the network, where queue.xml cannot see them. The lane-full rule (rules 0.2.0) could not catch this, because the queue stops 80–100 m short of the lane's end. Candidates I1–I3 did this on the north approach; the baseline did not. Their north mean-queue "improvements" had been Holm-confirmed while they were holding vehicles out of view.

**Decision** (rules 0.3.0, `cairoflow_evaluation.compare`, `.eligibility`, `.report`):
- **Uninformative maxima.** For each candidate, the first second at which its signal program differs from the baseline's is read from the two compiled programs. On a seed where both runs reach the arm's maximum queue before that second, and their queue.xml rows agree up to it, the difference measures a shared state, not the plan. If that holds on every seed, or on some seeds while the rest show no worsening, the comparison's verdict is `uninformative`. It counts neither for nor against the candidate. Raw values are kept, nothing is extrapolated, and a worsening shown by the other seeds still stands.
- **Entry censoring.** A vehicle whose insertion was delayed by at least 2 s while its lane's queue stood within 7.5 m of where it would enter is held off the network. Its wait in the window is recorded per seed and arm (`entry_blocked`) for the baseline and every candidate. That arm's queue claims are then untested in Holm's family, like a filled lane.
- **Asymmetric censoring blocks selection.** A candidate that holds more vehicle-seconds off an arm than the baseline cannot be selected, because harm there cannot be excluded. This blocks the candidate; it does not invent a worsening.
- **Tie-break by number.** Ties now go to the lower intervention number (I2 before I10), as the rule always said; the code had compared ids as text.

**Not changed:** the uncorrected harm verdicts, the Holm family and its alpha, completeness and integrity, the recommendation gate, and every committed record. Old records load unchanged; their stored verdicts are not rewritten.

**Effect on the committed replay experiments** (re-evaluated as a diagnostic, nothing written):
- North and west maximum queues are uninformative on all 20 seeds for every candidate, on both timelines.
- On version 1, I1's and I3's west maximum-queue worsening stands.
- Every candidate is entry-blocked on the north approach, and I2 also on the south; the baseline never is.
- The north mean-queue confirmations are withdrawn.
- Nothing is selected, as before.

**Alternatives rejected.**
- **Drop the first seconds of every run.** Any cut-off would be arbitrary; the signal-program difference marks exactly where runs can differ.
- **Extrapolate the hidden queue.** That would invent data.
- **Delete the metric.** That would hide the problem.

**Evidence:** packages/evaluation/cairoflow_evaluation/{compare,eligibility,report}.py, packages/contracts/cairoflow_contracts/experiment.py, tests/test_queue_disposition.py.

## ADR-025 — A pre-registered ensemble of signal timelines for intersection 2 (2026-10-29)

**Problem.** Intersection 2 has no signal log; its timeline is estimated from stop-line crossings. Versions 1 and 2 differ in two rules the evidence cannot settle, and I1's mean-delay verdict flips between them: +5.40 s on version 1, −0.75 s on version 2 (ADR-023). Seed intervals do not carry that uncertainty.

**Decision.** Report candidate verdicts across four signal timelines, all required. They are the corners of the 2×2 spanned by the two unsettled rules (`SignalRevision` flags on the unchanged estimator):

| Member | Timeline | Permissive lefts as green evidence | Yellow placement |
|---|---|---|---|
| T1 | version 1 (unchanged) | off | ends at the last crossing |
| T2 | version 2 (unchanged) | on | starts at the last crossing |
| T3 | v2a (new) | on | ends at the last crossing |
| T4 | v2b (new) | off | starts at the last crossing |

Members, gate and rule are fixed in `data/geometry/ngsim-lankershim-int2/timeline_ensemble.json`, committed before any T3/T4 baseline or candidate run.

**Admissibility gate.** A member must pass all of:
- **G1:** the code that builds it reproduces versions 1 and 2 byte for byte;
- **G2:** no conflicting heads, in the timeline or the compiled program;
- **G3:** the replay window [118, 751) s, replayed second by second;
- **G4:** no through or left movement shown red more often than version 1;
- **G5:** a valid baseline on seeds 1–20 (0 teleports, 0 collisions, every vehicle arrived, reruns identical).

A member that fails is reported, never replaced or tuned.

**Rule** (`cairoflow_evaluation.ensemble`). Each member is its own experiment, judged alone under rules 0.3.0 with its own Holm family.
- **Robust improvement:** on every member, the candidate is the same plan (each of its phase changes applies to at least one phase), has all planned paired seeds, and is eligible by `candidate_standings`. Requiring confirmation on every member is an intersection–union test, so no further correction is needed.
- **Otherwise** the verdict is `timeline_dependent` or `no_support`, with the per-member table.
- **Also reported:** robust harm (a metric that worsens on every member) and every metric whose verdict differs across members.
- **Nothing is pooled or averaged** across members.

**Alternatives.**
- **One version:** rejected, because the verdict flips.
- **Pooling or averaging across members:** rejected, because it would let one member's failure hide behind the others.
- **Variants of start-up, service gap, minimum window, the synthetic constants, or filling red gaps:** excluded on evidence, as listed in the design record.
- **A bootstrap of the estimate:** deferred.

**Consequences.**
- **Known in advance:** I3 cannot be robust, because its north-only change applies to no phase on T2 or T4. Version 1 and 2 results were also already known when the design was written; the record says so.
- **Corners, not probabilities:** they bracket two rule choices. Other unpropagated uncertainty remains: constants, east lower-bound greens, window edges, one demand realisation, south over-stopping.
- **Nothing is recommended:** no member's baseline is assessed as validated.

**Evidence:** scripts/build_ngsim_int2_timeline_ensemble.py, scripts/run_ngsim_int2_timeline_ensemble.py, packages/evaluation/cairoflow_evaluation/{ensemble,timeline_gate}.py, the `--timeline` options of scripts/build_ngsim_int2_realism.py and scripts/validate_ngsim_int2_baseline.py, tests/test_timeline_ensemble.py.

### ADR-024 amendment — rules 0.3.1, after the independent review (2026-10-29)

An independent reviewer compared `entry_blocked` with SUMO's own insertion delays and found two problems with rules 0.3.0.

**Problem 1: it missed about half of the off-network waiting.** On T2 (version 2), the baseline's vehicles waited 82 vehicle-seconds to enter on the north approach over 20 seeds, and 0.3.0 flagged none. Of I2's 148 vehicles that waited at least 5 s, 75 went unflagged. The cause: queue.xml's `queueing_length` counts standing vehicles only, so a wait for a gap in moving traffic left no trace there. The statement that "the baseline never holds any" was therefore unsupported.

**Problem 2: the block was over-broad.** It blocked a candidate for any excess at all. A synthetic control was disqualified by 10.8 vehicle-seconds on one seed of 20.

**Decision** (rules 0.3.1):
- **Entry waits from insertion delays.** The wait is now every approach-lane vehicle's insertion delay of at least 2 s, inside the window (tripinfo `departDelay`), whatever stopped it.
- **Blocking is a harm test.** A candidate holds vehicles off an arm only when the paired 95% interval of its per-seed excess over the baseline lies above zero. That is the same uncorrected test that flags any other harm. Equal positive excesses on every seed also count.
- **Unchanged:** the censoring of that arm's queue claims, and the uninformative-maximum rule.

**Disclosure.** The ensemble design (ADR-025) pre-registered rules 0.3.0. Its members, gate and cross-timeline rule are unchanged. The ensemble runner records the pre-registered and the used rules versions, with this amendment's reason. The 0.3.0 ensemble record stays committed, superseded but not deleted.

**Effect on the replay.** The baseline now waits 2–10 vehicle-seconds per seed on the north approach on T2, so north queue claims are untested for every candidate. I2 and I3 hold significantly longer than the baseline on the north approach (about 1,090 and 1,330 vehicle-seconds on T2); I1 does not. No candidate becomes eligible.

**Also from the review:** `tests/test_experiment.py` now guards the 0.3.x wiring in `compute_evaluation`.

**Known and left (minor):**
- **Partial shared seeds can dilute a harm.** With some shared-state seeds, a comparison whose remaining seeds would show harm can still read `uninformative`. No real case exists.
- **The delay cohort is not paired.** `mean_delay_s` uses actual departure in the window, so held vehicles can drop out of a candidate's cohort; the effect was 0.12 s or less where checked.

## ADR-026 — A synthetic known-answer control for the evaluation and selection stage (2026-10-29)

**Decision.** Add a synthetic known-answer test (`cairoflow_evaluation.known_answer`, DATA_REGISTER.md §26) as a positive and negative control for the evaluation and selection stage. It runs a junction drawn in code, apart from the NGSIM evidence, with a deliberately bad plan.
- **Pre-registered before any simulation:** the expected correction (Webster) and analytical predictions.
- **Candidates:** the correction, a half-way plan, an exact null, a mirror, and 8 seeded random ±5 s perturbations, with ids shuffled. All go through the normal validator, simulation, evaluation and selection.
- **Provenance:** each record names the git commit and the cleanliness of every imported file.

**Not a test of discovery.** No proposer runs and Muse is not exercised. Not evidence about any real junction.

**The selection rule is not changed.** In the reallocation scenario, the known correction is measured as the best candidate but not selected, because the road that loses green queues longer.
- That is the rule written in ADR-021: no metric may worsen, and no weighted score or harm margin has been agreed. Changing it to let the known answer through would weaken a safeguard to suit a result, so it was not done.
- **Open for the project owner:** whether to agree explicit harm margins or objective weights.

**Consequences.**
- The long-cycle scenario, designed after the first result and disclosed as such, shows that the stage selects a known improvement that harms nothing.
- The test's power is limited and stated in DATA_REGISTER.md §26.

**Evidence:** experiments/records/synthetic-known-answer-{reallocation-20261029T123315Z,long-cycle-20261029T123354Z}.json, tests/test_synthetic_known_answer.py.

## ADR-027 — Integrity: a report is verified by recomputation, never by its own values (2026-10-30)

**Decision:** `trace_report` recomputes the evaluation from every SUMO output, checks each number's label, unit and kind against its source, re-derives verdicts and selection, and assembles the report again from its recorded inputs; any difference is a problem. Each orchestrated run writes an experiment identity (`integrity.json`: settings, seeds, simulator, networks, demand, inputs, outputs, evaluation, report, all by SHA-256) and `integrity.verify_run` re-hashes, re-derives and can rerun SUMO seeds. A missing artifact makes a verdict `unverifiable`, never `verified`. **Reason:** the audit forged a metric that the old trace passed (D2). **Evidence:** `tests/test_pipeline.py`, `tests/test_integrity.py`; legacy records: the 4 under rules 0.3.1 verify, the 8 older ones pass every committed hash and recomputed metric but are unverifiable under the current rules.

## ADR-028 — Evidence grounding gate, prompt 0.3.0 and validator 0.2.0 (2026-10-30)

**Decision:** a Muse reply is checked by code (`cairoflow_reasoning.grounding`): a hypothesis without supporting evidence, citing ids the bundle lacks, or resting only on assumed evidence is unsupported; causal or overconfident claims are flagged; only proposals addressing a supported hypothesis reach the validator. Prompt 0.3.0 asks for limiting factor, claim type, restated evidence, inference and missing evidence, each prompt keeping its own schema. The validator checks green, protected-left, yellow and all-red bounds per signal head over the repeated cycle, and the compiled candidate network again before SUMO. **Reason:** the audit found citation problems recorded but never enforced, and an 80 s left phase accepted.

## ADR-029 — The camera-to-world-model link is gated, and the first gate was no-go (2026-10-30)

**Decision:** camera entities may enter the world model only through a pre-registered lane gate (`cam2-int2-lane-gate-v1`, committed before its run) that compares placed SAM tracks with the time-synced NGSIM vehicles. The run on the recorded excerpt was **no-go** (C: 7 judgeable tracks of the 8 required), so no camera entity enters it; the threshold is not revisited. A new excerpt (a paid SAM request, the project owner's decision) would be judged by the same rule. ADR-018 is unchanged: the calibration stays not accepted for along-road distances. **Evidence:** `experiments/records/ngsim-cam2-world-model-20261030T213226Z.json`.

## ADR-030 — One orchestrated command and a summary objective J (2026-10-30)

**Decision:** `scripts/run_cairoflow.py` runs the existing stages in order and records each stage's kind of data. J = w_T dT/T0 + w_Q dQ/Q0 + w_D dD/D0 is reported beside every metric with a weight sensitivity table and never selects; C is reported as not computed (no surrogate safety device). ADR-007 ("TraCI first") is superseded in practice: the runner calls the `sumo` binary, which fixed-time plans need; TraCI would be needed only for dynamic control or runtime closures.

**ADR-029 amendment (2026-10-31, the project owner approved one more SAM request):** gate v2 (`cam2-int2-lane-gate-v2`, the v1 rule verbatim, committed `2ca1cc5` before the request) on a new 30 s excerpt (source 390-420 s; 25 tracks): A 0.64, B 0.925, C 0.833 over at least 8 tracks, D lateral p90 2.206 m against 1.5 m: **no-go**. The camera stays out of the world model. Not tested, and to be pre-registered before any new data if pursued: whether the lateral tail comes from points inside the junction, where a turning vehicle's "lateral" axis is the arm's.

## ADR-031 — Camera gate v3: lateral error across the vehicle's heading, track pairing, sync 120.0 s (proposed 2026-10-31)

**Status:** proposed for the project owner's review. No SAM request is sent on its excerpt until they approve one; once it is sent, the gate file does not change.

**Context:** a read-only diagnosis of gate v2 (`experiments/diagnostics/camera-gate-v2-20261030T223228Z/report.md`, commit `33c6713`) found the D tail to be measurement, not SAM's lateral placement. Across each vehicle's own heading, p90 was 0.755 m. The tail was along-road error that v1/v2 count as lateral, entering in two ways:
- 66 of the 126 points over 1.5 m came from the `|x| < |y|` axis fallback inside the junction box;
- 54 came from greedy 3 m per-frame pairing, which took the car in the next lane.

The along-road error itself fits a time sync about 0.3 s off, in v2's and in v1's windows. Turning was not the cause, and the junction was only where the axis fallback happens.

**Decision (if approved):** gate `cam2-int2-lane-gate-v3` (`data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate_v3.json`). It is measured by `cairoflow_perception.camera_gate_v3` and run by `scripts/build_camera_world_model_v3.py`, beside the v1/v2 code, which does not change.
1. **Lateral error** is measured across the NGSIM vehicle's own heading, for every point. v1/v2's arm-axis lateral is reported only.
2. **Pairing is per track.**
   - A frame counts within 3.0 m across and 8.0 m along.
   - The cost is the median of (across/1.6)² + (along/8.0)².
   - An option needs at least 5 frames and at least 70 % of the track's frames that had any vehicle within the windows.
   - Options are taken cheapest first. A vehicle takes a second track only on frames it has not already taken.
3. **Sync:** 120.0 s. This is the `frame_id` value recorded in DATA_REGISTER §15 on 2026-10-28, before any gate. 119.7 s is reported as a sensitivity and tested by prediction 2.
4. **Thresholds:** A–D are unchanged, with D taken over all points. The junction box is also judged on its own (≤ 1.5 m) when it has at least 50 points from at least 3 tracks. No region replaces the overall D.
5. **Excerpt:** source 970–1000 s, chosen by a stated rule. The diagnosis report's 600–630 s holds calibration frame 6217, so it was dropped. One SAM request (about $0.06), whose tracks are judged alone.
6. **Two predictions** are written down in advance; if either fails, the timing explanation is refuted, whatever the outcome.

**Known before the data, by exploration on data already judged:**
- On v2's data, v3's procedure would be go: D 0.54 m.
- On v1's data it would be no-go: D 2.59 m, B 0.846. That is decided by one stationary track (100 of 893 points), which the camera places between lanes, 2.6 m from the nearest NGSIM car. v1's 3 m radius had left that track unpaired.

The procedure was not changed after these runs. The project owner may still change the design before the data, with disclosure. The main open choice: D is a percentile over points, so one stationary track (up to 300 points) can decide it.

**Unchanged:** gates v1 and v2, their code and records; ADR-018 (along-road distances not accepted). The camera stays out of the world model unless a gate is go.

**ADR-031 result (2026-10-31, the project owner approved the gate as written and one SAM request):**
- **Request:** one SAM request on source 970–1000 s, $0.06: 2,854 observations, 26 tracks. The gate record is `experiments/records/ngsim-cam2-world-model-v3-20261030T230506Z.json` and the SAM record is `ngsim-cam2-sam-20261030T230408Z`. Gate file unchanged since `c307da1` (sha256 `92ff93e5…`).
- **Gate v3: go.**
  - A 0.8077 (21 of 26 tracks);
  - B 0.9061 over 1,534 points;
  - C 0.9444 over 18 tracks;
  - D 1.004 m (51 of 2,657 points over 1.5 m; 3 frames excluded by the windows);
  - junction box 0.5427 m over 804 points from 17 tracks.
- **Both predictions hold.** At 120.0 s the moving vehicles' median along error is EB −0.45, NB −0.11 and SB +0.02 m; the same pairs at 119.7 s give −2.84 m pooled.
- **Reported, deciding nothing:**
  - v3 at 119.7 s would also be go (D 1.05 m).
  - v1/v2's own procedure on this excerpt would be no-go: D 1.96 m and B 0.842 at 120.0 s; D 2.53 m at 119.7 s. The go therefore rests on the redefined measurement, as the diagnosis predicted, not on the sync.
  - The region outside lanes beyond the stop lines has p90 1.86 m (305 points). The gate does not judge it separately, and most of it is one slow right-turning track (t12, 45 of the 51 points over 1.5 m).
  - Stopped NB vehicles' along error median is +3.69 m: along-road quantities stay out (ADR-018), and the stationary-pairing limitation applies.
  - The SAM record's informational `time_sync` field still states the old default (119.7 s); the gate record's 120.0 s governs.
- **Consequence:** per the gate, the camera layer exists (`camera_layer.json` in the gate record). It is not yet wired into `scripts/run_cairoflow.py`, which reads only v1/v2-shaped records; doing so changes the evidence bundle and needs a new Muse request.
- **Wiring (2026-10-31, the project owner asked for it):** `scripts/run_cairoflow.py` now defaults to gate v3's record and its SAM record (`--camera-record`, `--sam-record`). It refuses a go layer whose SAM request keys or clip are not that record's. It describes the perception stage from the SAM record. The layer enters the world model, and the bundle gains 11 `cam.lane.*` items and `cam.gate` (bundle `9df855820476337c`, 117 items), plus a limitation: lane-level only, lower bounds, nothing along the road.
  - Without a cached reply for that bundle, the run records its stages up to the bundle and stops before reasoning; nothing is paid.
  - Gate v1's records still replay the three recorded runs offline, unchanged (bundle `22548ce9b8413d3b`).
- **First live Muse request on the camera-fed bundle (2026-10-31, approved: one request, cap $0.06):** it returned `incomplete` at the 8,000-token output cap, $0.0465 (9,974 input tokens; 8,000 output, of which 5,780 reasoning). There was no usable reply, so nothing was grounded, validated or simulated; the failed response stays in the Git-ignored `cache/muse/failed/`. The cap counts reasoning, and the three earlier runs already used 5,781–7,476 output tokens of it. `--max-output-tokens` now sets it; it defaults to 8,000 because the cap is part of the cache key of the recorded runs.
- **Approved retry (2026-10-31, cap 16,000 tokens, spend limit $0.10):** complete, $0.0403 (9,974 input and 6,551 output tokens, 4,360 of them reasoning). Record `experiments/records/cairoflow-20261030T235016Z-r1.json`.
  - **Muse cited none of the 12 camera items.** Its two hypotheses rest on the NGSIM aggregates: north protected-left capacity (medium) and north-south through capacity (low). These are the same competing factors as the three earlier runs, and grounding supports both.
  - Its 3 proposals were validated and simulated on 20 paired seeds. None is selected: I1 worsens mean delay by 3.35 s [3.21, 3.48], and every candidate worsens throughput and some queues. The run is verified, including a SUMO rerun and `scripts/verify_run.py`.
  - The camera layer therefore reaches the world model and the evidence, but has not informed a decision.
  - The default output cap is now 16,000, matching the default camera record; the three earlier runs replay with `--max-output-tokens 8000`.

## ADR-032 — Prompt 0.3.1: evidence limitations that say what camera items cannot show (2026-10-31)

**Decision:** `scripts/run_cairoflow.py` chooses its fixed evidence limitations by prompt version (`evidence_limitations`).
- **Versions:** new runs send prompt 0.3.1. `--prompt-version 0.3.0` rebuilds the requests of the four runs recorded under 0.3.0, byte for byte. Any other version is refused.
- **What 0.3.1 keeps:** 0.3.0's instructions and schema (`cairoflow_reasoning.prompts`).
- **What 0.3.1 drops:** the claim that the evidence is "not from camera tracking; no camera calibration is used". It has been false beside `cam.*` items since `0ac54cf`.
- **What 0.3.1 states instead:**
  - camera items come from one camera's field of view and time window;
  - they may show only some of the vehicles and movements there;
  - they are not independent of the trajectory items;
  - they are not intersection-wide measurements;
  - they bear on a hypothesis only where lanes and seconds overlap;
  - their presence does not mean they support any hypothesis.
- **Cache:** the Muse cache now also refuses a reply recorded under another prompt version, because 0.3.0 and 0.3.1 share their instructions.
- **Legacy runner:** `scripts/run_ngsim_int2_experiment.py` builds only prompts 0.1.0–0.3.0, its camera-free limitations. It refuses 0.3.1 before reading anything and points to `scripts/run_cairoflow.py`, so no request can carry the 0.3.1 label without the 0.3.1 limitations.

**Alternatives:**
- **Edit the limitation in place:** it changes the recorded camera-fed request and orphans its cached reply.
- **A separate limitations-version flag:** it adds a second identifier for one request, and the recorded runs carry only a prompt version.
- **New instruction text:** it changes what the model is asked, not only what it is told.

**Reason:** a read-only investigation on 2026-10-31 (camera-to-muse-20261031; its report is not committed) found this false sentence in the camera-fed request, beside the 12 camera items. Historical replay must stay exact, and new runs must not send a false statement.

**Consequence:**
- Until a new paid request is approved, the default run has no cached reply and stops before reasoning (exit 3). The README's replay commands now pass `--prompt-version 0.3.0`.
- Prompt 0.3.1 claims nothing about the camera improving reasoning or informing a decision.
- The camera-fed bundle under 0.3.1, `a7ff92ceae6ca07b`, differs from `9df855820476337c` only in its limitations.

**Evidence:**
- `tests/test_run_cairoflow.py`: the recorded request keys `6dd46648…` and `51188ff3…` are rebuilt; each version sends its own key; unknown versions are refused; the cached camera-fed reply replays only under 0.3.0.
- `tests/test_reasoning.py`: a reply recorded under another prompt version is refused.
- `tests/test_run_ngsim_int2_experiment.py`: the legacy runner refuses 0.3.1 before building anything; 0.1.0–0.3.0 pass as before.
- The offline replay `cairoflow-20261031T032647Z-r1` matches `cairoflow-20261030T235016Z-r1` in its bundle, reply, grounding, objective, comparisons and all 800 metric values.

## ADR-033 — Decision freeze, and the M1 foundation: pair identity, WorldView, one comparator, governed contracts (2026-11-02)

**Status:** decisions 1, 2, 4, 5, 6, 7, 9 and 10 stand on existing evidence and need no project owner action beyond what each names. Decision 3 records what EQ-1 assumes and what the repository supports. Decision 8 was approved by the project owner on 2026-11-02 ("Approve D2 rule"), as worded below; its plan hash is not frozen yet. The M1 foundation (pair identity, population hash, WorldView, contract snapshots) is commit `b069e77`; the evaluation change that enforces pairing, this ADR and the provenance link layer are uncommitted and not approved (corrected 2026-11-02).

**Context.** This decision rests on a baseline audit and a plan that were never written to a file in the repository. What survives is the audit's facts in the project owner's notes and some read-only prototypes, which are not in this repository. Everything below marks which statements were checked against the code on 2026-11-02 and which are carried over. One carried-over fact was wrong and is corrected here: the prefixes `ea3e3b62`, `83510046` and `3d6ea83a` were noted as the members' population hashes; they are the `trace_vehicles.json` file hashes. The population hashes are `7bddb137…` (T1), `491de28d…` (T2 and T3) and `c8ec465f…` (T4).

**Verified on 2026-11-02:** HEAD `48ce384`, nothing changed since the audit and its timing spike; `apps/web/` holds a `.gitkeep` only (the UI is `apps/api` static HTML); `TrafficWorldModel` carries an optional camera layer that is omitted from the JSON when unset; the camera-aware snapshot exists in a Git-ignored run directory; no D1/D2 allocator, `PairIdentity`, population hash or ladder exists in tracked code; `compare.compute_evaluation` paired by seed only; the 95% interval was written three times; no pre-registration artifact for a Holm family exists; the suite passed 491 tests before M1.

**Decisions.**

1. **Local Muse 0.3.0 replies are excluded from the confirmatory comparison and kept as labelled exploratory evidence.** Evidence: the cache holds one committed reply (prompt 0.1.0, ADR-020) and Git-ignored replies for 0.3.0 (`51188ff3…` and replicates, `6dd46648…`); their outcomes on seeds 1-20 are known (nothing selected); their evidence carried the false limitation ADR-032 removed; the cache refuses a reply recorded under another prompt version. Consequence: the confirmatory Muse arm needs a fresh 0.3.1 reply, one paid request. Project owner: not for the exclusion; the request needs a per-call approval and is not authorised.
2. **The camera-aware reply (`6dd46648…`) stays uncommitted.** It cited none of the 12 camera items; committing a model reply is the project owner's decision (ADR-020); licensing of the combined material is unreviewed. Consequence: a clean clone cannot replay it, and a committed-only inspector shows the camera layer as existing but not attached to the decision-driving snapshot. Project owner: nothing to do; committing is theirs.
3. **EQ-1 (D1, equal-saturation allocation) assumptions**, from the prototype: (a) the 17-phase cut program, cycle 99 s kept; (b) free phases p0, p5, p8, p11, p14, p16, with p13 fixed because its change cannot be mapped onto the replays; (c) demand = per-movement whole-clip `flow_veh_per_h` from `scenario.json`, confidence intervals ignored; (d) per-head critical flow: N and S through flow per through lane, E and W the larger of through and left per lane, N and S left per left lane, right turns ignored, lanes counted from `allowed_turns`; (e) **equal saturation flow for every head and no lost-time term** (load = y/g); (f) a head's green is the sum of its phases, fixed ones included; (g) integer seconds, each free phase at least 5 s, the first vector in the ordering that passes `validation.timing_problems`; (h) order: leximin of the descending load vector, then smaller L1 change from the baseline, then lexicographic, in exact integer arithmetic; (i) result 7-phase vector (p0, p5, p8, p11, p13, p14, p16) = (29, 5, 10, 7, 1, 12, 5), input-spec sha `3aaa9926fb3f…`, plan sha `5ed562a348ac…` (the prototype's plan hash is over program id, phase names and durations and is **not** `pairing.plan_sha256`). Supported by committed data and code: a, c, d (inputs), f, g (validator). Unsupported by any calibration: e, and d's treatment of shared lanes and permissive lefts. Project owner: **(e) accepted on 2026-11-03** as an explicit experimental assumption (the project owner's execution directive: "Treat this as an explicit experimental assumption, not an empirically established traffic characteristic"); every EQ-1 record carries it as such. Implemented 2026-11-03 in `cairoflow_simulation.eq1` ("eq1-v1"): an exhaustive search over all 962,598 integer vectors of the six free phases (each at least 5 s, pool 68 s, cycle kept), exact integer loads, the ordering of (h), first vector without `timing_problems`; on the committed inputs (`tests/fixtures/eq1_cut_program.json`, the cut program, plus `scenario.json` and `geometry.json`) it gives (29, 5, 10, 7, 1, 12, 5) at rank 1, `pairing.plan_sha256` `60bc41d3…`. Not yet: a report of EQ-1 as more than a baseline-free comparator needs the confirmatory evaluation below. **EQ-1 v2 ("eq1-v2"), approved by the project owner on 2026-11-03 ("Approve EQ-1 v2 (p16 fixed)")**: the held-out evaluation of eq1-v1 found that its p16 change applies to no replayed phase on members T2 and T4 (there p16's replays are all shorter than the 5 s minimum green, so `replay_candidates` leaves them unchanged). eq1-v2 changes (b) only: free phases p0, p5, p8, p11, p14; p13 and p16 fixed, each for the same reason (a free phase must change at least one replayed phase on every member). Everything else in (a)-(h) and assumption (e) is unchanged. eq1-v1 and its evaluation stay on record unchanged; eq1-v2 is a new version with its own pre-registration and evaluation.
4. **The Holm family is frozen when the ladder plan is pre-registered**: a committed record naming the confirmatory arms (by plan hash), the improvement metrics, the seed roles and alpha, before the first held-out run. Per member, size = arms x 5 improvement metrics (`mean_delay_s`, `mean_queue_m.N/E/S/W`), alpha 0.05 (`significance.FAMILY_ALPHA`); a planned test that cannot run still counts. Evidence: `significance.py`, `compare.improvement_plan`, `ensemble.py`. Gap: today the family is fixed only when the experiment manifest is written; the pre-registration record does not exist. Project owner: the existing rule stands; the plan itself is approved at pre-registration.
5. **Seeds 301-320 stay reserved.** Roles, carried over from the plan and its spike: 1-20 legacy (used by the pre-registered replays and the Muse runs; exposed), 101-108 D2 search, 201-220 held-out confirmation, 301-320 reserve, unused and unrecorded until a documented trigger. Evidence: no tracked file defines these roles; this ADR now does. Enforcement in code is M2a. Project owner: no.
6. **Canonical population hash**: "population-v1", defined in `cairoflow_simulation.population`. `speedFactor` is excluded and hashed separately; the route file's own SHA-256 covers both. Evidence: it equals the spike's function on all four members. Project owner: no.
7. **Pairing is strictly within a timeline member**, on equal member, timeline, seed, demand file, population, world model, geometry and settings (seeds aside), with different arms; two candidates of a member may pair with each other. Evidence: T2 and T3 share a population and differ in timeline. Project owner: no.
8. **D2 selection rule (approved 2026-11-02).** Move family, as enumerated in the prototype: for the 7 adjustable phases, 168 pair moves (a to b, 2, 4, 6 or 8 s), 28 single increases and 28 single decreases (224); 88 distinct moves were validator-accepted and 80 mapped onto all four members. Recomputed in tracked code on 2026-11-02 (approved by the project owner: "Yes, recompute moves"; uncommitted): `cairoflow_simulation.d2_moves` ("d2-moves-v1") and `scripts/d2_move_family.py` (read-only, no SUMO) reproduce 224 generated, 88 distinct accepted (52 pair, 26 increase, 10 decrease; every rejection is a green outside 5-90 s; no two moves validate to the same timing), mapped per member T1 85, T2 80, T3 85, T4 80, on all four 80 (52 pair, 19 increase, 9 decrease), `menu_sha256` `709bc2cf…`, on the cut baseline `ngsim-int2-baseline-20261028T074304Z` (program `ngsim_int2_cycle`, cycle 99 s), bundle `94d8ba7f75925ed4` and ensemble record `ngsim-int2-timeline-ensemble-20261029T123045Z`. New fact: "mapped" allows the minimum-green clamp, and 21-26 of each member's mapped moves are stopped at 5 s on some replay cycle (T1 26, T2 21, T3 26, T4 26), so on those cycles the move is smaller than its label. Rule: search seeds 101-108 on each member; per move, score = the largest over members of the mean delay difference; admissible if on every member `candidate_standings` finds it compared, with no uncorrected worsening and no entry holding, and the score is below zero; choose the lowest score, ties by fewer changed phases, smaller L1 change, then lexicographic durations; none admissible = `no_feasible_plan`, reported `not_testable`, never a no-op. Freeze its plan hash in the ladder plan before seeds 201-220. Open: the planning notes mention an "R" arm as descriptive and outside the family; its definition is in no surviving file and was not reconstructed. Project owner: **approved** on 2026-11-02, the project owner's words "Approve D2 rule", choosing this rule (the worst member's mean delay difference, admissibility on every member) over a mean-over-members score or a single pre-specified plan. On 2026-11-03 the project owner's execution directive authorised the search on seeds 101-108, freezing the selected plan and its evaluation on seeds 201-220 once the D1 and D2 tests pass and the selection code is reviewed; seeds 301-320 stay reserved. Scoring and selection: `cairoflow_evaluation.d2_selection` ("d2-rule-v1", synthetic tests only); search runner: `scripts/run_d2_search_experiment.py`. The "R" arm stays undefined: no surviving file defines it, so M2a is a two-arm comparison (D1, D2) against the baseline and makes no three-arm claim.
9. **Licence and attribution of the world model snapshot.** The snapshot is derived from NGSIM Lankershim trajectories (CC BY-SA 3.0); the snapshot file is Git-ignored; the committed derived files (geometry, timelines, counts) are shared under CC BY-SA 3.0 with the NOTICE attribution; the camera layer derives from NGSIM video pixels (CC BY-SA 4.0, each folder with its own NOTICE.md); SAM output is acknowledged as SAM 3.1 output. A `WorldView` of that snapshot is a derived work and inherits those terms. Not resolved: the CC legal texts and Meta's retention terms for uploaded footage were never read (S2.1 worksheet D-02), so whether trajectory-derived and camera-derived entities may share one published file is unreviewed. Handling until then: every entity names its source; no combined view is committed or published. Project owner: yes, before any snapshot-derived view is committed or published.
10. **External access, spending, publication, pushing.** M1 needed none: the suite uses mocked HTTP, no key was read, nothing was pushed (the M1 foundation was later committed locally as `b069e77`; nothing else was). Later, each needs its own approval: a fresh Muse 0.3.1 request (about $0.04 by the earlier runs), the SAM acquisition test (about $0.14), committing M1, publishing, and any push.

**Consequences.** With the held evaluation change applied (not in commit `b069e77`), `compute_evaluation` refuses an experiment whose recorded candidate runs differ from the baseline's in route-file hash, signal timeline, geometry, network (apart from the signal program), phase structure, SUMO command or run settings, or that repeat a seed or an intervention id (`PairingError` as `EvaluationError`). It checks what the records hold: a hand-edited manifest, or a run that left a different trace, is caught; the member, world model and population of a candidate are taken from its baseline, so they matter only when the ladder pairs arms of different experiments. The interval formula has one owner; contract changes are snapshot-checked. Valid experiments evaluate unchanged: all 38 recorded experiment manifests evaluate with none refused.

**Evidence.** 642 tests passed on 2026-11-02 (491 before M1; 96 s before, 96 s after); two independent reviews of the first version found real faults, now fixed (relation certainty, bundle and network ties, silent drops, pair checks that compared copied fields, a population hash that ignored route features); red/green: with the pairing call disabled the three refusal tests fail with "DID NOT RAISE"; `scripts/contract_snapshot.py` reports no difference; population hashes equal the G0 function on 4 of 4 members.

**Correction and addendum (2026-11-02, after commit `b069e77`).** The "642 tests" and "96 s" above were measured before the commit and before the provenance and `/flow` tests. Rechecked: commit `b069e77` alone, in a detached worktree with its own environment, 599 passed and 16 skipped (each a Git-ignored record), 88-90 s; the full working tree 684 passed and 2 failed, 93 s, both failures asserting that the stray `experiments/runs/ngsim-int2-baseline-20261028T074304Z/never-created/` (left by an earlier test run in a symlinked clone) does not exist, so not code failures. Provenance link layer, uncommitted: `cairoflow_contracts.provenance` (nodes, links, a typed chain walker and a minimal rule-made `ProblemStatement`; five categories camera_derived, dataset_derived, world_model, simulated, ai_generated; SAM output labelled model output), builders in `cairoflow_perception.provenance` and `cairoflow_evaluation.provenance`, `scripts/trace_provenance.py`, 26 tests, a first snapshot `schemas/provenance.json`; no existing model, record or hash changed. On recorded runs the chain is complete from the world model down to the NGSIM file and the camera clip, and breaks between every proposal and a problem statement: no record ties a proposal to a problem, and no producer of a problem statement exists. Project owner decisions are unchanged by this addendum.

**Results, 2026-11-03 (simulated, trajectory-derived timelines).** D2 search, seeds 101-108, all 80 menu moves on T1-T4 (2,592 SUMO runs, rules 0.3.1, `experiments/records/d2-search-20261102T232051Z.json`): **no feasible plan, D2 not testable** (`d2-plan-20261102T232051Z.json`). No move is admissible on every member; the move with the lowest worst-member delay score (p11 +8 s, -1.54 s on T4) worsens the E and W queues on T1 and T2. Pre-registration `ladder-preregistration-20261102T233412Z.json` (committed before any held-out run): D1 plan `60bc41d3…`, D2 not testable, seeds 201-220, Holm family per member. Held-out evaluation `ladder-evaluation-20261102T233511Z.json`: **D1 `no_support`**: eligible on no member; the E, S and W queues worsen on every member (robust harm); delay and throughput worsen on T1, T2 and T4; vehicles are held off at the N entry on T1 and T4. Defect found by the evaluation: on T2 and T4 D1's p16 change maps onto no replayed phase, so it is not the same plan there; decision 3(b) fixed p13 for that reason but left p16 free. The evidence stands as recorded; correcting it means a new EQ-1 version (p16 fixed or a mapping rule), which changes decision 3(b) and needs the project owner, and then a new pre-registered experiment. The R arm is not an arm: no surviving file defines it. No result here is a recommendation: the held-out baselines are not assessed. **eq1-v2** (approved the same day; plan (27, 5, 10, 7, 1, 11, 8), `789635426ea6…`), pre-registered in `ladder-preregistration-20261103T001537Z.json` before its run and evaluated on the same seeds 201-220 (`ladder-evaluation-20261103T001603Z.json`; the pre-registration lists the eq1-v1 evaluation that used them, and eq1-v2 was derived from the committed inputs and the replay mapping, not from that evaluation's outcomes): the plan is now the same plan on every member, and **D1 v2 has `no_support`**: mean delay worsens on every member (+1.00 to +1.83 s, every 95% interval above zero), and robust harm covers mean delay, throughput and the E and S queues; no entry is held. Under assumption (e) the equal-saturation allocation does not improve this junction in simulation; the comparator ladder has no supported arm.

## ADR-034 — PairIdentityView: the pairs of one experiment as a derived sidecar (2026-11-03)

**Decision.** New contract module `cairoflow_contracts.pair_identity` (registered in `governance.CONTRACT_MODULES`, first snapshot `schemas/pair_identity.json`; no other snapshot changed). A `PairIdentityView` is computed from existing records only (`cairoflow_evaluation.pair_identity`): the member and timeline every pair lives in, each seed's demand file hash as recorded and as the file hashes now, its population-v1 and speed-factor hashes, each arm's plan, network and SUMO binary (read from the output header; the eclipse-sumo wheel version is kept apart), the shared horizon, the existing `PairIdentity` pairs and the unit of inference ("n = 20 seeds on one vehicle realisation; conditional on it" for a replay). It adds no field to `RunRecord` or any frozen file. Seeds 301-320 (the reserve of ADR-033 decision 5, which the execution plan calls the reserved confirmation seeds) are refused unless `confirmation=True`. After an independent review the validator also checks every pair side against its arm (plan, network, intervention) and the view (world model, network, the seed's demand and population), requires exactly one baseline-referenced pair per candidate and seed, and requires canonical order. Tests: `tests/test_pair_identity.py` (the T2 realism record rebuilds its 20 recorded demand hashes; skipped where the run directories are absent).

**Decision chain (same day).** New contract module `cairoflow_contracts.provenance_chain` (registered, first snapshot `schemas/provenance_chain.json`; no other snapshot changed): a link layer over the frozen `provenance` records. It adds the BaselineManifest and the NGSIM observations (the NGSIM track points, observed, and the movement counts and queue samples CairoFlow derives from them by fixed rules, labelled as the world model labels itself; dataset-derived, not SAM observations), input links (experiment <- baseline <- world model snapshot; the only links allowed to skip stages), refused links, and `walk(graph, decision_id)`, which returns the best path to a source hash plus every typed break. The camera layer's link into the World Model is refused: calibration not accepted (ADR-017/018). Measured on the T2 realism run (report `ef995ff590426b18`): the path reaches the NGSIM file `c74ef7cc…` through the baseline's inputs, and the chain is not full: its candidates record no proposal hash, so the walk's one break is `experiment <- intervention` (missing). Separately, where proposals are recorded, no record ties a proposal to a problem (the frozen layer's finding, ADR-033 addendum). With the view and its world model, entities are re-linked to what their SourceRef names; entities drawn from the geometry, the signal timeline or the network get no link (a typed break), and a graph with camera entities cannot be assembled without them. Tests: `tests/test_provenance_chain.py`.

**ProblemStatement (G3a, same day).** New contract module `cairoflow_contracts.problem_statement` (registered, first snapshot `schemas/problem_statement.json`; no other snapshot changed), built by `cairoflow_evaluation.problem_statement` from committed records only: the timeline-ensemble record `ngsim-int2-timeline-ensemble-20261029T123045Z.json`, the four member experiment records, the two cited replay assessments (re-hashed) and the T1/T2 realism validation records. Subject: the S approach, `mean_queue_m.S`. It copies, never computes: the simulated baseline mean per member (24.7-26.0 m), the Holm-confirmed improvements (I1 on T1-T4, I3 on T1-T3), the NGSIM-derived S quantities with their same-definition simulated values and comparability (mean longest-lane queue 24.8 m observed, a censored lower bound), the assessments (T1, T2 partially validated; T3, T4 not assessed) and the record's limitations as failure conditions. The Muse hypotheses of the pinned reply attach as AI-generated text when the Git-ignored reply is present, never as evidence. The statement text is written by code and re-checked by the validator. Tests: `tests/test_problem_statement.py`. **Correction after an independent review (same day):** the first statement set the decision metric's simulated range (24.7-26.0 m, SUMO's definition, which has no observed counterpart) beside the NGSIM-derived 24.8 m (the observed definition), which reads as agreement. The records say the opposite: with the same definition the assessed baselines simulate 18.1 m (T1) and 17.3 m (T2), outside the observed interval [18.9, 30.8]. The statement now gives each value with its own definition, and carries each comparison's interval verdict, discrepancy and caveats. Failure conditions now also carry each assessment's own summary ("It is not validated for estimating intervention effects.") and the validation records' note on the metric's definition. The builder re-hashes each validation record against the hash its assessment pins, and the unit of inference rests on each baseline record's own demand note. The validator checks the Holm and interval facts of every listed improvement; the record's hash is a content hash, so it is checked by rebuilding, not trusted. The WorldView export is now in `.gitignore` (it was excluded on one machine only). The `problem_statement` snapshot was regenerated with `--accept-breaking` (new required fields): no record cites a ProblemStatement yet, so nothing needs migrating.

## ADR-035 — One analysis record for the product, and the delivery decisions (2026-11-04)

**Decision.** New contract module `cairoflow_contracts.analysis_record` (registered, first snapshot `schemas/analysis_record.json`; no other contract changed). One immutable directory per analysis holds `record.json`, `tracks.json` and a columnar `trajectories.bin`, read by the video viewer, analytics, 3D twin and evidence pages. Every displayed number is a `Metric` or `MetricSeries` carrying its evidence kind and a status (`gate_pass`, `gate_fail`, `ungated`, `not_available`); a withheld value is absent, never zero. `data_origin` separates `camera_cv` from `reference_dataset` so published NGSIM rows are never shown as computer-vision output.

**Owner decisions, same day.** Public repository: derived tracks and analytics only, CC BY-SA 4.0, no pixels (this lifts ADR-033 #9 for derived data; pixel-bearing files stay out). CC BY-SA 3.0 section 4(b) lets an adaptation use a later version with the same licence elements, so derived data from the 3.0 trajectories and 4.0 video is published under 4.0. Meta spend for this project is capped at $3.00 cumulative. Detector selection is Apache-first, with an AGPL adapter only as an opt-in and only if the best Apache model trails by 0.10 recall or more. The web app is Vite and React rather than Next.js (no server rendering is needed, and FastAPI serves the static build); 3D is React Three Fiber alone.

**DecisionSummary (same day).** New contract module `cairoflow_contracts.decision_summary` (registered, first snapshot `schemas/decision_summary.json`; no other contract changed): the recorded decision as one file the product can serve without any pixel-bearing input, copied by code from the records pinned in the demo manifests, with each source path and SHA-256.

**CvDiagnosis (same day).** New contract module `cairoflow_contracts.cv_diagnosis` (registered, first snapshot `schemas/cv_diagnosis.json`; no other contract changed): Muse Spark hypotheses about a camera analysis, with code-checked citations and a list of any numbers the evidence does not contain.

## ADR-036 — Camera agreement gate cv-int2-v1, registered before its held-out read (2026-11-04)

**Decision.** `data/gates/cv-int2-v1.json` sets which camera-derived measures may show as gate-passed. It was committed before any held-out block was read, with the footage split into alternating 60 s blocks (`floor((source_s - 120) / 60)`: even dev, odd held out). It measures agreement with the published NGSIM trajectories, never accuracy: the reference shares the video and the placement was fitted to it. One read per gate id; a further read needs a new version.

**Thresholds.** Placement across heading at most 1.5 m and lane agreement at least 0.85 are the limits of camera gates v1 to v3. Vehicle recall at least 0.70: most vehicles must be found for counts to mean anything. Precision proxy at least 0.80: the bake-off's bar. Entering count error share at most 0.20 and turning-matrix agreement at least 0.80: what a turning-movement study can use. Median speed error at most 1.0 m/s, about 3.6 km/h against moving speeds of 8 to 10 m/s here. On dev block 14 the run measured 0.79, 0.93, 0.72 m, 0.95, 0.13, 0.96 and 0.47 m/s; thresholds were not moved after that.

**Detector and tracker.** The bake-off (`experiments/records/cv-bakeoff-20261103T235327Z`) selected VisDrone YOLO26s at 0.806 recall against D-FINE-L at 0.636 on dev frames, so the AGPL opt-in applies under ADR-035. The committed default stays Apache. Tracker `high_score` 0.27 and `low_score` 0.03 were chosen on dev blocks 14 and 4.

**Speed and ADR-018.** ADR-018 rejects along-road distances from this homography. A constant along-road offset cancels in a difference, so speed may still agree with the reference; this gate tests that directly. Speed is shown only if the gate passes, and then as agreement with NGSIM.

**Gate v2 (same day).** The one read of `cv-int2-v1` was spent at 01:15Z on a full-span run that died at about 01:22Z when the process running it ended. It wrote no analysis, so no held-out figure was computed or seen by anyone; only the detector touched those frames. The log `data/gates/held_out_reads.jsonl` keeps that read. `cv-int2-v2` has the same thresholds and blocks, changed only in id and date, and takes its own single read. Nothing was tuned between the two.

**Gate v2 result (same day).** Read once, on the seven held-out blocks of `cv-ngsim-int2-15min`: recall 0.7612, precision proxy 0.8634, across-heading p90 1.3891 m, lane agreement 0.8808, turning-matrix agreement 0.847 and median speed error 0.4763 m/s pass; entering count error share 0.2033 fails its limit of 0.20. The limit was not moved and no further read was taken: the held-out data has now been seen, so any new gate on it would be contaminated. `scripts/finalize_analysis.py` therefore withholds entering flow and per-arm entering counts in `cv-ngsim-int2-15min-gated`, and the screens show the reason. The vehicle total stays ungated, because recall does not validate a total. Dev blocks for comparison: 0.7818, 0.868, 0.9605 m, 0.9245, 0.1381, 0.8949, 0.4497 m/s, all passing, which shows how optimistic dev figures were.

**Audit correction (same day).** An independent audit of the public claims found that the camera homography was never held out: 15 of its 29 fit points and 12 of its 21 check points come from frames inside the odd blocks, and the clicks predate the gate. So the across-road and lane checks are not independent of the fit, although the detector, tracker and thresholds were tuned on even blocks only. The sentence in `cv-ngsim-int2-15min-gated` saying the pipeline was not tuned on those blocks was wrong. Analyses are immutable, so `cv-ngsim-int2-15min-checked` replaces it with the corrected disclosure and labels the total as counted tracks; every figure is unchanged. Muse Spark was asked again on the corrected record, and its three replies were clean.

**Second audit correction (same day).** A claims audit found more wording that said too much, so `cv-ngsim-int2-15min-study` replaces `-checked` and `-gated`, which stay in Git history. Stopped share loses its gate pass and keeps its value, ungated: the speed check scores only vehicles moving at 2 m/s or more, so no gate covers stopped time. The class reason was false: NGSIM publishes a `v_class` field, populated for these rows, and our 12 fetched columns leave it out, so classes weren't compared. The disclosures now say Claude read the fit points, the turning check is pooled, and speed agreement is empirical (the along-road offset isn't constant, so the argument above that it cancels is withdrawn); the decision summary says no baseline is validated. Every figure is unchanged; only stopped share's status moved. Muse Spark was asked again on this record, and its three replies passed both checks. The two reference analyses keep the old class sentence, because analyses are immutable; `scripts/build_reference_analysis.py` is corrected for any rebuild.
