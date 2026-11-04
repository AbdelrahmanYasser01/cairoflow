# Architecture and Data Contracts

## System flow
Decision path: published trajectories (and, where a gate allows, SAM 3.1 camera tracks) -> Traffic World Model -> Muse Spark diagnosis and candidate actions -> deterministic scenario validation -> SUMO baseline/interventions -> metric extraction -> ranking -> evidence-linked report.

Camera path: video -> local detector and tracker -> placement on the ground -> analysis record -> agreement gate on held-out blocks -> the analysis people see, with Muse Spark's hypotheses about it.

## Trust boundaries
- Perception: model output is evidence with known limitations, not ground truth.
- World model: derived estimates with provenance and uncertainty.
- Reasoning: hypotheses and candidate interventions, not facts.
- Scenario compiler: deterministic allowlist of supported SUMO modifications.
- Simulator: source of simulated outcome metrics.
- Evaluator: deterministic metric calculations and ranking.
- UI: must preserve the distinction between observed, inferred, assumed, and simulated.

## Coordinate systems
Document source image pixel coordinates, calibrated ground-plane coordinates, map coordinates, and SUMO network coordinates separately. Define transforms explicitly. Never compare coordinates from different spaces without a transform.

## The product
One server, `python -m cairoflow_api.serve`, answers three surfaces, tried in this order.

| Surface | Code | What it is |
|---|---|---|
| API | `apps/api/cairoflow_api/product` | `/api/v1`: health, analyses (record, tracks, trajectories, artifacts, diagnosis), site, intersections, decision, sources, uploads and jobs. A view of files on disk: client input never becomes a path, and every figure comes from a stored record or from a worker's own report. `var/cairoflow.db` is a SQLite index rebuilt from the analysis directories on every boot; files stay the source of truth |
| Web app | `apps/web` | Vite, React, TypeScript strict, Tailwind CSS 4, React Three Fiber for the twin (ADR-035 records the move from Next.js: no server rendering is needed). Six pages: `/`, `/intersections`, `/vision`, `/twin`, `/analytics`, `/evidence`. The server serves the static build from `apps/web/dist`; on a fresh clone there is no build, and these pages answer with the commands to make one |
| Report pages | `apps/api/cairoflow_api/app.py` | `/demo`, `/flow`, `/world`, `/problem`, `/decision/trace`, `/runs/<name>/`: server-rendered, no scripts, a strict Content-Security-Policy, read from records pinned by SHA-256 in a demo manifest |

The web app holds no metrics of its own: until an analysis is loaded, each page shows an empty state.

No footage is distributed with this repository. `data/demo/ngsim-int2-no-footage.json` pins the same records as the full manifest and names the pixel-bearing pins in `absent_allowed`, so `/demo` loads without them and says so; a file that is there must still match its pin, and no record may be marked. The manifest `data/sample/decision_summary.json` pins stays as it was.

## Camera analysis
`cairoflow_perception.local.pipeline.run_analysis` takes a video to one analysis directory. Entry points: `scripts/cv_analyse.py` writes a precomputed analysis; `scripts/cv_run.py` is the worker the API starts for a job (protocol `cairoflow-job/1`, `apps/api/cairoflow_api/product/jobs.py`), one job at a time, with progress only as the worker reports it.

decode -> detect -> track -> relink -> place on the ground -> compute -> write

- Detect: any object with `detect(frame_bgr)`. `data/cv/detector.json` picks the model; `local/detectors.py` is the factory, the default is Apache-2.0, and the AGPL-3.0 adapter loads only if chosen (`NOTICE`). `scripts/cv_bakeoff.py` scored the candidates with the SAM baseline's definitions.
- Track and relink: `local/tracker.py`. A detector's boxes are the only source of positions; nothing is filled in.
- Place and compute: `local/analytics.py`. Geometry is always evidence kind `approximated`. A run stores values as ungated and withholds speed (ADR-018).
- A run completes or leaves nothing: it writes to a hidden sibling and renames, and an analysis directory is immutable.
- Odd 60 s blocks of camera 2 are held out: a window that reaches one is refused before any pixel is read, unless a committed gate has claimed its one read. A job never reads held-out footage.

## Agreement gate
A gate file (`data/gates/<gate id>.json`) is committed before its read and fixes seven checks, each with a threshold and a minimum sample: vehicle recall, a precision proxy, across-heading p90, lane agreement, entering count error share, turning matrix agreement and median speed difference. Blocks are `floor((source_s - 120) / 60)`: even blocks are for development, odd blocks are held out.

`scripts/cv_gate.py` scores an analysis against the published trajectories (`cairoflow_evaluation.cv_agreement`). `--split dev` can be repeated; `--split held_out` is refused unless the gate file is committed and clean and the analysis is the one the gate's read named. The read and the score are logged in `data/gates/held_out_reads.jsonl` before any held-out row is touched, and a second read needs a new gate version. `scripts/finalize_analysis.py` then writes a new analysis in which each value carries what the gate decided: a passed check marks it `gate_pass`, a failed one removes it, and what no gate covers stays `ungated` with its reason.

What is measured is agreement with a correlated reference (the same video, to which the camera placement was fitted), never accuracy.

## Analysis record
`cairoflow_contracts.analysis_record` (record format `analysis/1`). One analysis is one immutable directory read by every consumer: `record.json`, `tracks.json`, and a columnar `trajectories.bin` a browser can view with typed arrays (format in `cairoflow_perception.analysis_store`; `verify_analysis` checks every bundled artifact against its hash).

- Every number on a screen is a `Metric` or `MetricSeries`, so it says what kind of evidence it is and whether a gate covered it. A value withheld by a failed gate is absent, not zero.
- `data_origin` keeps `camera_cv` (produced from video by this pipeline) apart from `reference_dataset` (read from the published NGSIM trajectories), which is never shown as computer-vision output.
- Ground values are metres in the junction frame of `data/geometry/ngsim-lankershim-int2/geometry.json` (`local_enu_junction_m`).
- `data/sample/analyses/` holds the two reference analyses and the gated 15-minute study; `data/analyses/` holds what a machine has run locally and is not committed.

## Initial schemas
The first sketch of the data model, kept for orientation. The Pydantic contracts in `packages/contracts` are the definition.

### Observation
- observation_id
- source_video_id
- frame_index
- timestamp_seconds
- track_id
- class_label
- bbox_xyxy_px
- mask_artifact_uri (optional)
- centroid_px
- confidence (nullable if not supplied)
- provenance

### TrafficStateSnapshot
- snapshot_id
- timestamp_seconds
- camera_id
- vehicles[]
- lane_occupancy_by_lane
- queue_estimate_by_lane
- flow_estimate_by_movement
- observed_signal_state (nullable)
- uncertainty_by_field
- calibration_version
- derived_from_observation_ids[]

### Hypothesis
- hypothesis_id
- statement
- supporting_evidence_ids[]
- contradicting_evidence_ids[]
- assumptions[]
- confidence_label
- alternative_explanations[]

### Intervention
- intervention_id
- type (initial allowlist: signal_plan, turn_restriction, route_guidance, demand_stress_test)
- target
- parameters
- rationale
- expected_tradeoffs
- preconditions
- validation_status

### ExperimentRun
- experiment_id
- scenario_id
- baseline_id
- input_state_version
- network_version
- demand_profile_version
- random_seed
- simulator_version
- config_hash
- start/end timestamps
- status

### MetricResult
- experiment_id
- metric_name
- value
- unit
- aggregation_window
- source_output_file
- calculation_version

### DecisionReport
- selected_intervention_id
- objective_definition
- weights
- metric_comparisons
- tradeoffs
- evidence_refs[]
- uncertainty_and_limitations[]
- reproducibility_manifest

## Intervention safety
Only support a small, explicit allowlist. Reject unknown action types, out-of-range values, invalid signal programs, routes referencing unknown edges, and changes that fail SUMO validation. No external actuation.

## Experiment fairness
Baseline and candidate scenarios must share the same network, demand, simulation horizon, warm-up, vehicle types, and seed set. For stochastic experiments, use multiple paired seeds and report mean, spread, and sample count. Do not compare one candidate run against a baseline with different demand.

## Module boundaries and interfaces
A modular monolith (ADR-003): one Python distribution, five packages, a thin FastAPI app. No database server, queue, container or service split. Stages communicate only through `cairoflow_contracts` models serialised as JSON artifacts.

| Area | Module | Public interface | Owns |
|---|---|---|---|
| Contracts | `cairoflow_contracts.config` (exists) | `load_settings() -> Settings` | Settings; key as `SecretStr` |
| Evidence schemas | `cairoflow_contracts.evidence` (**exists, schema 0.1.0**: `Provenance`, `ClipLicense`, `VideoStreamInfo`, `TrimSpec`, `FrameRef`, `SourceClip`, `Observation`, `GeometryValue`) | Pydantic models, frozen, `extra="forbid"`; since added: `Track`, calibration and intersection-geometry contracts, `TrafficWorldModel`, `SignalTimeline`, with the reasoning, experiment and report contracts in their own modules. Every model has `schema_version` | The vocabulary; no I/O |
| Video ingestion | `cairoflow_perception.ingest` (**exists**) | `ingest_clip(source, licence, out_dir, *, trim=None, target_fps=10) -> SourceClip` (refuses unusable licences and overwrites; ffprobe; trim; bit-exact 10 fps H.264 `+faststart`; SHA-256; every frame with its timestamp; `source_clip.json`); `load_source_clip(manifest)` re-checks the hash; `new_run_dir()` | Clip files and their manifest |
| SAM integration | `cairoflow_perception.sam_adapter`, `.sam_parse` (**exist**, ADR-016) | `SamClient(settings, cache_dir, mode, *, spend_limit_usd=0.0).segment_video(clip, clip_path, prompt) -> (SamRequestRecord, from_cache)`; `.segment_and_parse(...) -> ParsedSegmentation`; `parse_sam_stream(lines, clip, ...) -> ParsedSegmentation` (official `parse_responses_stream` + `video_segmentation_format`; Observations namespaced by class; masks referenced as `cache://sam/<key>/<order>`) | Raw SAM streams in `cache/sam/`, failed attempts in `cache/sam/failed/`; request records (id, latency, frames billed, estimated cost) |
| Tracking | `cairoflow_perception.tracking` (**exists**) | `build_tracks(observations, policy=None, *, now=None) -> TrackingResult` (tracks plus every removed duplicate, bridged gap and dropped short chain, with reasons). Steps: cross-class duplicates by IoU, segments split at gaps > `max_gap_frames`, constant-velocity gap bridging between ids of one class, minimum length. Input-order independent. Track points carry the box bottom-centre as the image contact point | Track ids (`car:t3`); `Track`/`TrackPoint` contracts in `cairoflow_contracts.evidence` |
| Review overlay | `cairoflow_perception.overlay` (**exists**) | `render_overlay(clip, clip_path, tracks, out_path, *, trail_frames=10) -> OverlayManifest` (boxes, track ids, contact points, trails; "NOT GROUND TRUTH" caption, timestamp and licence attribution on every frame; bit-exact H.264; refuses mismatched clips or tracks and never overwrites) | Overlay videos and their manifests; a review artifact, not evidence |
| Calibration | `cairoflow_perception.calibration` (**exists**) | `fit_homography(spec, *, target_max_check_error_m=1.0) -> (Homography, CalibrationReport)` (deterministic least squares on `fit` points; collinear, too few or horizon-straddling points rejected; accuracy only from held-out `check` points, in metres and pixels; accepted only with check points within target); `Homography.to_ground/to_image` (refuses points beyond the horizon), `.ground_error_at(px, sigma_px)` (pixel noise to metres, grows with distance), `.from_report`; `save_report` (never overwrites). Contracts `ControlPoint` (with ADR-014 label), `CalibrationSpec`, `CalibrationReport` | `calibration.json` and its report |
| Reference trajectories | `cairoflow_perception.ngsim` (**exists**, ADR-017) | `load_trajectories(path) -> Trajectories` (exact 12-column header, typed arrays); `CA_ZONE5_FTUS.to_geographic/from_geographic`, `.convergence_deg`, `.scale_factor` (EPSG:2229 Lambert conic); `LocalFrame` (State Plane ft to local metres); `kinematics` (speed and heading from consecutive 100 ms frames only, heading kept while stopped); `along_across`, `lane_offsets`, `boundaries`, `queue_heads`, `footprint_occupancy` | Nothing written; NGSIM data stays in `data/raw` (CC BY-SA 3.0) |
| Junction from published trajectories | `cairoflow_perception.ngsim_junction` (**exists**, ADR-019); driven by `scripts/build_ngsim_int2_scenario.py` | `junction_frame` (rotated to Lankershim NB), `junction_rows`, `ground_series` (`GroundSeries` per vehicle run, NGSIM's stated 4 ft as error), `arm_statistics` (direction of travel from velocity; counting gates from pooled queue heads, spanning inbound vehicles; stop positions or "not observable"; lanes per NGSIM lane_id; exit lanes from outbound rows only), `build_geometry` (anchored on the dataset hash), `allowed_turns` (observed lane use), `estimate_signal_timeline` (`trajectory-derived`: per-head service windows, protected-left test, sparse arms, conflict trimming, consistency check, representative cycle). No camera input | `data/geometry/ngsim-lankershim-int2/` (geometry, timeline, scenario record); processed outputs and the SUMO baseline (Git-ignored) |
| Static scene | `cairoflow_perception.scene` (**exists**) | `median_background(video, step)`, `activity_map(video, background, step, threshold)`, `register(raster, image_map, initial_warp, mask) -> Registration` (ECC, affine then homography, no time sync), `correlation`, `profile_peaks`, `paint_strength`. Driven by `scripts/analyse_ngsim_cam2.py` (DATA_REGISTER.md §14) | Background, activity and registration in `data/processed/ngsim-cam2-analysis/`; the A/B/C correspondence record in `data/geometry/ngsim-lankershim-cam2/v2/` (no calibration) |
| Image-side geometry | contract `ImageGeometry` / `ImageLane` / `ImageLine` in `cairoflow_contracts.evidence` (**exists**); drawn by `scripts/draw_ngsim_cam2_geometry_v2.py` | Lanes and lines in image pixels, each with an ADR-014 label and an error in pixels. Turns may be unknown. There is one limit line per approach arm. Needs no calibration and is not read by the world model (which wants ground metres) | `data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json` |
| Signal timeline | `cairoflow_perception.signals` (**exists**) | `load_signal_annotation(path, clip, *, label, source) -> SignalTimeline`: CSV of changes `time_s,arm,state[,note]` (green, yellow, red, unknown; `#` comments; unknown before an arm's first row; repeats merged; errors name the line). `SignalTimeline` (label `observed-annotated`, `published`, `trajectory-derived` or `assumed`; never model-inferred) keeps exact intervals per head covering the clip, rejects gaps, transitions other than green → yellow → red → green (except via unknown), green/yellow on crossing arms at once, and a protected-left head green/yellow while the opposite arm's `all` head is; `.heads()`, `.state_at(arm, t, head="all")`, `.per_second()` (phase per second as `SignalSecond`, with `left_states`). Each arm has an `all` head and may have a protected `left` head | Annotation files, versioned with the calibration; derived timelines with their scenario |
| Traffic World Model | `cairoflow_perception.world_model` (**exists**) | `build_world_model(clip, tracks, geometry, calibration_spec, calibration_report, policy=None, *, signals=None) -> TrafficWorldModel`: with a timeline, every approach lane is also sampled in the last frame before its red turns green (`end_of_red_queues`); a red ending into unknown is noted, not guessed; the timeline and phase per second are embedded and the annotation hash is in the provenance; contact points to ground metres with per-point error; speed over ±0.5 s; movement = first inbound stop-line crossing then first outbound exit-line crossing (direction fixed by the origin at the junction centre, right-hand traffic); lane = approach polygon just before the stop line, checked against its markings; one-crossing tracks listed as ambiguous, not counted; flow = stop-line crossings over the clip with an exact Poisson 95% interval; queues per approach lane once per second (stopped < 1 m/s, chained back from the stop line with ≤ 12 m spacing, length error at the last vehicle; `censored` when the chain reaches the end of the observed lane, a lower bound). Refuses unaccepted calibrations and geometry drawn against another calibration. `build_world_model_from_ground(window_id, frames, window_s, series, geometry, source_sha256, ...)` applies the same rules to published ground positions (`GroundSeries`) with a geometry anchored on that dataset; `count_movements(series, geometry, window_s)` gives movements only. `poisson_interval`, `turn_for`, `exit_for`. Contracts `LanePolygon`, `Gate`, `IntersectionGeometry` and `TrafficWorldModel` (ADR-014 labels; exactly one frame anchor: a calibration spec hash or a ground dataset hash) | All derived measurements |
| Muse Spark integration | `cairoflow_reasoning.evidence`, `.prompts`, `.muse_adapter` (**exist**); contracts in `cairoflow_contracts.reasoning` | `build_evidence_bundle(world_model, network_manifest, limitations=(), *, end_of_red_per_lane=False, conventions=ARM_CONVENTIONS) -> EvidenceBundle` (every world-model and network value as a citable item with its evidence kind; adjustable phases with bounds; extra source limitations; end-of-red queues per sample or summarised per lane; naming conventions, by default "arm N = vehicles arriving from the north", left out of the JSON when empty so older bundles keep their ids; content-hash id; driven for the real intersection by `scripts/run_ngsim_int2_experiment.py`); `build_request_body(bundle, model, effort, max_output_tokens, store=None, prompt_version="0.2.0")` (prompt 0.2.0 states the arm convention; 0.1.0 kept in `PROMPTS` so cached replies replay; hand-written strict JSON schema using only plain keywords; `store` not sent until `store: false` is tested); `MuseClient(settings, cache_dir, mode, *, spend_limit_usd=0.0).diagnose(bundle) -> ReasoningResult` (cache-first like ADR-016; a cached file is used only if its stored body hashes to its key (ADR-020); replay never connects; upper-bound cost check before sending; one request, no retries or redirects; key scrubbed; failures and invalid replies kept under `failed/`, never replayed; reply schema-validated: >= 2 hypotheses and >= 2 proposals; unknown citations flagged in `problems`; provenance `model_generated`) | Raw Muse replies in `cache/muse/`; hypotheses and proposals (no metric values) |
| Intervention validation | `cairoflow_simulation.validation` (**exists**, ADR-015 accepted) | `validate_proposal(proposal, network: CompiledNetwork, bundle) -> ValidationOutcome` and `validate_proposals(...)` (allowlist `signal_timing`; target program; green phases only, clearance fixed (a phase with any yellow, or no green, is clearance); whole seconds in 5–90 s; cycle 30–180 s; not a no-op; cited evidence exists; no priority green on conflicting links; duplicate timings rejected). Candidate networks are built in the experiment step | Accept/reject decisions with every reason |
| SUMO experiments | `cairoflow_simulation.network` (exists), `.geometry_compiler`, `.demand`, `.runner` (**exist**); records in `cairoflow_contracts.experiment` | `build_network(out_dir, plan)` (hand-authored `baseline_4way`); `compile_network(geometry, signals, out_dir, options) -> CompiledNetwork` (approach lanes, kerb order, widths and turns from the drawn polygons; exit lanes drawn or synthetic default; 200 m arms, speed limit and straight N/E/S/W arms synthetic; every value a `NetworkParameter` with its ADR-014 label; drawn and SUMO stop-line distances both recorded; SUMO coordinates = ground metres (`--offset.disable-normalization`); signal program = one complete cycle cut from the `SignalTimeline` (the first, or the one starting at `CompileOptions.cycle_start_s`), left links following a protected-left head where there is one, turning links permissive `g` when a conflicting link is green or yellow, the cut checked to close legally and embedded state by state, parallel left lanes connected without crossing, optional right turn on red (`s`, junction type `traffic_light_right_on_red`, labelled synthetic)); `generate_demand(world_model, network, settings, out_dir) -> DemandManifest` (ADR-008: Poisson departures per movement at the world model's point flow, vehicle type from counted classes, `speedFactor` clipped normal, generator seeded by (seed, movement); counted U-turns noted, not simulated); `run_seed(...) -> RunRecord` and `run_baseline(world_model, geometry, signals, out_dir, settings) -> BaselineManifest` (refuses phase durations off the step; valid = no teleports, collisions (junction collisions checked) or unfinished vehicles; each seed rerun and compared on the comment-stripped tripinfo hash; manifest written once) | Networks, demand files, SUMO outputs, `baseline_manifest.json` |
| Candidate runs | `cairoflow_simulation.candidates` (**exists**) | `load_baseline(dir)`; `build_candidate_network(baseline, outcome, out_dir)` (baseline nodes, edges, connections copied and hash-checked; only green durations change; link order checked; program `<id>_<intervention>`); `run_candidates(baseline_dir, outcomes, out_dir) -> ExperimentManifest` (baseline settings, seeds and demand files, each demand hash re-checked; rejected outcomes listed as skipped; manifest written once) | Candidate networks, runs, `experiment_manifest.json` |
| Metrics | `cairoflow_evaluation.metrics`, `.compare` (**exist**), `.report` | `run_metrics(run_dir, ...) -> list[MetricResult]` from tripinfo.xml and queue.xml only, over [warm-up, warm-up + horizon): `mean_delay_s` (timeLoss + departDelay of vehicles departing in the window), `throughput_veh_per_h` (arrivals), `max_queue_m.<arm>` and `mean_queue_m.<arm>` (per step, longest approach-lane `queueing_length`); each with source file, its SHA-256 and calculation version. `paired_comparison` and `compute_evaluation(experiment_dir) -> EvaluationResult` / `evaluate_experiment` (writes `evaluation.json` once): per candidate and metric, per-seed differences, means, SD, n, 95% t interval, verdict improves/worsens only when the interval excludes zero; no combined score. Metric source hashes ignore XML comments (SUMO writes a timestamp in one). Rules 0.2.0 (ADR-021), in `.eligibility` and `.significance` (**exist**):
<ul><li>runs are complete, incomplete or an integrity failure;</li><li>an incomplete candidate is never selectable, but it is compared on window throughput and queues (not mean delay);</li><li>censored queues are listed;</li><li>improvements need Holm's step-down over a family planned in advance.</li></ul> `baseline_validation` (**exists**) holds the observed-versus-simulated comparison rules used by `scripts/validate_ngsim_int2_baseline.py` | Metric values (the only source of numbers in the report) |
| Decision report | `cairoflow_evaluation.report` (**exists**); contract `cairoflow_contracts.report` | `assemble_report(world_model, bundle, reasoning, outcomes, experiment, evaluation) -> DecisionReport`: every number a `ReportNumber` whose first reference yields it (metric, comparison, world-model movement, validated outcome or setting) and whose other references are its evidence (all paired metrics, counted tracks); model text labelled model_generated; published selection rule (every seed complete, no metric worsens, at least one Holm-confirmed improvement, most confirmed improvements, then larger delay reduction), no weighted score. A selection is a recommendation only under a baseline assessment of `validated` (`BaselineAssessment`, `load_baseline_assessment`). `trace_report(...)` resolves every reference, recomputes each comparison from its metrics and re-hashes each SUMO file (A9). `render_markdown`, `write_report` (never overwrite) | `report.json`, `report.md` |
| Pipeline and replay | `cairoflow_evaluation.pipeline` (**exists**), `scripts/replay.py` | `run_pipeline(inputs_path, out_dir, mode)` runs every stage from `pipeline_inputs.json` (clip manifest, SAM cache, calibration spec, geometry, signal CSV, Muse cache, run settings) with `as_of_utc` as every timestamp, refuses a report that does not trace, and writes `pipeline_record.json` (hash of every artifact and every tripinfo). `compare_records(reference, replayed)` lists every difference; the reference must be a replay-mode record (A10) | Run directories and their records |
| Report pages | `apps/api/cairoflow_api` (**exists**, read-only) | `create_app(runs_root, demo_manifest, repo_root)`: `/` (runs with a `pipeline_record.json`, each with its data source), `/runs/<name>/` (report page rendered from report.json: every number with its evidence-kind badge, source and expandable references; model text boxed as model-generated; verdict per metric; candidate headings say accepted or rejected by the validator, and an accepted candidate without verdicts points to the Limitations entry that says why; a Data source section with the record's `data_source` (camera tracks by default; the NGSIM intersection 2 runs name the published trajectories), attribution and notes; review overlay video when there is one; synthetic-data and decision-support banners), `/runs/<name>/report.json`, `/report.md`, `/overlay.mp4`. Run names allow-listed and resolved to direct children of the root; only fixed file names served; all text HTML-escaped; no scripts; strict CSP, `nosniff`, `no-referrer`; tables scroll in place at phone width. `python -m cairoflow_api.app RUNS_ROOT` serves them alone. Starting the simulation pipeline from the API is not built; camera analyses run as jobs | HTTP surface |

Rules:
- Only the two adapters talk to the Meta API, through `httpx`. Model names, base URL and key come from `Settings`. Both adapters are cache-first: `replay` mode never opens a connection and fails loudly on a cache miss.
- Only `cairoflow_evaluation.metrics` produces numbers that appear as results. Muse output can cite evidence ids; it cannot supply metric values.
- Each stage writes immutable artifacts to `experiments/runs/<run_id>/<stage>/` plus a manifest (input hashes, code version, model ids, parameters, seeds). A rerun gets a new `run_id`; nothing is overwritten.
- Persistence is files (JSON, SUMO XML). The product's SQLite index in `var/` is rebuilt from them.

## Testing and reproducibility
| Layer | Strategy | Fixtures |
|---|---|---|
| Unit | Parsing, coordinate transforms, measurements, schema validation, validator rules, metric formulas. Pure functions; no network; SUMO only where needed | Synthetic: generated clips (moving shapes), hand-written observations, small calibration specs with known answers |
| Adapter | `httpx.MockTransport` for request shape, error classification, no retry, scrubbing; parsing tested on recorded streams | Real cached responses from approved smoke runs (git-ignored; tests skip when absent) and synthetic streams built in code |
| Live API | `scripts/api_smoke_test.py` only, one approved case at a time | — |
| Simulation | Deterministic: same inputs and seed give identical tripinfo hashes; paired seeds share demand files; teleports fail the run | `baseline_4way` (synthetic) network |
| End-to-end | `replay` over cached SAM and Muse outputs, from clip manifest to report, offline; compares the report hash with the recorded one | One recorded run |
| Demo | `scripts/fresh_clone_check.sh`: install, suite, every demo URL and the rebuild, on a fresh clone | — |
| Provenance | A test walks every number in a `DecisionReport` back to a `MetricResult` and its SUMO file, or to a world-model value and its frames | — |
| Performance | Record per stage: wall time, SAM latency and frames billed, Muse tokens, SUMO real-time factor. Reported, not optimised, in the MVP | Run manifests |

The demo must not depend on live API availability: replay mode is the primary path for judges, and cached outputs are labelled "cached model output" with the date they were produced.
