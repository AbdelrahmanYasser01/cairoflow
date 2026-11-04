# Data acquisition and licensing register

> This log was kept while the project was built. Some working notes and records it cites are not in
> this repository. Commit hashes and branch names refer to the project's earlier history, and
> M numbers are its milestones.

Every external input to CairoFlow gets one row here **before** it is downloaded. Nothing has been downloaded yet. Researched 2026-10-27 from licence texts and publisher metadata; quotations are short and paraphrases are marked.

Status key: **V** = read in the licence or terms text or the publisher's machine-readable metadata; **I** = our reading of what was verified; **U** = unknown.

## 1. Acquisition specification (what a clip must provide)

| Requirement | Minimum | Preferred | Why |
|---|---|---|---|
| Licence | Permits commercial use, modification, upload to a third-party processing API, and showing frames in a public demo | Permits committing a short excerpt and derived outputs to a public repo | Hackathon submission and ADR-009 replay |
| Scene | One signalised four-arm (or T) intersection | Stop lines and lane markings visible on at least two approaches | Lane-level measurements need lanes |
| Camera | Static, elevated or oblique | High enough to limit occlusion | Homography assumes a fixed camera |
| Duration | 60 s | 120 s covering at least one full signal cycle | Flows and queues per phase |
| Video | ≥ 640x360 | ≥ 720p | SAM mask quality |
| Upload format | Transcoded by us to MP4, H.264, `+faststart`, 10 fps | — | Formats accepted by SAM (MP4/MOV, H.264/H.265/MPEG-4; not VP9/WebM); 10 fps keeps the per-frame bill low |
| Calibration aids | 4 identifiable ground points | Known distances (lane width, crosswalk, published drawings) | Homography control points |
| Signal state | Annotatable by a person (signal heads visible), or published timing | Published timing plans | Signal state is never inferred by a model (sumo-and-geometry.md, rule 6) |
| Vehicle mix | Cars | Cars, buses, trucks, motorcycles | SUMO vehicle types |
| Personal data | Faces and plates not legible at native resolution | — | Privacy; Egypt's Law 151/2020 if Cairo footage is used |

## 2. Candidate register

| Field | A. NGSIM Lankershim Blvd (primary) | B. Wikimedia Commons "Intersection Pie-IX-Sherbrooke" (fallback) | C. Pexels / Pixabay clips (second fallback) | D. Own footage, Cairo (stretch) | E. inD dataset (rejected) | F. UA-DETRAC (rejected) |
|---|---|---|---|---|---|---|
| Source | U.S. DOT FHWA, ITS DataHub. https://data.transportation.gov/Automobiles/Next-Generation-Simulation-NGSIM-Program-Lankershi/uv3e-y54k | Uploader Thomas1313, 2017-07-09, "Own work". https://commons.wikimedia.org/wiki/File:Intersection_Pie-IX-Sherbrooke.webm | Individual contributors, e.g. https://www.pexels.com/video/traffic-flow-in-an-intersection-4791734/ | The team | leveLXData / ika RWTH, https://www.levelxdata.com/ind-dataset | Univ. at Albany, https://arxiv.org/abs/1511.04136 |
| Licence | CC BY-SA 4.0 (V, dataset metadata `uv3e-y54k.json`; trajectories are CC BY-SA 3.0, `8ect-6jqj.json`) | CC BY-SA 4.0 (V, file page) | Pexels Licence / Pixabay Content License (V for summaries; full Pixabay licence not read) | Ours, subject to Egyptian law | Non-commercial, by application (V) | None found (U) |
| Commercial and hackathon use | Allowed (I, CC BY-SA) | Allowed (V, Commons accepts only licences allowing commercial use) | Allowed (V) | Unclear whether "personal" filming covers it (U) | No (V) | U |
| Upload to a cloud AI API | No AI clause in the licence or metadata (V); covered by the reproduce-and-share grant (I) | Same as A | No clause in the licence; ToS bans bulk or ML scraping of the service, not one manual download (I) | Possibly a cross-border transfer of personal data under Law 151/2020 (U) | Conflicts with "no distribution" (I) | U |
| Redistribution of the clip | Allowed with attribution, licence link and change notice; ShareAlike (V) | Same as A | Not as a standalone copy (V); commit frames or overlays only (I) | Ours | No (V) | U |
| Attribution | "U.S. Department of Transportation, Federal Highway Administration (2016). Provided by ITS DataHub through Data.transportation.gov. DOI: 10.21949/1504477" plus https://ops.fhwa.dot.gov/trafficanalysistools/ngsim.htm (V) | Author, title, licence and link (V) | Not required (V); we credit anyway | — | Cite paper | — |
| Derived outputs (masks, boxes, tracks, overlays) | Anything containing pixels is an adaptation and must be CC BY-SA 4.0 (I). Numeric tracks: grey area; publish them under CC BY-SA too (conservative) | Same as A | No ShareAlike or NonCommercial (V) | Ours | Only abstract, irrecoverable derivatives (V) | U |
| Fit | Five rooftop cameras covering three signalised intersections, 2005-06-16, 08:30–09:00, raw and processed video (V). Processed video overlays IDs, so use raw. Single ZIP of about 6.86 GB (V). Resolution, codec and per-clip identifiers U. **Ground-truth trajectories at 0.1 s exist** (V) | 61 s, 1920x1080, VP8 WebM, 78 MB (V). Camera view and signals U until viewed | No clip yet confirmed as a static four-arm view; several are drone or portrait | Ideal story; legal risk | German intersections, drone | 960x540, many road segments |
| Personal data | 2005 rooftop view: faces and plates unlikely legible (I) | U until viewed | Contributors warrant releases; public-scene risk remains (I) | Faces and plates; images are personal data under Law 151/2020 | — | — |
| Verdict | **Primary** | **Fallback** | Second fallback | Stretch only, after legal advice | Rejected | Rejected |

Why NGSIM is primary: it is the only candidate with a verified permissive licence **and** a verified fixed elevated view of signalised intersections, and its published trajectories let us **measure** tracking accuracy instead of asserting it. That turns a perception claim into an evaluated one.

Its costs: the clip is from Los Angeles (2005), not Cairo; the download is one large archive; resolution is unknown and may be low; anything we publish with its pixels is CC BY-SA 4.0.

### Cairo footage (D), what is known
- Personal photography in public spaces needs no permit since a 2022 cabinet decision; heavy equipment and productions do, and filming children or non-consenting adults is restricted. Source: secondary news (https://thenationalnews.com/mena/egypt/2022/07/20/egypt-to-allow-photography-in-public-spaces); the Gazette text was not found.
- Drones need a Ministry of Defense permit under Law 216/2017 (secondary sources). **No drone footage.**
- Law 151/2020 treats images as personal data; its executive regulations' grace period reportedly ends 2026-10-31 (Clyde & Co, secondary). Uploading footage with faces or plates to servers abroad may be a regulated transfer. Unknown for a short research clip.
- Recommendation: do not record or upload Cairo footage for the MVP without legal advice. A roadside or bridge clip with no legible faces or plates could be a later addition.

## 3. Other inputs

| Input | Licence / terms | Status | Notes |
|---|---|---|---|
| `meta-sam-parser` 0.1.0 | SAM License (last updated 2025-11-19) | V (read in the installed package) | Grants use, reproduction and distribution of the SAM Materials; redistribution must include the licence text (§1.b.i); publications must acknowledge SAM (§1.b.ii); compliance with privacy law (§1.b.iii). It governs the parser and SAM Materials, not the hosted API's outputs. We do not vendor the package |
| SAM 3.1 and Muse Spark outputs (hosted API) | Meta Model API ToS §4.1: you own Inputs and Outputs | V | Outputs that contain footage pixels inherit the footage licence |
| Meta's retention and use of uploaded footage | Not read for the hosted SAM API | U | Read the ToS data sections and the privacy terms before the first real-footage upload |
| SUMO 1.28.0 | EPL-2.0 (Eclipse SUMO) | I (well known; not re-read today) | Simulator outputs are ours |
| Synthetic test media | Generated in code (`scripts/verify_env.py`, `scripts/api_smoke_test.py`) | V | No third-party content |

## 4. Storage rules

| Artifact | Location | Git | Rule |
|---|---|---|---|
| Original download | `data/raw/<source_id>/` | Ignored | Keep with its SHA-256 and access date |
| Trimmed and transcoded upload clip | `data/processed/clips/<clip_id>.mp4` | Ignored | Record the exact ffmpeg command in the clip manifest |
| Raw model streams | `cache/` | Ignored (ADR-009), except the one approved Muse reply in `cache/muse/` (ADR-020) | A small sample may be committed only after explicit approval, with attribution and licence notice; see `cache/muse/NOTICE.md` |
| Tracks, world model, experiment outputs | `experiments/runs/<run_id>/` | Ignored; curated manifests may be committed | Anything containing pixels carries the footage licence |
| Demo video and screenshots | Outside the repo until submission | — | Attribution text on screen and in the description |

## 5. Before any download (user decisions)

1. ~~Accept NGSIM Lankershim as the MVP clip source, with "not Cairo" stated plainly in the demo, or choose another source.~~ **Decided 2026-10-27: NGSIM Lankershim is the MVP clip source.** Not downloaded.
2. Approve downloading the Lankershim video archive (about 6.86 GB; it cannot be fetched partially). **2026-10-27: the user checked the licence page; the download waits for approval of the plan in §6.**
3. Accept CC BY-SA 4.0 on every published artifact that contains its pixels (demo video, overlays, any committed sample frames). Needed before publishing, not before downloading.
4. ~~Confirm licence, attribution text and file list~~: archive name, size and licence verified from the portal's metadata API (§6). The archive's inner file list and the camera layout are known only after download and from the metadata PDF.
5. Read Meta's terms on retention and use of uploaded content before the first real-footage SAM request.

After download, add to this file: archive SHA-256, file list, chosen camera and time window, clip SHA-256, trim and transcode command, and the geometry sources used .

## 6. NGSIM Lankershim acquisition plan (2026-10-27, not yet executed)

Verified 2026-10-27 from `https://data.transportation.gov/api/views/<id>.json` and the dataset pages (metadata only; nothing fetched):

| # | File | Size | Dataset, licence | URL | Why |
|---|---|---|---|---|---|
| 1 | `NGSIM Lankershim Videos.zip` | 6,860,224,646 bytes (V) | `uv3e-y54k`, CC BY-SA 4.0 (V) | `https://data.transportation.gov/download/uv3e-y54k/application%2Fx-zip-compressed` (V, the page's Download link) | Raw video, 5 cameras (1 southernmost to 5 northernmost), 08:30–08:45 and 08:45–09:00, 2005-06-16 (V, description); raw and processed video (processed has IDs overlaid; not used) |
| 2 | `Lankershim_Blvd_Metadata_Documentation.pdf` | U | `8ect-6jqj` attachment, CC BY-SA 3.0 (V) | Socrata attachment URL for asset `38de7132-edbb-44ea-b9c5-c53b33317ee5` (pattern U until the first request) | Camera layout, study area, coordinate system, any signal timing |
| 3 | `Lankershim-Boulevard-LosAngeles-CA.zip` | U | `8ect-6jqj` attachment, CC BY-SA 3.0 (V) | asset `62273322-a1e9-48fc-8692-b4c576bafc20` (pattern U) | Site supporting data; expected to hold trajectories and site documents (contents U) |

Not fetched: the processed-video attachment (`lankershim-boulevard-ProcessedVideo-0830am-0845am-Cam45.zip`), other sites, and the 11.8M-row trajectory table (Lankershim rows can be filtered later with `location = 'lankershim'` if file 3 lacks them).

Steps, each stopping on any failure:
1. Files 2 and 3 first (small; sizes checked from the response headers and stopped above 2 GB), into `data/raw/ngsim-lankershim/docs/`. Read the PDF: which camera sees which intersection, the frame rate and the resolution, and whether signal timing was published (which would make the timeline `published` rather than `observed-annotated`).
2. File 1 into `data/raw/ngsim-lankershim/` with `curl --fail --location -C -` to a `.part` file; accept only if the size is exactly 6,860,224,646 bytes; record SHA-256 and access date here. Expected 20–50 minutes at 20–50 Mbit/s; WSL has 952 GB free.
3. List the archive (`unzip -l`) into this file; extract only the raw videos of one period (no network).
4. One still per camera (FFmpeg, local) to choose the camera that shows one whole signalised intersection with its stop lines; `ffprobe` resolution, frame rate and codec.
5. Ingest with `cairoflow_perception.ingest.ingest_clip` (bit-exact 10 fps H.264, SHA-256, frame timestamps) twice: a 10 s excerpt for the first SAM pass (100 frames, about $0.02 per class prompt at the SAM video rate) and a 5-minute excerpt for the world model (3,000 frames, about $0.60 per class prompt), covering at least two signal cycles. Neither is sent to SAM without a separate spend approval.
6. Record the chosen camera, window, trim commands and clip SHA-256 here; draw control points, lanes, gates and the signal annotation for that clip.

## 7. Verification results, step 1 (2026-10-27)

Authorised by the user: files 2 and 3 only; the 6.86 GB archive was not downloaded, no footage was sent anywhere, no licence was accepted on the user's behalf.

Downloaded to `data/raw/ngsim-lankershim/docs/` (Git-ignored), accessed 2026-10-27T17:29:33Z, sizes equal to the server's `Content-Length`:

| File | URL | Bytes | SHA-256 |
|---|---|---|---|
| `Lankershim_Blvd_Metadata_Documentation.pdf` (17 pages) | `https://data.transportation.gov/api/views/8ect-6jqj/files/38de7132-edbb-44ea-b9c5-c53b33317ee5?download=true&filename=Lankershim_Blvd_Metadata_Documentation.pdf` | 491,613 | `1d4673badd8249767b135938763deb77109db2119262616749db376dc0e1d8b3` |
| `Lankershim-Boulevard-LosAngeles-CA.zip` | `https://data.transportation.gov/api/views/8ect-6jqj/files/62273322-a1e9-48fc-8692-b4c576bafc20?download=true&filename=Lankershim-Boulevard-LosAngeles-CA.zip` | 80,179,421 | `8c14c09b45993d4aa18bcada587757104d96ae36ba270d4e99777a104652b2b9` |

**The site ZIP is truncated at the source.** It has no central directory. Walking its local headers: one stored entry, `lankershim-boulevard-aerial-ortho-photos.zip`, declared 122,661,759 bytes with 80,179,347 present; inside it, `aerial-ortho-photos/LA-UniversalCity.tfw` (89 bytes, complete) and `LA-UniversalCity.tif` (845,098,820 bytes uncompressed, 65% present). Nothing else of the package the metadata describes is in the file (CAD drawings, GIS files including `camera-coverage.shp`, signal timing, trajectories, detector data, reports). Since the served length equals the file length, this is the published file, not a failed transfer.

Licence evidence per asset (from `https://data.transportation.gov/api/views/<id>.json` and `.../api/views/metadata/v1/<id>`, both `provenance: OFFICIAL`):

| Asset | Record | Licence as published | Notes |
|---|---|---|---|
| Video archive `NGSIM Lankershim Videos.zip` | `uv3e-y54k`, created 2021-10-28, metadata updated 2026-10-07 | CC BY-SA 4.0 International (`licenseId: CC_40_BY_SA`, both endpoints) | The record's licence logo is `cc30bysa.png` (inconsistent with its own name). Its recommended citation uses DOI `10.21949/1504477`, which resolves to `8ect-6jqj`, a CC BY-SA 3.0 record; the archive has no DOI of its own. No separate use or redistribution terms on the record (`Rights: public`). No checksum published. |
| Trajectory table and its attachments, incl. files 2 and 3 | `8ect-6jqj`, created 2017-09-01, data updated 2018-08-27 | CC BY-SA 3.0 Unported (both endpoints) | Attachments carry no licence of their own on the portal; the dataset licence is the only one shown. |
| Metadata PDF (2016) | — | States no licence | "Access Constraints": registration on the former USDOT RDE portal with "agreement to terms of use" (terms not seen; the RDE has since been replaced by ITS DataHub). |

The federal catalogue cross-check (catalog.data.gov) could not be completed: its search API returned "Not Found" and its search page redirected.

From the metadata PDF: five cameras on a 36-storey building by the US-101/Lankershim interchange; about 500 m of arterial, three to four lanes, "complete coverage of three signalized intersections"; intersections 1 to 4 numbered south to north; trajectories every 0.1 s with direction (EB/NB/WB/SB), movement (TH/LT/RT), lane, and origin/destination zones; "primarily congested conditions"; processed video is XviD AVI; aerial ortho photos 0.25 ft per pixel, relative accuracy 2.5 ft, CA State Plane Zone 5 (NAD83, US survey feet); signal timing published as 10 TXT/JPG/PDF files. Which camera sees which intersection is not in the text; it is in `camera-coverage.shp`, missing from the truncated ZIP.

## 8. Verification results, step 2: archive directory (2026-10-27)

Authorised by the user: the last 1 MB of the video archive only. Request: `curl -L --max-filesize 2097152 -r -1048576` on `https://data.transportation.gov/download/uv3e-y54k/application%2Fx-zip-compressed`, which redirects (302) to `https://data.transportation.gov/api/views/uv3e-y54k/files/27fbaaac-d4b0-4b04-aacd-253d88f93ea8?filename=NGSIM+Lankershim+Videos.zip`; answered `206 Partial Content`, `Content-Range: bytes 6859176070-6860224645/6860224646`, no content encoding. Tail saved as `data/raw/ngsim-lankershim/archive-tail/tail.bin` (SHA-256 `d6062b2c86004a77c8fc0e32c19bc94fc1c8ca406de10728aecc1a6b3769a28f`).

**The archive's ZIP64 directory is complete and consistent**: end record at the last byte, ZIP64 records agree, 22 entries, entries tile the file up to the directory (header gaps 86–148 bytes). Every member has a CRC32, so any downloaded member can be verified exactly. The server supports byte ranges, so single members can be fetched.

| Member (`NGSIM Lankershim Videos/…`) | Archive offset | Compressed | Uncompressed | CRC32 |
|---|---|---|---|---|
| `lankershim-camera1-0830am-0845am.avi` | 701,031,259 | 448,676,964 | 448,657,408 | `9b42054a` |
| `lankershim-camera2-0830am-0845am.avi` | 2,449,745,454 | 449,183,196 | 449,163,264 | `f8be5340` |
| `lankershim-camera3-0830am-0845am.avi` | 4,359,418,326 | 449,192,273 | 449,173,504 | `2821bb9a` |
| `lankershim-camera4-0830am-0845am.avi` | 5,154,538,451 | 449,092,326 | 449,073,152 | `0a2ed3ec` |
| `lankershim-camera5-0830am-0845am.avi` | 5,875,379,033 | 448,649,193 | 448,630,784 | `1d8f53cf` |
| `lankershim-camera1-0845am-0900am.avi` | 1,475,418,405 | 272,457,841 | 272,382,695 | `03c52a50` |
| `lankershim-camera2-0845am-0900am.avi` | 3,554,472,868 | 104,358,470 | 104,334,543 | `528d7c44` |
| `lankershim-camera3-0845am-0900am.avi` | 4,901,510,011 | 8,864,029 | 8,869,191 | `c2b801d5` |
| `lankershim-camera4-0845am-0900am.avi` | 5,610,377,725 | 261,356,701 | 261,284,939 | `1fcdc3bb` |
| `lankershim-camera5-0845am-0900am.avi` | 6,592,514,568 | 267,289,679 | 267,216,114 | `f3e7bdda` |
| 10 `…-processed.avi` (IDs overlaid; not used) | — | 3.6 MB to 702 MB | — | listed in `tail.bin` |
| `Lankershim_Blvd_metadata_with_disclaimer.pdf` | 6,859,804,385 | 416,941 | 436,182 | `11b99ade` |

All five 08:30–08:45 raw files are about 449 MB (consistent with full 15-minute recordings); the 08:45–09:00 raw files vary from 8.9 MB to 272 MB, so some are much shorter or partial. Use 08:30–08:45.

The in-archive PDF lay inside the fetched tail; it was inflated from it (CRC32 matched; SHA-256 `498b6055086454120ef5e8ec0b8a48978bae267cde9db809a807a0c61af46ebd`). Its text is an ITS JPO "Disclaimer" (last updated 01/04/2022): data provided "AS IS" and "AS AVAILABLE", no warranty, no liability; video files public since November 2021; contacts: the FHWA NGSIM page and James Colyar (FHWA). It states no licence and no use or redistribution restriction (rough text extraction; a few clauses were not recovered).

## 9. Verification results, step 3: camera preview (2026-10-27)

Authorised by the user: the first 5 MB of each of the five 08:30–08:45 raw camera members, nothing else. Five byte-range requests (5,242,880 bytes each, 26,214,400 bytes in total) starting at the member offsets in §8; all answered `206` with the exact range. Each prefix was inflated locally (about 5.25 MB of AVI each) and one frame decoded at t = 5 s. Files in `data/raw/ngsim-lankershim/camera-preview/` (Git-ignored; the stills contain footage pixels and fall under CC BY-SA 4.0).

All five streams: AVI, MPEG-4 Part 2 (XviD), **640x480**, yuv420p, 10000000/999999 fps (about 10 fps).

| Camera | View (one still each) | Complete signalised intersection |
|---|---|---|
| 1 (southernmost) | Lankershim across the frame; a partial side-street junction at the lower left; near-vertical view | No (mainly mid-block) |
| **2** | **A four-arm intersection in the centre: Lankershim left to right, the cross street top to bottom; crosswalks, stop lines and lane arrows visible on the approaches; near-vertical view** | **Yes** |
| 3 | Mid-block segment with a left-turn bay; a partial side junction at the bottom | No |
| 4 | A large junction cut off by the left edge; more oblique | No (incomplete in frame) |
| 5 (northernmost) | A skewed intersection cut off at the right; strongly oblique and distant | No |

Choice: **camera 2, 08:30–08:45** (`lankershim-camera2-0830am-0845am.avi`, archive offset 2,449,745,454, 449,183,196 bytes compressed, CRC32 `f8be5340`). Which named intersection it is has not been established (needs the trajectory `Int_ID` or the missing `camera-coverage.shp`).

Limits seen in the still, for the traffic analysis:
- 640x480 from a rooftop: vehicles are roughly 20–30 px long, small for segmentation.
- Visible approach lengths are short. Rough estimate from pixel counts with an assumed lane width (not a calibration): about 20 m to 55 m depending on the arm, so longer queues leave the frame. Cameras 1 and 3 cover the adjacent Lankershim segments.
- From almost directly above, signal lamps are probably not readable; signal states may have to come from the published timing (missing from the truncated package) or be labelled `assumed`.
- The bottom of the frame shows the window frame and glass reflections; exclude that band.
- A near-vertical view keeps the ground close to planar in the image, which suits the homography.

## 10. Camera 2 video downloaded and verified (2026-10-27)

Authorised by the user: the camera 2 08:30–08:45 raw member only; no ingest, upload or API call.

Byte range `2449745454-2898928787` of the archive (449,183,334 bytes: the member's 138-byte local header plus 449,183,196 compressed bytes). The first request (17:45:04Z) ended with "connection reset by peer" after 203,984,293 bytes; the remaining range `2653729747-2898928787` was requested at 17:49:56Z and appended. Both replies were `206` with the exact ranges asked for.

| File (`data/raw/ngsim-lankershim/camera2/`, Git-ignored) | Bytes | SHA-256 |
|---|---|---|
| `camera2-0830.member.range` (the downloaded range) | 449,183,334 | `11f84d3821578b21e0a8a83a1d1971f1edac5dd74629100c1f71a83eb818ae2a` |
| `lankershim-camera2-0830am-0845am.avi` (inflated) | 449,163,264 | `04ef7435be3a107e7321900c384bb1ee7703ce753a122559cd2937fef341f530` |

Verification against the archive's central directory: compressed size, uncompressed size and **CRC32 `f8be5340` all match**. `ffprobe`: AVI, MPEG-4 Part 2 (XviD), 640x480, 10000000/999999 fps, **11,220 frames, 1,121.999 s (18.7 minutes)**, longer than the 15 minutes the "0830am-0845am" name suggests; which part matches the published trajectory period is to be checked against their timestamps. Licence: CC BY-SA 4.0 (record `uv3e-y54k`, §7). Not ingested, not uploaded.

## 11. Camera 2 excerpts ingested (2026-10-27, local only)

Licence record `data/licences/ngsim-lankershim-videos.json` (committed; CC BY-SA 4.0, attribution from the record's recommended citation). Command, from the source in §10:

```bash
uv run --frozen python scripts/ingest_clip.py data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi --licence data/licences/ngsim-lankershim-videos.json --out data/processed/clips/<name> --start 60 --duration <10|300>
```

| Clip directory (`data/processed/clips/`, Git-ignored) | Window in the source | Frames | clip_id | Output SHA-256 |
|---|---|---|---|---|
| `ngsim-lankershim-cam2-0830-t060-d010` | 60–70 s | 100 | `4a06259854300b67` | `4a06259854300b67fe4197593fa48367bc84dd4f939699a553a9be84e9939761` |
| `ngsim-lankershim-cam2-0830-t060-d300` | 60–360 s | 3,000 | `fd74a18fdb99bc73` | `fd74a18fdb99bc73cb0a8e8675d286bc55b54dd5c73f2c581e23ac40d64f26f0` |

Both: 640x480, 10 fps, H.264 (`-preset medium -crf 18`, bit-exact flags; the full command is in each `source_clip.json`), timestamps 0.0 to 9.9 s and 0.0 to 299.9 s. Checks: both manifests re-verify against their clips and name the §10 video (`04ef7435…`) as source; a second ingest of the 10 s excerpt was byte-identical; the first frames of the two clips are identical (PSNR infinite), so the 10 s clip is the start of the 5-minute clip. The start at 60 s was chosen after stills at 0, 60 and 360 s showed a steady, identical view (a queue is visible on the right-hand approach at 360 s). Not uploaded; no API called. The window frame and reflections at the bottom of the image remain to be masked.

## 12. Camera 2 geometry, provisional (2026-10-27, local only)

Drawn on frame 0 of the excerpts by `scripts/draw_ngsim_cam2_geometry.py`; written to `data/geometry/ngsim-lankershim-cam2/{d010,d300}/{calibration,geometry}.json` (one pair per clip id, identical except for the clip id); overlay check in `data/processed/geometry/ngsim-cam2/frame0_drawing.png`.

- Image-side, read from the frame: 8 control features (stop-line ends), 15 approach lanes (E 5, W 4, N 3, S 3), stop and exit lines for 4 arms.
- **Ground metres are synthetic**: image axes as ground axes, arm labels N/E/S/W following the image (top/right/bottom/left), not the compass; 4.5 px/m from an assumed 3.66 m lane; vertical view assumed. Allowed turns assumed (arrows unreadable).
- **No measured source available locally**: the aerial ortho photo is truncated and its TIFF directory (at byte 844,968,296) is in the missing part, so it cannot be decoded.
- Calibration result: **not accepted** ("no held-out check points, so accuracy is unknown"), by design; the world model refuses it unless explicitly overridden.
- To replace: see §13 for the trajectory-based plan; or measured coordinates for the same 8 features (complete NGSIM ortho/CAD from ITS JPO, or NGSIM trajectories matched to vehicles), plus independent check points; the true compass bearing; the turn markings; the bottom arm's middle lane (a bus or rail lane is visible).

## 13. Plan: measured control points from the NGSIM trajectories (2026-10-27, not executed)

Source: table `8ect-6jqj`, CC BY-SA 3.0 Unported (§7), rows filtered with SoQL on `location` (Lankershim value to be read from the data, not assumed) via `https://data.transportation.gov/resource/8ect-6jqj.csv`. Useful columns: `global_x`, `global_y` (front centre, CA State Plane Zone 5, NAD83, US survey feet), `global_time` (epoch ms), `frame_id` (0.1 s), `vehicle_id`, `v_length`, `v_width`, `lane_id`, `int_id` (1-4, south to north), `section_id`, `direction`, `movement`.

1. **Size first (one-number query, needs approval):** `$select=count(*)` for the Lankershim rows, then for 08:30-08:45. Download only that period and only the columns above, paged with `$limit`/`$offset`; stop if larger than an agreed bound. Record row count and SHA-256.
2. **Find camera 2's intersection:** plot `global_x/y` by `int_id`; match lane counts (E 5, W 4, N 3, S 3) and arm layout to §12. Camera numbering suggests intersection 2, to be confirmed.
3. **Ground frame:** convert US survey feet to metres (1200/3937 m per ft); origin at the intersection centre; x east, y north on the State Plane grid (close to true north here). This also gives the true compass labels for the arms.
4. **Fit points without time sync:** stop lines from where the first queued vehicles' fronts stop, per lane; lane centrelines from vehicle centres (front centre minus half `v_length` along the heading) per `lane_id`. Stop line x lane centreline gives points that can be found in the image (§12 features).
5. **Check points with time sync:** the video's start time is unknown. Estimate the offset by matching per-approach stop-line crossing sequences (first discharges after green) between the trajectories and the video; vehicle centres clicked by hand in a few frames (no SAM needed) at synced times become independent check points. Report the offset and its residual.
6. **Fit** with step 4 points, **check** with step 5 points (`fit_homography`): only then can the calibration be accepted. Use box centres, not bottom-centres, as contact points for this near-vertical view (a perception setting to change).

Caveats: NGSIM trajectories are model-derived from video and contain noise; positions are of the front centre; the period labels may not match the 18.7-minute video exactly. Licence: anything built from the trajectories carries CC BY-SA 3.0; BY-SA 3.0 permits adaptations under a later version with the same elements, so a combined output could be CC BY-SA 4.0 (to confirm before publishing; the user decides).

Step 1 result (2026-10-27, count-only queries on `https://data.transportation.gov/resource/8ect-6jqj.json`; no rows downloaded):

| Query | Result |
|---|---|
| Rows per `location` | `i-80` 4,566,387; **`lankershim` 1,607,319**; `peachtree` 873,887; `us-101` 4,802,933 |
| `lankershim`, `global_time` in [1118935800000, 1118936700000) (08:30-08:45 PDT) | **616,749 rows**, 1,125 distinct `vehicle_id` (ids may repeat across vehicles, per the column description), times 08:30:00.0 to 08:44:59.9 |
| `lankershim`, all rows | `global_time` 1118935680200 to 1118937747000 = 08:28:00.2 to 09:02:27.0 PDT (34.5 minutes) |

The exact 08:30:00.0 start confirms `global_time` is epoch milliseconds aligned to Pacific Daylight Time. Download size for 08:30-08:45 with about 12 columns: roughly 40-90 MB as CSV (estimate, not measured). Hypothesis for the time sync, to be tested: the 18.7-minute video may start before 08:30, since the trajectories start at 08:28:00.

Step 1b result (2026-10-27T18:40:21Z, authorised): `data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv` (Git-ignored), **616,749 rows** (matches the count), **54,219,834 bytes**, SHA-256 `c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e`. Columns: `vehicle_id, frame_id, global_time, global_x, global_y, v_length, v_width, lane_id, int_id, section_id, direction, movement`. Query: `8ect-6jqj.csv` with `$where=location='lankershim' AND global_time >= 1118935800000 AND global_time < 1118936700000`, `$order=global_time,vehicle_id`, pages of 50,000 (one connection reset on the first attempt; retried). Licence CC BY-SA 3.0 Unported.

Steps 2–4 executed on 2026-10-27; results in §14. Steps 5–6 (time sync, check points) not started.

## 14. Camera 2 identified; trajectories audited; time-independent correspondences (2026-10-27, local only)

No download, upload or model API call. Only network use: the EPSG registry (epsg.io `2229.wkt`, `2227.wkt`) for projection parameters and three OpenStreetMap Nominatim reverse look-ups (§14.3). Reproduce:

```bash
uv run --frozen python scripts/audit_ngsim_trajectories.py
uv run --frozen python scripts/analyse_ngsim_cam2.py --geometry-out data/geometry/ngsim-lankershim-cam2/v2
```

Both check the input SHA-256 values and refuse to overwrite existing output. A second, independent run of the analysis gave a byte-identical `analysis.json` (SHA-256 `bd611a813576f473ec513ad073cc92ae65ee18a5aa3a82aac8c5d7792efd3793`) and `background.png` (`4a1668e4…5e10`), using OpenCV 5.0.0 and numpy 2.5.4. The analysis takes about 4 minutes.

Outputs:
- `data/processed/ngsim-trajectories/integrity.json` and `data/processed/ngsim-cam2-analysis/`: `analysis.json` and figures `registration_overlay.png`, `paint_check_overlay.png`, `v1_vs_v2_points.png` and `background.png`. These are Git-ignored. The figures contain CC BY-SA 4.0 video pixels and CC BY-SA 3.0 trajectory data, and their captions carry both attributions.
- `data/geometry/ngsim-lankershim-cam2/v2/control_points.json`: the A/B/C correspondence record. It is committed and contains no pixels; values derived from the trajectories carry CC BY-SA 3.0 with attribution to U.S. DOT FHWA NGSIM (`8ect-6jqj`). No calibration file is written (§14.5).
- Code: `cairoflow_perception.ngsim` (loading, projection, kinematics, lane statistics) and `cairoflow_perception.scene` (background, activity, ECC registration, paint profiles), both with tests.

Labels used below: **observed** (seen in the image or counted in the data), **derived** (computed by a stated procedure), **hypothesis** (not established).

### 14.1 Dataset integrity (`scripts/audit_ngsim_trajectories.py`)

**23 PASS, 3 NEEDS INVESTIGATION, 1 NOT VERIFIABLE LOCALLY, 0 FAIL.** An independent rerun reproduced every status.

The checks that pass:
- **File:** existence, byte size and SHA-256 (against both recorded values).
- **Format:** LF line endings with a final newline; 616,749 data rows, equal to the server count; 12 quoted fields per row; the exact 12-column header.
- **Values:** strict int/float parsing, with no empty, NaN or inf values. Vehicle dimensions are 8–64 ft long and 4–8.5 ft wide.
- **Time:** times run 08:30:00.0–08:44:59.9 PDT, all multiples of 100 ms. `frame_id = (global_time − 1118935800000)/100 + 1201` holds on every row, with 9,000 contiguous frames.
- **Codes:** int_id, section_id, direction and movement are all in the codebook.
- **Vehicles:** 1,125 distinct `vehicle_id`, equal to the server count. Each id is one contiguous trajectory with constant dimensions, so no id is reused in this window.
- **Duplicates and order:** no exact duplicate rows and no repeated `(global_time, vehicle_id)` keys. The file is strictly increasing on that key.
- **Continuity:** 615,624 of 615,624 same-id steps are exactly one frame.
- **Pages:** all 12 page boundaries are clean (described below).

Pagination:
- **Ordering:** `(global_time, vehicle_id)` is a unique total order for this data, so the server's sort had no ties to break differently between page requests.
- **Duplication** between pages is excluded locally.
- **Omission** is not detected. Any row lost at a boundary would fall in that frame with an id between the two boundary ids. No such vehicle exists in the neighbouring frames, and the file has no frame gaps. Each of pages 1–12 must have returned exactly 50,000 rows.
- **Not provable locally:** row-for-row equality with the server (no per-page fingerprint was kept), the stability of the server data between requests, and the `location` filter (that column was not downloaded). A matching row count alone would not prove completeness.

NEEDS INVESTIGATION:
- **`lane_id` 0** (55,087 rows), which is not in the codebook text. 54,305 of these rows are at an intersection; the cross-street rows at junctions also carry lane 0 (§14.3). Our reading is that 0 means "no lane" (**hypothesis**).
- **`int_id` = `section_id` = 0** (13,686 rows, 729 vehicles, all through movements; 5,114 EB and 6,100 WB). These are probably cross-street positions outside both a Lankershim section and an intersection zone (**hypothesis**).
- **Five speed spikes above 120 ft/s.** The worst is vehicle 648 at frames 4854–4855, at 261 ft/s. These look like position noise; the rows are kept as published.

Not a failed check, but it affects every count: `direction` changes along the trajectory for 474 of 1,125 vehicles, and `movement` for 640. The codes describe the current row, not a fixed route, so `movement == 1` does not select through vehicles. Per-vehicle counts therefore use each vehicle's first row.

NOT VERIFIABLE LOCALLY: completeness against the server.

### 14.2 Coordinates (verified)

`global_x` and `global_y` are NAD83 **California zone 5, EPSG:2229, in US survey feet**. The metadata PDF's column table says "State Plane III", but its text says zone 5 twice. We converted the data's extremes to latitude and longitude with the EPSG parameters (Lambert conformal conic on GRS80, `cairoflow_perception.ngsim`):

| CRS | Latitude | Longitude | Inside the metadata box (W −118.363, E −118.360, S 34.137, N 34.143)? |
|---|---|---|---|
| Zone 5, EPSG:2229 | 34.1380–34.1422 | −118.3628 to −118.3606 | **yes** |
| Zone 3, EPSG:2227 | 37.1376–37.1418 | −120.8766 to −120.8744 | no (central California) |

- **Units:** the median `v_length` is 15.8. That is a car in feet (4.8 m); in metres it would be a bus.
- **Grid versus true north:** grid convergence at the site is −0.206°, and the grid scale factor is 1.0000 to four decimals. Grid north is used and the difference is stated, not corrected.
- **Local frame (`ORIGIN_FT`):** metres from State Plane (6452083, 1873176) ft, the rounded centroid of the `int_id` 2 rows. It is a reference point, not a surveyed junction centre. x is grid east, y is grid north, and 1 US survey foot = 1200/3937 m.
- **Positions:** published positions are the vehicle's front centre (metadata). §14.6 questions how they behave along the road.

### 14.3 Which intersection camera 2 shows: `int_id` 2 (high confidence)

Per intersection, from the trajectories. Counts are **observed**. Bearings are **derived**: the circular mean of headings within 60 m of rows coded through and moving faster than 3 m/s. Cross-street entries count vehicles whose first row is nearest that intersection and coded EB or WB.

| int_id | Lankershim bearing (grid) | SB approach lane_ids | NB approach lane_ids | NB exit lane_ids | Cross-street entries EB / WB | Shape |
|---|---|---|---|---|---|---|
| 1 | 25.7° | 3, 2, 1 | 2 only (study-area edge) | 1–4 | 0 / 6 | Partial, at the south boundary |
| **2** | **25.8°** | **31, 3, 2, 1, 12, 11** | **11, 1, 2, 3, 4** | **1–3** | **66 / 54** | **Four-arm junction**; most cross-street traffic turns |
| 3 | 24.4° | 4, 3, 2, 1, 11 | 11, 1, 2, 3, 31 | 1–4 | 7 / 8 | Small cross flows |
| 4 | 0.1° | 4, 3, 2, 1, 11 | 11, 1, 2, 3, 4 | 1–4 | 4 / 10 | Small cross flows; Lankershim bends here |

Evidence, from strongest:

1. **Lane structure (observed in the image; matched to published lane_ids).** Read twice, independently, on medians of stills and on the full-video background, with no registration involved:
   - The image's right arm has 6 approach lanes: lines at v ≈ 119, 134, 150, 167 and 183, the yellow at about 200, and the kerb at about 98. The outer one is a right-turn pocket with an arrow. There are also 3 exit lanes.
   - Only int 2's SB approach has 6 lanes (11, 12, 1, 2, 3, 31: two left bays and a right bay), and only int 2 has a 3-lane NB exit. Ints 3 and 4 have 5-lane SB approaches and 4-lane NB exits.
   - The left arm (5 approach lanes beside a 2.6 m painted median, plus 3 exits) agrees with int 2, but on its own does not tell int 2 from int 3.
   - The pocket arrow curves toward the driver's right, so the image is not mirrored.
2. **Registration, with no time sync (derived).** A 15-minute raster of vehicle footprints (body rectangles behind each published position) was registered to a video activity map: the share of frames, sampled every 0.5 s over the whole 18.7 min, that differ from the median background. OpenCV ECC ran affine then homography. The hypotheses covered every intersection, four rotations of Lankershim against the image, seeds of 4.4, 5.2 and 6.2 px/m (and 5.2 px/m mirrored), with the junction seeded near pixel (258, 195): 64 in all. A warp counts as plausible if it converged, is un-mirrored, is within 2× of the seed scale and has anisotropy below 1.5.
   - **Best:** int 2 with the north leg on the image right, un-mirrored, **r = 0.906**.
   - **Same junction:** rotated 180° scores 0.750; rotated 90° or 270° scores 0.39 or 0.33; mirrored scores 0.56 at best.
   - **Best other intersection:** 0.748 (int 1, mirrored, a degenerate 70× warp). With a plausible warp: 0.667 (int 4, 6.2 px/m seed).
3. **Cross-street traffic (observed).** Int 2 has 66 + 54 cross-street entries, about 8× any other intersection, consistent with the camera's full four-arm junction.
4. **Paint consistency (derived; checks the registration, not the identity).** Lane lines in the background, which the registration did not use, against the lane boundaries the trajectories imply (midpoints of per-lane median lateral positions):
   - **North arm:** 8 of 8, rms 0.09 m, max 0.18 m.
   - **South arm:** 6 of 6, rms 0.17 m. A seventh boundary lies inside the painted median and is excluded.

   This confirms the registration to lane level across the road. It does not discriminate, though: the independent review of 2026-10-27 (a scratch analysis, not part of the script) found that int 3's or int 4's lane pattern also matches every line once shifted and scaled up to ±15%. Only the north-arm rms (0.08 m against 0.28 and 0.40 m) separates them.
5. **Name (secondary; present-day map, not 2005).** OpenStreetMap Nominatim (ODbL), queried 2026-10-27 at the int-2 centroid (34.139033, −118.362142) and 25 m along each cross leg, returns:
   - Lankershim Boulevard, with southbound turn lanes `left|left||||`;
   - **Campo de Cahuenga** for the west leg;
   - **Universal Hollywood Drive** for the east leg.

   The metadata's list of study-area intersections includes "Lankershim Blvd and Universal Hollywood …". The dataset's own int_id-to-name table (the data analysis report) is not available, so the name is a **supported hypothesis**, not a verified mapping.

**Orientation (derived)**, for converting the provisional labels, which followed the image:

| Image position | Leg | Grid bearing of the image axis (registration) |
|---|---|---|
| Right | North-north-east leg of Lankershim (SB approach on the top half) | +u ≈ 26° |
| Left | South-south-west leg | ≈ 206° |
| Top | West-north-west leg (Campo de Cahuenga) | −v ≈ 299° |
| Bottom | East-south-east leg (Universal Hollywood Dr) | +v ≈ 119° |

Traffic is right-hand and the image is un-mirrored. The usable view, down to v = 410, covers Lankershim from about 36 m south to 70 m north of the reference point. The camera holds still to within 0.1 px between the first 2 minutes, the middle and the last 2 minutes (review), so one geometry serves both excerpts.

Confidence is high (≥ 95%) that camera 2 shows `int_id` 2:
- The right-arm lane structure matches int 2 only.
- The registration margin holds across seeds and rotations.
- Cross-street entries are about 8× any other intersection.
- Camera numbering (1 = southernmost) is consistent.

The identification would only be undone by evidence that the trajectories near int 2 came from another camera. That evidence would be the missing `camera-coverage.shp`, or vehicle-by-vehicle matching after a time sync.

### 14.4 Audit of the provisional geometry v1 (commit `7041e67`; kept unchanged)

We compared v1 with two independent sources:
- an unanchored reading of medians of three stills, with intensity profiles, made before opening the v1 script;
- our own profiles on the full-video median background.

The visual comparison is `v1_vs_v2_points.png`.

| v1 item | Finding | Correction (pixels, ± uncertainty) |
|---|---|---|
| `E_stop_top` (372, 118) | On the right-arm limit line but not its end. It sits at the boundary between the right-turn pocket and lane 1. | Line end at the north kerb (370.2, 96) ±2 |
| `E_stop_bottom` (372, 198) | At the yellow centreline, 3.5 px west of the paint. The line continues across the exit half to the kerb. | (375.4, 198.5) ±2; south end (377.5, 252) ±2.5 |
| `W_stop_top` (147, 200) | On the line, 14 px from its end, in mid-lane | (149.0, 186) ±1.5 at the painted-median nose |
| `W_stop_bottom` (147, 262) | 5 px east of the line and 24 px short of its end | (140.5, 286) ±2.5 |
| `N_stop_left` (205, 100) | **Wrong line.** It is near the inner crosswalk line; the top-arm limit line is the outer one, at v ≈ 60–66. | (208, 66) ±3 |
| `N_exit_right` (330, 95) | **On bare asphalt**, 8.5 px from the nearest paint | Nearest feature: crosswalk corner (338.0, 85.0) ±1 |
| `S_stop_left` (265, 290) | On the zebra lower edge but not an end | (275, 289) ±3 or (293.7, 285.6) ±2 |
| `S_stop_right` (330, 290) | 8.7 px south of the zebra edge | (330, 281.5) ±2.5 |

| Arm | Lane-count finding |
|---|---|
| Right | 5 lanes plus an omitted right-turn pocket (6 approach lanes). The v1 boundaries match within 0.5 px near the junction. |
| Left | **5 approach lanes, not 4.** The v1 polygons are 15–16 px wide against a true 19–21 px, so they straddle the real lanes. |
| Top | 3 lanes. v1 ignores the arm's 3.6° lean, and its N3 polygon crosses the double yellow. |
| Bottom | 3 lanes. v1 polygons are shifted 6–16 px west of the real lanes (which lean 4.7°). S3 covers the planter and half of a strip that holds a static blue object; whether that is a parked bus or a painted bus pad is not determined. |

Model-level findings:
- **There are no separate stop bars.** Every limit line is a crosswalk edge (on the bottom arm, the zebra's lower edge).
- **The uniform 4.5 px/m vertical-view model is wrong.** Lane widths run 16 px on the right arm and 19–21 px on the left and bottom arms. The registration's local scale runs from 5.25 to 7.04 px/m (review), about 30% across the frame. Trajectory lanes are 3.0–3.1 m wide, not the assumed 3.66 m.
- **The v1 arm labels are rotated 90° from the compass** (see §14.3).

The v1 files stay as they were.

**v2 image-side geometry (2026-10-28).**

```bash
uv run --frozen python scripts/draw_ngsim_cam2_geometry_v2.py
```

The script writes `data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json` (contract `ImageGeometry`; pixels only, committed). It also writes two overlays, `image_geometry_v2.png` and `image_geometry_v1_vs_v2.png` in `data/processed/ngsim-cam2-analysis/` (Git-ignored, CC BY-SA 4.0 pixels). Two runs gave the same SHA-256 (`5878410b…02d2`).

How it is drawn:
- **Measured lines.** Every line starts from a pixel seed taken from the independent reading. It is then measured on the full-video median background, the `background.png` with SHA `4a1668e4…` (§14): a perpendicular profile every 2 px, a sub-pixel peak within ±3 px, and a total-least-squares fit with outlier removal.
- **Painted lines:** 25 of 28 lines are measured, with rms 0.06–0.6 px.
- **Kerb and edge lines:** a kerb or edge line counts as measured only when at least half the profile steps agree on it. Three did not:
  - the south kerb (gutter against lawn);
  - the east kerb (it ran into the right-turn arrow);
  - the east limit line (the zebra's lower edge, broken by the stripes).

  These keep their hand-read seeds, at 3 px error. The other edge lines carry an error floor of 2 px.

| Arm (image side) | Approach lanes | Limit line | Turns, and their evidence |
|---|---|---|---|
| N (right) | 6: `N_SB31`, `N_SB3`, `N_SB2`, `N_SB1`, `N_SB12`, `N_SB11`, matching NGSIM SB lane_ids | Outer crosswalk line (370–376, 96–199) | 31 right (painted arrow and bay code); 12 and 11 left (bay codes); 1–3 through (arrows hidden by queues) |
| S (left) | 5: NB lane_ids 11, 1, 2, 3, 4 (`S_NB11` … `S_NB4`) | Stop line (149, 186) to (140, 286) | 11 left (bay code); 1–4 through |
| W (top) | 3: `W_EB_kerb`, `W_EB_mid`, `W_EB_inner` | Outer crosswalk line (208, 66) to (256, 60), not v1's inner line | Unknown: no arrows visible, and the cross street has no lane ids |
| E (bottom) | 3: `E_WB_left`, `E_WB_through`, `E_WB_right` | Zebra lower edge (271, 289) to (331, 281) | Painted arrows: left (low confidence), through, right with "ONLY" |

Exit lines are drawn where an exit-side crosswalk line is visible (N, W, E). Exit lanes are not drawn.

The lanes run from the limit line to a fixed drawn extent: u = 470 on N, u = 12 on S, v = 4 on W and v = 400 on E. They carry `approximated` provenance and a per-lane error in pixels.

There are still no ground metres, because no calibration is accepted (§14.5). `ImageGeometry` is a new pixel contract, separate from the ground-metre `IntersectionGeometry` that the world model reads. The world model therefore still refuses camera 2.

### 14.5 Correspondences and calibration assessment

All three categories are in `control_points.json`:

**A. Image (observed).**
- 14 painted lane lines on the two Lankershim arms (8 north, 6 south), measured in pixels: a sub-pixel peak of perpendicular paint profiles every 0.5 m along each arm, then a total-least-squares line, with rms and sample count recorded. The registration only sets the ±4 px search window.
- 14 landmarks from the independent reading (limit-line ends, crosswalk corners, arrow tips), each with its uncertainty. These are not used in any fit and are kept for later check points.

**B. Ground (derived from NGSIM; `approximated` under ADR-014).**
- Lankershim lane centres: medians by `lane_id`, 8–32 m from the reference point on each arm.
- Lane boundaries (midpoints).
- Queue heads: the most advanced stopped vehicle per lane and frame, with lanes assigned by lateral position.
- The Lankershim bearing, in the local frame of §14.2.

**C. Correspondences (derived).**
- **Line pairs:** 14 painted lane lines matched to trajectory lane boundaries, within 0.9 m after registration. Residuals are in §14.3 item 4.
- **Point correspondences:** none.

**No calibration.** A point correspondence needs a second, crossing line that is known in both frames, and none is available without a time sync:
- **Cross streets:** they are tracked only from their limit lines inward. Their painted lane lines therefore have no trajectory counterpart, and the windows inside the junction box hold crosswalk stripes, arrows and turning traffic.
- **Superseded attempt:** an earlier attempt in this session crossed Lankershim lines with cross-street "lines". The review found that one of those lines was a zebra stripe plus an arrow shaft, and another a 3 m dotted guide line extrapolated 4–5×. Its hold-out table was degenerate, so the attempt was dropped.
- **Stop positions:** these would have to tie the frames along the road, but they disagree with the paint (§14.6).

So the record fixes the across-Lankershim direction to lane level (0.09 m and 0.17 m rms) and nothing along it. No calibration spec is written; v1's spec stays not accepted, and the world model keeps refusing camera 2. The ECC matrix is recorded only as an `approximated` registration, used to say where to look.

Missing evidence:
- Along-road check points from a time sync (§13 step 5: match stop-line discharge sequences, then click vehicle positions in synced frames).
- Or surveyed ground points: the complete NGSIM ortho photo or CAD files from ITS JPO.

### 14.6 Open discrepancy: published stop positions versus painted limit lines

The painted limit lines are independent landmarks, mapped through the registration and computed by the script. They sit 17.8 m (north arm) and 17.7 m (south arm, mean of the two ends; the line leans) from the reference point along the approach.

The NGSIM queue heads, with lanes assigned by lateral position, sit upstream of those lines. Gap from the limit line to the median head:

| Approach | Through lanes | Turn and kerb lanes |
|---|---|---|
| SB (north arm) | 4.7, 4.7 and 5.7 m | Left bays 11 and 12: 2.8 and 3.2 m |
| NB (south arm) | 4.2, 4.7 and 4.7 m | Kerb lane 4: 3.5 m; left bay 11: 1.3 m |

At the 90th-percentile head the gaps are 2.6–4.7 m for through lanes and 0.4–1.7 m for bays and the kerb lane. The result does not change with stopped-speed thresholds of 0.3–1.0 m/s (up to 2.0 m/s in the review) or with 1 s smoothing.

The video, by contrast, shows the lead vehicles about 0.5–1.5 m from the line on both approaches. That was read in 16 frames 50 s apart (review), and in frame 0 here. On the right arm, through-lane activity reaches its plateau within about 1.5 m of the line.

**Resolved in §15 (2026-10-28):** after the time sync, stopped NGSIM vehicles agree with the hand-read video fronts to 0.45 m rms, once one along-road scale correction of 1.156 is applied to the registration. The registration was wrong, not the trajectories. The rest of this section is the analysis as it stood before the sync.

So the registered NGSIM through-lane stop positions are 2–4 m short of where vehicles visibly stop. Equal gaps on opposite approaches rule out an along-road *translation* of the registration, but not an along-road *scale* error, which nothing time-independent constrains (§14.5).

An earlier version of this section suggested that the published positions behave like vehicle centres. The review rejected that. A regression of stopped leader–follower spacing on both vehicles' lengths (528 pairs) gives coefficients of 0.47 and −0.31, which leans towards the documented front reference (expected 1 and 0) rather than centres (expected 0.5 and 0.5), though weakly. The cause is therefore open: registration scale, NGSIM position bias near the junction zone, or both.

This blocks any along-road use of the trajectories (stop-line flows, queue lengths, check points). It does not affect the lateral lane correspondences of §14.5. Two things would settle it:
- vehicle fronts clicked by hand in a few frames, compared with the registered paint (no sync needed for the image side); done 2026-10-28, see below;
- vehicle-by-vehicle comparison after a time sync.

**Measured visible stop gap (2026-10-28, no time sync).**

```bash
uv run --frozen python scripts/measure_ngsim_cam2_stop_gap.py select
uv run --frozen python scripts/measure_ngsim_cam2_stop_gap.py report
```

`select` scanned the source video every 2 s and kept 11 frames, at least 60 s apart, in which at least two through lanes held a stationary vehicle near the limit line:
- north frames 3320, 5600, 8400, 9380 and 10260;
- south frames 580, 2440, 4520, 5620, 7500 and 10440.

Out of 70 and 131 candidates respectively. It writes 5× crops with a pixel grid and no markers.

The lead vehicle's front and rear in every visible lane were read by eye (Claude, unassisted by the automatic estimate) and committed as `data/geometry/ngsim-lankershim-cam2/v2/stop_gap_clicks.json`, with notes. `report` then measures each front against the v2 limit line, along the lane. It keeps only vehicles whose front patch barely changes over 1 s in the video: 42 of 47 (5 rejected, including the bay and kerb vehicles noted as possibly moving).

Two along-road scales convert pixels to metres:
- **Car lengths:** the clicked lead-vehicle lengths (median 25.5 px north, 30.0 px south) against NGSIM's median `v_length` of 4.82 m. This scale is independent of the registration.
- **Registration:** the local scale of the registration.

| Lane (published id) | Visible gap, median (n) | NGSIM gap, median |
|---|---|---|
| SB 3 / 2 / 1 | 1.8 m (5) / 1.0 m (4) / 2.2 m (5) | 5.7 / 4.7 / 4.8 m |
| SB left bays 12 / 11 | 1.0 m (3) / −0.3 m (3) | 3.2 / 2.8 m |
| NB 1 / 2 / 3 | 2.0 m (6) / 1.6 m (6) / 1.2 m (6) | 4.2 / 4.7 / 4.7 m |
| NB left bay 11 | −0.4 m (3) | 1.3 m |

These are car-length scale figures; a negative value means the vehicle stopped over the line. Over all lanes the visible gap is 1.4 m north and 1.3 m south (1.3 and 1.1 m with the registration's scale). The automatic estimate agrees within 1–4 px in most lanes.

Results:
- **The 4–6 m NGSIM gap is not driver behaviour.** Visible through-lane gaps are 1–2 m, and vehicles in the left bays stop at or over the line.
- **The registration shortens along-road distances.** At the stop lines it counts 5.87 px/m (north) and 7.31 px/m (south), 11% and 17% more than the car-length scale (5.29 and 6.23 px/m), so it makes along-road distances 10–15% too short. That is independent evidence of the along-road scale error §14.5 could not rule out.
- **The scale error explains part of the discrepancy, not all of it.** It does not by itself place the NGSIM stop positions; that still needs a time sync.

Reading uncertainty is about ±2 px per front, roughly ±0.4 m. Vans, trucks and shadows make some rears uncertain (noted per click), and the medians are robust to them.

### 14.7 Status and next steps

- **Done (local, no spend):**
  - the dataset audit;
  - the CRS check;
  - the camera 2 identification (int 2, high confidence);
  - the v1 audit, with corrections listed;
  - the A/B/C correspondence record (lateral only);
  - the calibration assessment: no calibration yet;
  - an independent adversarial review, whose findings were applied above.
- **Next (no spend):**
  1. ~~Draw the v2 image-side geometry~~: done 2026-10-28 (§14.4, `image_geometry.json`).
  2. ~~Click the lead-vehicle fronts by hand~~: done 2026-10-28. The visible gap is 1–2 m; the registration makes along-road distances 10–15% too short (§14.6).
  3. ~~Time sync per §13 step 5~~: done 2026-10-28 (§15). The offset is 119.7 s; §14.6 is resolved; the calibration is still not accepted.
- **Still waiting for the user:** ShareAlike on any published figure (§5 item 3); per-call spend approval before any real SAM or Muse request.

## 15. Time sync and along-road check points (2026-10-28, local only)

No network, no API call. Reproduce, after §14:

```bash
uv run --frozen python scripts/sync_ngsim_cam2.py
uv run --frozen python scripts/checkpoints_ngsim_cam2.py
```

The first writes `data/processed/ngsim-cam2-sync/` (Git-ignored: `sync.json`, correlation curves and a figure). The second writes `checkpoints.json` there and the curated `data/geometry/ngsim-lankershim-cam2/v2/sync_checkpoints.json` (committed; no pixels; NGSIM-derived values carry CC BY-SA 3.0).

**Offset (derived).** Discharge sequences were matched at all four limit lines:
- **Video signal:** per frame, the share of pixels differing from the background in a 25 px band just downstream of each v2 limit line.
- **Trajectory signal:** every 0.1 s, the vehicle-body sample points inside the same band, mapped to the ground through the registration.

Both series were z-scored and cross-correlated over lags of −60 to +282 s, then averaged over the four approaches. The relation is `video_time_s = (global_time_ms − 1118935800000)/1000 + offset_s`.

| | Lag | Peak r | Next peak ≥ 10 s away | Half-width |
|---|---|---|---|---|
| **Combined** | **119.7 s** | 0.49 | 0.20 (at 129.8 s) | 0.8 s |
| N / S / W / E | 119.6 / 119.6 / 119.8 / 120.0 s | 0.60 / 0.58 / 0.44 / 0.36 | 0.21 / 0.14 / 0.29 / 0.25 | 0.45 / 0.40 / 11.7 / 9.6 s |

Source frame 1197 is therefore 08:30:00.0, and the video starts at **08:28:00.3**. Two independent facts agree with that:
- the full NGSIM table starts at 08:28:00.2 (§13);
- the metadata PDF says transcription ran "from 8:28 a.m.".

`frame_id` (1201 at 08:30:00.0) counts frames from 08:28:00.0, which would make the offset exactly 120.0 s, within 0.3 s of the measurement. That 0.3 s is within the method's resolution. It matters for moving vehicles (about 3 m at 10 m/s) but not for stopped ones. The trajectories cover video frames 1197–10197; frames 580, 10260 and 10440 of §14.6 fall outside.

**Check points (observed image, published ground).** Each stopped lead vehicle read by hand (§14.6) is paired with the NGSIM vehicle of the same approach direction at the synced 0.1 s tick:
- **Matching window:** within 1.6 m across and 8 m along the road (compared through the registration), and stopped (below 0.5 m/s).
- **Matched:** 30 of the 31 stopped clicks inside the trajectory window (16 north, 14 south; NGSIM lane_ids 0, 1, 2, 3 and 12).

Because the vehicles are stopped, the pairs do not depend on a sub-second sync error. Residuals (image front through the registration, minus the NGSIM front):

| Approach | Along Lankershim, median (IQR) | Across, median (IQR) |
|---|---|---|
| North (SB) | −3.18 m (0.66) | +0.04 m (0.18) |
| South (NB) | +2.81 m (0.55) | +0.38 m (0.51) |

The along-road residuals are equal and opposite on the two approaches, the signature of a scale error. One along-road scale about the reference point, k = 1.156, removes them: what remains has a median of 0.15 m and an rms of 0.45 m. The car-length scale of §14.6 independently gives 1.11 and 1.17.

**Conclusions.**
- The ECC registration made along-road distances about 13–16% too short.
- NGSIM's stopped positions are consistent with the video to about 0.5 m. The published front-centre reference holds; the "positions are vehicle centres" idea fails again.
- The §14.6 discrepancy is explained.

**Calibration assessment: not accepted.** A homography was fitted (`fit_homography`) to the 30 pairs: fit on alternate frames of each approach (16 points), check on the others (14 points, different vehicles in different signal cycles).
- **Fit:** rms 0.40 m.
- **Check:** median 0.37 m, maximum 1.12 m. One point is over the repo's 1 m target.
- **Independent check against the 14 painted lane lines** (§14.5): rms 0.83 m, maximum 2.2 m. The large errors are on the south arm's SB exit-side lines, across the road from every fitted south point. The fit extrapolates across the road there.

No calibration file is written, and the world model still refuses camera 2. Fit points are needed on the exit sides and cross streets.

**Cross streets added (2026-10-28).**

```bash
uv run --frozen python scripts/checkpoints_ngsim_cam2.py select-cross
```

This mode chose 5 synced frames per cross street in which NGSIM has a stopped EB vehicle near the west limit line, or a stopped WB vehicle near the east one:
- west frames 1817, 3197, 5127, 6217 and 9207;
- east frames 3597, 4207, 5287, 7007 and 8557.

The lead vehicles were read by hand on 5× gridded crops (18 fronts; low-contrast ones skipped) and committed as `v2/cross_clicks.json`. 17 of them paired with a stopped NGSIM vehicle (8 west, 9 east). Their residuals through the old registration are 0.5 m and −0.05 m along Lankershim and 0.48 m and 0.24 m across it. The cross streets sit near the reference point, so the registration's along-road scale error barely affects them.

**Refit on all four approaches (47 pairs).**

| Check | Result | Pass? |
|---|---|---|
| Fit, alternate frames per approach | 26 points, rms 0.58 m | — |
| Held-out vehicles (21 points, other frames) | median 0.41 m; 20 of 21 within 1 m; worst 1.15 m (`E4207_E_WB_right`) | **No** (repo target 1.0 m) |
| Painted lane lines, independent (14 lines) | rms 0.25 m, worst 0.57 m (was 2.2 m without the cross streets) | Yes |

**Not accepted.** One held-out east right-lane vehicle is 0.15 m over the target, and the acceptance rule set for this step is that both checks must pass. No calibration file is written; `sync_checkpoints.json` records the assessment with `accepted: false`.

**More east-arm points (2026-10-28).**

```bash
uv run --frozen python scripts/checkpoints_ngsim_cam2.py select-cross E
```

This round wrote to `cross2/`. It chose 5 more east frames (1787, 2767, 5887, 7707, 9157), each at least 60 s from those already read. Their 7 fronts are appended to `cross_clicks.json`.

The pairing now keeps each NGSIM vehicle only once per approach, because a car stopped through a long red can be read in two frames and could then sit on both the fit and the check side. Four readings were dropped as repeats:
- frame 4207 through lane, the same car as in frame 3597 (a repeat already present in the previous round);
- frame 5887 through lane, the same car as in frame 5287;
- frame 9157 left and through lanes, the same cars as in frame 8557.

That leaves 49 unique pairs: N 16, S 14, W 8, E 11.

| Check | Result | Pass? |
|---|---|---|
| Fit, alternate frames per approach | 26 points, rms 0.53 m | — |
| Held-out vehicles (23) | median 0.42 m; worst per approach N 0.56, S 0.65, W 0.84, **E 1.38 m**. Two points over 1 m, both from frame 8557 (left 1.38 m, through 1.24 m). | **No** |
| Painted lane lines (14) | rms 0.29 m, worst 0.69 m | Yes |

**Still not accepted** under the same rule. The north, south and west approaches meet it (all within 0.84 m). The east approach fails in one frame. That frame's two vehicles stand in the image's lowest, most oblique region, about v 290–300, where the camera's scale changes fastest.

**Target 1.25 m (ADR-018, decided by the user, 2026-10-28).** The refit is unchanged; only the target moves.
- **Held-out vehicles:** 22 of 23 are within 1.25 m. The through-lane point at 1.24 m now passes; `E8557_E_WB_left` at 1.38 m does not.
- **Lane lines:** all within 0.69 m.

**Still not accepted.** No calibration file is written, and the world model still refuses camera 2. The rule now asks for every point within 1.25 m, so acceptance needs one of:
- further east-arm points that change the fit;
- a different hold-out design.

It does not mean dropping frame 8557.

**A third east round finds no frames (2026-10-28).** `select-cross E` returned none: every second of the 15-minute trajectory window with a stopped WB vehicle at the east limit line lies within 60 s of the 10 east frames already read. Under the 60 s separation rule the east arm is exhausted. The only further east points would come from a shorter separation. The one-pair-per-NGSIM-vehicle rule would still keep vehicles distinct, but that change is the user's decision.

**East round at 30 s separation (2026-10-28, user's decision).**

```bash
uv run --frozen python scripts/checkpoints_ngsim_cam2.py select-cross E --separation 30
```

This wrote to `cross3/`: frames 3897, 4957, 6187, 6617 and 8167, with 7 fronts appended to `cross_clicks.json`. Only one new vehicle resulted (frame 4957, right lane). Five readings were repeats of vehicles already paired, and one had no stopped NGSIM match. That leaves 50 unique pairs (E 12).

**Accepted only on one split, so not accepted.**

| Split | Held-out points | Worst | Pass at 1.25 m? |
|---|---|---|---|
| Alternate frames | 21 | 1.16 m | Yes |
| The same split swapped | 29 | 1.47 m (`E8557_E_WB_left`; also 8557 through 1.29 m, `W1817_W_EB_mid` 1.25 m) | **No** |

The lane lines are within 0.54 m.

The new frames reshuffled the alternate-frame split, and frame 8557's left lane moved from the check side to the fit side. That is why the first split passes. A pass that depends on which frames happen to be held out is not evidence. The script now requires both complementary splits to pass, which is a stricter reading of the user's held-out rule, recorded so it can be revisited. The calibration remains **not accepted**, and no calibration file is written.

## 16. Intersection 2 scenario from the published trajectories (2026-10-28, local only)

No network, no API call, **no camera input** (ADR-019). There are no pixels, no calibration and no image registration. Camera 2 stays uncalibrated (ADR-018) and outside this world model. Reproduce from the §13 CSV (SHA-256 checked):

```bash
uv run --frozen python scripts/build_ngsim_int2_scenario.py [--record]
```

Each run writes `data/processed/ngsim-int2/<stamp>/` (Git-ignored): `geometry.json`, `signal_timeline.json`, `world_model.json` (one point per second per vehicle) and `derivation.json`. It also writes two SUMO baselines (Git-ignored): `experiments/runs/ngsim-int2-baseline-<stamp>/`, and the same without right turn on red, suffixed `-no-rtor`.

With `--record` it writes the committed record `data/geometry/ngsim-lankershim-int2/`:

| File | SHA-256 |
|---|---|
| `geometry.json` | `f8fe90a4…a4f04` |
| `signal_timeline.json` | `71a114a4…44ab9b` |
| `scenario.json` | `9d08eb6e…f156a1` |

`scenario.json` holds:
- every policy threshold;
- per-arm statistics, movement counts and the NGSIM code census;
- the signal estimate and its checks;
- the compile, demand and vehicle-type settings;
- the baseline summary with per-seed metrics and their source-file hashes;
- the right-turn-on-red ablation;
- the processed-output hashes.

All values are derived from the trajectories and carry CC BY-SA 3.0 with attribution to U.S. DOT FHWA NGSIM (`8ect-6jqj`, DOI 10.21949/1504477); the attribution is in `scenario.json`.

**Reproducibility (observed).** A second, independent run gave byte-identical `geometry.json` and `signal_timeline.json`, the same network hash and the same tripinfo hash for all 5 seeds. `tests/test_ngsim_junction.py` recompiles the committed geometry and timeline with the recorded options and gets the recorded network hash and program. The committed figures differ from first drafts made during development: two independent reviews found defects, and those were fixed before this record was written.

Labels below: **published** (a value in the dataset), **derived** (our fixed rule over published values), **synthetic** (chosen by us).

**Frame (derived).** ADR-017's local metres, rotated by 25.8215°: the mean grid bearing of 37,118 NB through rows within 60 m. +y points along Lankershim northbound. N and S are Lankershim, E and W the cross street. The cross street lies about 5.6° off perpendicular (EB through bearing 110.2°); the network straightens it (synthetic). The study box is ±110 m: 377,040 rows and 1,015 vehicle runs (24 at least 7.5 m long, simulated as trucks).

**Arms (derived; distances from the reference point).** Approach statistics use inbound or stopped rows and exit statistics clearly outbound rows. A vehicle on the wrong side of the arm axis, such as a left bay against the median, is therefore not taken for an exit.

| Arm | Approach lanes (NGSIM lane_id, axis outwards), width | Stop position (median queue head) | Counting gate | Exit lanes |
|---|---|---|---|---|
| N | 11, 12, 1, 2, 3, 31 (two left bays, three through, right bay); 2.9–3.1 m | 23.1 m | 20.5 m | 3 |
| E | 1, 2, 3; 3.2–3.4 m | 17.0 m | 13.4 m | 3 |
| S | 11, 1, 2, 3, 4 (left bay, three through, right lane); 2.6–3.5 m | 22.4 m | 20.2 m | 3 |
| W | 1, 2, 3; 3.2–3.4 m | not observable (first tracked where vehicles stop, about 25 m) | 16.5 m | none derived (2 synthetic) |

- **Gates.** A gate sits just inside the nearer of two distances, less 0.5 m:
  - the most advanced decile of the arm's pooled queue heads;
  - where the arm's vehicles are first tracked (cross streets).

  It spans the inbound vehicles crossing that distance on either side of the axis.
- **Lane counts.** They agree with the image-side count of §14.4 (6/5/3/3). That is a count only; no position comes from the image.

**Movements (published positions, derived counts; 08:30–08:45, 900 s).** Turns are classified by gate crossings.

| From | Left | Through | Right |
|---|---|---|---|
| N | 133 (532 veh/h) | 323 (1,292) | 28 (112) |
| S | 15 (60) | 250 (1,000) | 82 (328) |
| W | 27 (108) | 34 (136) | 3 (12) |
| E | 6 (24) | 8 (32) | 37 (148) |

- **Completeness against NGSIM's own codes** (direction and movement of each vehicle's first `int_id` 2 row): 946 counted against 956, every cell within max(3, 10%). 10 tracks crossed only one gate and are listed, not counted.
- **Agreement at the crossing:** 93.5% of counted vehicles. 61 left turners still carry NGSIM's "through" code at the gate, because NGSIM switches movement codes inside the intersection.
- **Allowed turns** come from observed lane use, not markings. Examples: N11 and N12 are left only, N31 and S4 right only, and W1 left only.

**Signal timeline (`trajectory-derived`; no signal head was read; on a 1 s grid so SUMO runs it exactly).**

Heads and evidence:
- **N left:** protected; 5% of 133 lefts cross while S through is served.
- **S left:** follows N's protected left (67% of its 15 lefts fall in it), clipped where it would conflict.
- **N, S and W `all` heads:** estimated from their own through crossings. Permissive lefts are not used as green evidence.
- **E:** sparse but its own. None of its 14 through and left crossings fall in W's service; E runs about 10 s before W.
- **W left:** permissive; the opposing through is too small to test protection.

Median greens: N through 46 s, N left 21 s, S 31 s, S left 9 s, W 9 s, E 5 s. All 8 E greens are the 5 s minimum, a clamp, not a measurement. Yellow 4 s, start-up 2 s, clearance 1 s and minimum green 5 s are synthetic, and the timeline's `source` says so.

Thirteen windows were trimmed to remove conflicts and seven were dropped, each listed in `scenario.json`. Red gaps include time when a head may have been green with nobody crossing.

**Checks of the estimate.**
- **Consistency (circular for its own reference movements):**
  - through crossings during their head's green or yellow: N 99.7%, S 99.2%, W 97.1%, E 100%;
  - N left 94.0%;
  - informative rows: S left 60%, W left 77%, right turns 21–71% (they often go on red).
- **Onset check (from positions and speeds, not crossing times).** At each estimated green onset, the lead vehicle standing in a through lane passes 2 m/s 0.5–5.1 s later, and never before (19 onsets on four arms). This fits the 2 s start-up assumption. The check's definition was tightened after its first versions:
  - lead vehicle only, above 2 m/s;
  - through lanes only.

  Those first versions flagged creeping queues and turn lanes, which follow other heads or go on red.

**The cycle SUMO repeats (derived).**
- **Which cut:** of 25 candidate cuts (cuts from different arms overlap; the window holds 7 physical cycles), 7 qualify: every head green, none unknown, and the cut closes legally when repeated. The chosen cut is the one nearest the medians.
- **Its timing:** 99 s, against a median onset spacing of 99 s, which ranges from 80 to 201 s.
- **Its greens:** N through 45 s, N left 17 s, S 32 s, S left 10 s, W 12 s, E 5 s, summed 10 s from the medians.
- **Fixed-time simplification:** the spacing range suggests actuated control, which one fixed cycle does not represent.

**SUMO baseline (simulated; seeds 1–5, 300 s warm-up, 900 s horizon, 600 s drain, 1 s step).**
- **Run validity:** every seed loaded 1,214–1,261 vehicles and all arrived, with 0 teleports and 0 collisions (junction collisions checked) and no emergency braking. Reruns are identical. "Valid" means run validity, not model validity.
- **Network parameters:** 46 `approximated`, 11 `synthetic`.
  - The synthetic ones are the arm lengths (200 m), the speed limit (13.89 m/s), the W exit lanes, right turn on red and four SUMO stop-line distances.
  - SUMO puts its stop lines 5–6.5 m from the counting gates, because netconvert builds the junction from lane widths: nearer the centre on Lankershim (N 14.0 m against 20.5 m, S 15.2 against 20.2) and farther out on the cross street (E 19.3 against 13.4, W 22.1 against 16.5).
- **Right turn on red** is a modelling choice. Observed right turners crossing on red: E 79%, S 52%, N 29%, W 1 of 3. Without it, every seed is invalid with 5–28 vehicles unfinished.

| Metric | Simulated, mean ± SD over 5 seeds | Observed (trajectories) |
|---|---|---|
| Throughput | 3,745 ± 32 veh/h | 3,784 veh/h (demand-derived: the demand is the observed counts) |
| Max / mean queue, N | 100 ± 30 / 43 ± 16 m | ≥ 84 / ≥ 31 m |
| Max / mean queue, S | 79 ± 9 / 21 ± 2 m | ≥ 80 / ≥ 22 m |
| Queues, E / W | 29 ± 13 / 44 ± 12 m max | E ≥ 10 m (observed only about 10 m deep); W not observable |
| Mean delay | 32.9 ± 5.0 s | not observed |

- **Lower bounds:** "≥" marks values that include censored seconds, when the queue reached the end of the observed lane. For example, N12's 84 m queue at 115 s continues beyond the observed lane end in the dataset.
- **Different measures:** SUMO's queue runs from its stop line; the observed one runs from the counting gate to the front of the last queued vehicle. This is a plausibility check, not a calibration.

**Limitations.**
- **Signal timing.** It is an estimate from discharges, not a published plan, and SUMO repeats one 99 s cycle of what looks like actuated control.
- **Small samples.** The E phase rests on 14 vehicles and the S left head on 15.
- **Synthetic geometry.** Bay lengths, arm geometry beyond ±110 m, straight arms and the speed limit are synthetic.
- **Not modelled.** There are no pedestrians.
- **Queue error.** Queue lengths carry NGSIM's stated 4 ft position error only (not a verified sigma). They exclude the gate-to-stop-line offset (2–4 m) and the last vehicle's length.
- **Latent: vehicle runs.** A vehicle split into two runs by a gap in its rows would be listed as ambiguous; this data has one run per vehicle.

**Fresh-clone replay (2026-10-28, ADR-020).**
- **Rebuild:** `build_ngsim_int2_scenario.py --as-of 2026-10-28T07:43:04Z --check-record` rebuilds the scenario at the recorded time, under the recorded directory names.
- **Reply:** the experiment's Muse reply is committed at `cache/muse/` with only its rate-limit header values removed. Its notice gives the SHA-256 of the raw and committed files, the attribution and the licence.
- **Test before committing.** A fresh clone in another folder, with the trajectories copied in, gave:
  - byte-identical `geometry.json`, `signal_timeline.json`, `world_model.json` and `baseline_manifest.json`;
  - the same bundle and request key;
  - with the reply in place and no API key, a replay with 0 trace problems and all 15 candidate tripinfo hashes identical.
- **Scrubbed reply locally:** replaying from the scrubbed file gave the same `bundle.json` and `reasoning.json` as the raw one, and the same tripinfo hashes.

## 17. Baseline validity and experiment eligibility (2026-10-28, local only, no spend)

**Question.** Is the §16 SUMO baseline trustworthy enough to evaluate interventions? No API call was made, and no new candidate was generated. Camera 2 stays uncalibrated and outside the world model (ADR-018). The recorded baseline, the experiment and their settings are unchanged; every analysis reruns or re-reads them in new directories. Labels: **observed** (NGSIM trajectories), **derived** (our rule over observed values), **assumed** (synthetic), **simulated** (SUMO).

### 17.1 Unfinished vehicles (Investigation A)

**Same runs.** All 20 recorded candidate runs (I1–I3) and the baseline were replayed, and every tripinfo hash matched its RunRecord.

**Which vehicles.** Every vehicle unfinished at 1800 s is a west-to-north left turner on `W_in_2`, the left-only lane:

| Run | Unfinished |
|---|---|
| I1 seed 4 | 3 |
| I1 seed 5 | 2 |
| I3 seeds 2 and 3 | 2 each |
| I3 seed 4 | 12 |
| I3 seed 5 | 11 |

- At 1800 s some were already past the junction and the rest queued at most 10 deep.
- The W→N link is `G` only in the west phase: protected-only.
- None needed a lane change, none was in a deadlock, and none stood still for 300 s, so nothing teleported.

**Mechanism: oversaturation caused by the intervention.**
- The west phase gives 12 + 4 s per 99 s cycle in the baseline and I2, 6 + 4 s per 102 s in I1, and 5 + 4 s per 102 s in I3.
- Simulated saturated discharge is 5.3, 2.6 and 2.0 vehicles per green. Against the observed 108 veh/h, that is a degree of saturation of about 0.56, 1.20 and 1.53 (1.06–1.96 across seeds).
- 96% of I1 and I3 cycles do not clear their queue. The I3 seed 4 queue peaks at 23 vehicles, and insertion was delayed by up to 19.7 s.
- These saturation figures are our estimates from the simulation, not measurements.

**Is the baseline near the edge?** Its network empties by 1279–1378 s. Its tightest movement is the protected north left: x ≈ 0.95 in simulation, with 40% of window cycles not clearing.

**Drain sensitivity** (`scripts/drain_sensitivity_ngsim_int2.py`, ends 1800, 2400 and 4200 s):
- Every output before 1800 s is identical to the recorded runs.
- Every vehicle finishes by 2317 s.
- Throughput and all queue metrics are identical at every end.
- Only the mean delay of incomplete runs moves: +0.1 to +3.2 s, because unfinished trips' timeLoss was truncated.
- With complete trips:
  - I1 mean delay worsens by +6.9 s [1.0, 12.9];
  - I3 mean delay is +12.7 s [−0.2, 25.7], inconclusive. At 1800 s it read "worsens", because the truncation shrank its spread.

**Decision (ADR-021).** The termination policy stays the same for every arm. An incomplete candidate is never selectable. It is compared on throughput and window queues, which are unaffected by the end, and not on mean delay. The old "not compared" label hid I1's and I3's west-queue harm:
- max W queue +51.0 m [12.4, 89.6] and +82.4 m [42.1, 122.7];
- mean W queue +33.0 m and +53.1 m.

**Latent, not changed.** `mean_delay_s` defines its cohort by actual insertion time. A vehicle held at the entry past 1200 s drops out, which would bias a congested arm low. On the recorded runs it moves 2–3 vehicles at the window edges and the mean by under 0.1 s.

### 17.2 Signal timing (Investigation C)

**How the timeline is inferred.** Green onsets are the first stop-line crossing minus 2 s (derived). 19 onsets are confirmed independently: the lead vehicle passes 2 m/s 0.5–5.1 s later. Other values are less certain:
- **Onsets:** could be up to 31 s earlier.
- **Green ends:** the last crossing minus 4 s, a lower bound; the true end could be 0–31 s later (21–31 s for E).
- **E greens:** all 8 are the 5 s minimum clamp (assumed).
- **S-left greens:** clipped to the N-through onset, so not an independent measure.
- **Red gaps:** may contain unused green.
- **Constants:** yellow, start-up, clearance and minimum green are assumed.

**Physical cycles.** Seven, with S-through onsets 94–104 s apart (mean 99.4 s). The N-through onset lags the N-left onset by −1 to 24 s while vehicles wait. E is skipped once with no E demand (cycle 360–460), which gives the 201 s spacing. S left goes unserved in 2 cycles. This fits semi-actuated side and left phases inside a near-constant cycle; confidence is moderate. Timing sheets, detector data or readable signal heads would settle it, and none is available (§§7, 9).

**Sensitivity** (`scripts/signal_sensitivity_ngsim_int2.py`, seeds 1–5, recorded settings, demand hashes as recorded, only the cut changed):
- **Cuts:** of 25 candidate cuts, 7 qualify; cut 564 reproduces the recorded network and tripinfo.
- **3 of the 7 give incomplete baselines** (ADR-021 terms: nothing broke, but vehicles were left after the drain). Each is a protected-only left turn with a short green:
  - cuts 244 and 266: the west left at 5 s, with west-to-north vehicles unfinished on seeds 2–5 and 4–5;
  - cut 402: the south left at 4 s, with 2 south-to-west vehicles on seed 5.
- **The 4 complete cuts, paired against the recorded cut** on the same seeds and demand:
  - mean delay −1.4, +3.4 and +6.1 s (the last two with intervals excluding zero), against I2's −2.0 s;
  - throughput −21, −71 and −143 veh/h;
  - south mean queue +2.6, +5.3 and +7.1 m (the last two excluding zero), against I2's +2.1 m;
  - north mean queue −13.4 and +10.3 m (excluding zero).
- **I2's recorded change re-applied** to cuts 601 and 615, which have the same phase structure. The validator accepted it on both, and no vehicle was left unfinished. Only the south-queue verdict held: it worsens again, by +8.1 [4.5, 11.7] and +12.7 [4.6, 20.9] m, and delay is again inconclusive. Cut 615 adds a small east-queue "improves" (−0.16 m), and throughput changes sign.

**Conclusion.** Repeating one observed cut is a material limitation. Whether the baseline clears its demand depends on the cut, and changing the cut moves the baseline by more than the effects being judged. I2's decisive verdict held in direction but not in size.

### 17.3 Statistics (Investigation D)

**Mechanics check out:**
- the pairing is real (same demand file and `--seed` per seed);
- the T95 table and its degrees-of-freedom indexing are correct;
- every metric's direction is correct.

**Problems found:**
- **Multiplicity.** With 10 metrics at an uncorrected 95% each, a candidate with no effect becomes eligible with probability 0.11–0.18, and one of three null candidates with 0.24–0.44 (Monte Carlo with the recorded SDs and correlations).
- **Degenerate intervals.** A zero or near-zero standard deviation of the differences gives a zero-width interval and a spurious verdict (e.g. a constant +0.01 m).
- **Throughput** cannot meaningfully improve when every vehicle arrives.
- **The maximum queue** moves in whole-vehicle jumps.
- **The old selection reason** ignored excluded candidates.

**Power at n = 5.** The minimum detectable effect at 80% power is 1.68 SD of the paired difference at α = 0.05 (6.7 s of mean delay for I2), and 3.16 SD at α = 0.005. Detecting a 2 s delay effect needs about 34 seeds (57 under Holm).

**Implemented (ADR-021):**
- Holm's step-down at a family-wise 5% over a family fixed in advance: every simulated candidate with mean delay and each mean queue. Untested claims still count.
- Throughput and the maximum queue may only worsen.
- Worsening stays uncorrected.
- Zero-SD and censored-queue claims are not tested.
- The reason names every candidate.

**Independent review.** It confirmed the t distribution against closed forms and Monte Carlo, the Holm stepping, the power figures and backward compatibility. It found seven should-fix items, all applied before the records below were written:
- rule text that still said incomplete candidates are not compared;
- a false throughput rationale;
- a family chosen from outcomes;
- censored window queues;
- A9 not checking the assessment's baseline;
- the re-evaluation not checking SUMO outputs;
- overstated wording for broken runs.

**Recorded experiment under the new rules** (`experiments/records/ngsim-int2-reevaluation-20261028T122501Z.json`, 0 trace problems):
- **Selected and recommended: none.**
- I1 and I3 are incomplete and worsen the west queues. I3's are lower bounds: `W_in_2` filled on seed 4.
- I2 worsens the south mean queue (t = −5.28, p = 0.0062).
- No improvement survives the 15 planned tests: the first Holm threshold is 0.0033, and I2's best improvement p is 0.135, for the north mean queue.

### 17.4 Validation against the NGSIM observations (Investigation B)

**How it was checked** (`scripts/validate_ngsim_int2_baseline.py`; record `experiments/records/ngsim-int2-baseline-validity-20261028T121654Z/validation/`):
- **Same runs.** The 5 recorded seeds were rerun with FCD output. Each reproduces the recorded tripinfo once the FCD device entries are removed.
- **Same rules on both sides.** The observed side is rebuilt from the CSV; its world model is byte-identical to the record. The same `build_world_model_from_ground` rules then run on both sides, with:
  - the same ±110 m box and a 900 s window (simulated 300–1200 s);
  - SUMO's own counting lines, found with the observed rule;
  - lanes cut to the observed depth, so queues are censored alike.
- **Timing.** The simulated signal state is read with a 1 s shift: SUMO's state at step t governs the motion it reports for (t−1, t]. With that shift, 3020/3020 simulated through crossings fall in green or yellow.

**Coverage.** 93 quantities, each with its own status (no pass/fail score): 13 comparable, 68 comparable with caveats, 12 not comparable. A further 8 are listed as not validatable.

| Quantity | Observed (95% interval) | Simulated (5 seeds) | Reading |
|---|---|---|---|
| Vehicles counted per 900 s | 946 [887, 1008] | 928.8 ± 8.1 | agrees; circular (demand comes from these counts) |
| Saturation headway, positions ≥ 5, S through | 2.03 s [1.89, 2.21] (n = 56) | 2.02 ± 0.03 s | agrees |
| Same, N through | 1.81 s [1.68, 2.13] (n = 14) | 2.02 ± 0.02 s | SUMO slower; small observed sample |
| Headway, positions 2–4, N / S | 3.20 / 2.30 s | 2.70 / 2.69 s | opposite errors: start-up lost time unvalidated |
| N through arriving on green | 73.7% [68.6, 78.2] | 45.6% ± 1.9 | **platoon ratio 1.63 observed, about 1.0 simulated** |
| N through stopping at least once | 31.9% [27.0, 37.2] | 64.8% ± 2.5 | SUMO doubles N stops |
| N through segment travel time | 20.3 s [15.4, 25.5] | 30.4 ± 1.2 s | SUMO +10 s |
| S through stopping / travel time | 74.8% / 40.5 s | 73.4% / 38.4 s | agrees |
| S lane shares S1 / S3 | 16.1% / 31.1% | 24.2% / 25.0% | SUMO spreads S evenly |
| Queue at end of red, S through | ≥ 18.6 veh | ≥ 17.2 ± 0.9 veh | agrees (censored) |
| Queue at end of red, N through | ≥ 8.4 veh | ≥ 17.6 ± 0.6 veh | SUMO about double |
| Right turners stopping, N / S | 14.3% / 41.5% | 46.2% / 63.6% | SUMO's right turn on red forces a full stop |

**What it means for signal-timing decisions.**
- **South approach (northbound): plausible.** It agrees on saturation flow, stops, travel time and the end-of-red queue in vehicles. Its longest-lane queue in metres is understated, because SUMO does not reproduce the observed lane preference.
- **North approach (southbound): not represented.** It is observed arriving in platoons, probably progressed from the upstream signal, while SUMO's Poisson arrivals are random. SUMO therefore overstates north stops, queue and delay. Interventions that give the north more green will look better than they are, and offsets or coordination cannot be evaluated.
- **Right turns** carry a modelling artefact: the full stop SUMO requires before a right turn on red.

**Not validatable with this data, and what would be needed:**
- **Mean delay:** no observed counterpart. Needs observed free-flow speeds over a longer corridor.
- **East and west queues and travel times:** tracked only about 10–12 m beyond the counting lines. Needs upstream coverage.
- **Signal timing and start-up lost time:** estimated from the same crossings. Needs a timing plan or signal logs.
- **Residual queues after green:** circular for the same reason.
- **Throughput:** circular.
- **Pedestrians, and vehicle and driver parameters:** not observed and not calibrated.

### 17.5 Assessment: not validated

`data/geometry/ngsim-lankershim-int2/baseline_assessment.json` (ADR-021) records the judgement, tied to the baseline manifest's hash and to the SHA-256 of 7 evidence files.

The baseline runs validly and reproduces its demand, but it is **not validated for estimating intervention effects**:
- its signal program repeats one cut of what looks like actuated control, and the cut decides whether the baseline clears its demand (§17.2);
- its north approach misses the observed platoon arrivals (§17.4);
- mean delay, the main decision metric, has no observed counterpart.

Consequences:
- **Nothing is recommended.** Even if a candidate were selected, the report would call it a simulated result, not a recommendation.
- **What the baseline can still support:** directional statements about harm on the south approach and on protected lefts. That is how I1, I3 and I2 fail.

### 17.6 Reproduce

```bash
uv run --frozen python scripts/validate_ngsim_int2_baseline.py --out experiments/runs/ngsim-int2-validation-<stamp>
uv run --frozen python scripts/signal_sensitivity_ngsim_int2.py --out experiments/runs/ngsim-int2-signal-sensitivity-<stamp> --i2
uv run --frozen python scripts/drain_sensitivity_ngsim_int2.py --out experiments/runs/ngsim-int2-drain-sensitivity-<stamp>
uv run --frozen python scripts/reevaluate_ngsim_int2_experiment.py
```

The four commands take about 30, 45, 35 and 5 s.
- **Inputs:** each needs the trajectories, the processed world model and the recorded baseline and experiment run directories, all Git-ignored and rebuilt per §16. Each checks its inputs against the recorded hashes.
- **Outputs:** each writes only a new directory.
- **Records:** the committed copies are in `experiments/records/ngsim-int2-baseline-validity-20261028T121654Z/`, whose `NOTICE.md` gives the commits and the licence. On this machine, a second independent run of each script gave identical reports, apart from timestamps and paths.

### 17.7 Blockers and next milestone

**Blockers for any recommendation on this junction:**
- **North platoon arrivals.** Demand needs driving from the observed arrival times at the entry line instead of a Poisson process.
- **Single repeated cycle.** It needs a timing plan or signal-state logs, or a model of the actuated logic. Short of that, every candidate should be compared on every observed cut that clears its demand, with the verdicts reported as a range.
- **No observed delay.**
- **Power:** n = 5 detects only large effects. About 20 or more pre-registered seeds are needed.

Paid API calls and camera 2 are not involved in any of these.

## 18. Replay baseline: observed signals and observed vehicles (2026-10-28, local only, no spend)

**What it is** (ADR-022). The milestone after §17.7. A second baseline on the same committed geometry and compile options:
- **Signals.** It replays the trajectory-derived timeline over clip 118–751 s, the span where every head is known, with no state invented. After that come 4 s of yellow, 1 s of all-red (synthetic) and the cut cycle, for the drain only.
- **Demand.** It is driven by the 731 counted vehicles observed in that span. Excluded: 81 that crossed before 118 s and 134 first seen after 751 s.
  - 38 enter in place at 118 s, re-packed at SUMO's spacing;
  - 600 enter where first seen, about 105 m out on Lankershim, at SUMO's safe speed;
  - 93 cross-street vehicles first seen within 40 m of the stop line enter at the arm start, timed for the speed limit.
- **Window and seeds.** The window is the replay span (warm-up 0), with a 600 s drain. Seeds 1–20 were pre-registered.

**The baseline** (`scripts/build_ngsim_int2_realism.py --record` at `7b75e1a`; summary `experiments/records/ngsim-int2-realism-baseline-20261028T130401Z.json`):
- valid on all 20 seeds: every vehicle arrives, with 0 teleports and 0 collisions;
- reruns identical;
- insertion delay at most 8.8 s, and no more than 2 vehicles per seed over 5 s.

**A rule that failed.** A first version kept each vehicle's observed speed and entered cross-street vehicles inside their queues. It left 7 vehicles uninserted and delayed others by up to 399 s. Entering those cross-street vehicles where first seen, at SUMO's safe speed, still blocked them for up to 202 s.

**Validation** (the §17.4 script with a replay branch; record `experiments/records/ngsim-int2-realism-validity-20261028T130401Z/`):
- observed clip [118, 751) s against simulated [0, 633) s;
- the replay program equals the timeline second by second;
- 838 of 838 through crossings fall in green or yellow;
- each simulated vehicle is paired with its track.

| Quantity | Observed | Repeated-cycle baseline (§17.4) | Replay baseline |
|---|---|---|---|
| N through: share stopping | 24.1% [19.0, 30.1] | 64.8% (vs 31.9% over 900 s) | 30.2% ± 0.9 |
| N through: segment travel time | 17.2 s [12.3, 23.5] | 30.4 s (vs 20.3) | 18.9 ± 0.3 s |
| N through: arriving on green | 81.2% | 45.6% (vs 73.7%) | 76.3% |
| S through: share stopping | 66.9% [59.3, 73.7] | 73.4% (vs 74.8%) | 79.8% ± 0.8 |
| S through: segment travel time | 37.0 s [31.0, 40.6] | 38.4 s (vs 40.5) | 40.8 ± 0.5 s |
| Saturation headway, S, positions ≥ 5 | 2.06 s [1.91, 2.27] | 2.02 (vs 2.03) | 1.99 ± 0.03 s |

Paired stop-line crossing time, simulated minus observed (median per seed):

| Movement | Lag |
|---|---|
| N through | +0.1 to +0.3 s |
| N left | −1.5 to −2.5 s |
| S through | −3.0 to −3.8 s |
| E right | +2.5 to +4.3 s |
| **W left / through / right** | **+89 to +93 / +86 to +87 / +167 to +168 s** |

**Reading.**
- **North: fixed.** It is now close to the observations: the missing platoons were the main cause.
- **South: over-stops** by 13 points and is 3.8 s slow, while its vehicles reach the stop line about 3 s early. A plausible cause, not tested, is SUMO's start-up discharge, which the §17.4 queue-position 2–4 headways (2.69 s simulated, 2.30 s observed) already showed.
- **West: wrong.** Its vehicles cross one to two cycles late. They are observed only inside their queues, so their arrivals cannot be reconstructed, and west and east results are not trusted.
- **Unchanged:** the S lane shares (SUMO spreads S evenly) and the absence of any observed delay.

**Candidates** (the three recorded validated outcomes mapped onto every replayed cycle; `scripts/run_ngsim_int2_realism_experiment.py --record`; record `experiments/records/ngsim-int2-realism-experiment-20261028T130511Z.json`; 0 trace problems):
- **How they applied:**
  - I2 is −5 s on 14 N/S-through phases and +5 s on 12 protected-left phases;
  - I1 cuts the west green on 14 phases, 3 of them clamped at 5 s;
  - I3 has 4 west phases clamped, and its north-lead extension is absent from 7 short phases.
- **Status:** all three are complete on every seed.
- **Selected: none. Recommended: none** (the replay baseline is not assessed).

| Candidate | Mean delay | Mean S queue | Mean N queue | W / E mean queue | Throughput |
|---|---|---|---|---|---|
| I1 | −0.01 s [−0.22, 0.20] | −4.9 m (improves) | inconclusive | +3.8 / +0.6 m (worsen) | +53 veh/h |
| I2 | **+2.6 s [2.4, 2.8] (worsens)** | **+1.7 m (worsens)** | −1.2 m (improves) | +0.2 / −0.5 m | −115 veh/h (worsens) |
| I3 | +0.2 s [0.04, 0.4] (worsens) | −0.35 m | −3.4 m (improves) | +3.6 / +0.15 m (worsen) | −5 veh/h |

**What it supports:**
- **I2 is harmful.** It worsens Lankershim delay and the south queue in both baselines.
- **I1 and I3 cannot be judged.** I1 is ineligible only because of cross-street queues, and I3 partly so. The cross streets are where this model fails, and on the repeated-cycle baseline I1 oversaturated the west left.

None of this is a recommendation.

**Limits.**
- **One realisation.** With identical vehicles on every seed, the 20-seed intervals measure only SUMO's microscopic noise. They are conditional on one observed 10.5 minutes, not a demand-uncertainty estimate.
- **Unchanged from §17.7:** the window is short (6.4 cycles), there is no observed delay, and the north saturation headway rests on 10 observations.

**Reproduce:**

```bash
uv run --frozen python scripts/build_ngsim_int2_realism.py
uv run --frozen python scripts/validate_ngsim_int2_baseline.py --baseline experiments/runs/ngsim-int2-realism-baseline-<stamp> --out experiments/runs/ngsim-int2-realism-validation-<stamp>
uv run --frozen python scripts/run_ngsim_int2_realism_experiment.py experiments/runs/ngsim-int2-realism-baseline-<stamp>
```

**Next.**
- **Cross streets:** model arrivals for the cross streets from their discharge times and the observed signal states, rather than from first observations. For example, place each vehicle in the queue of the red that precedes its observed discharge.
- **South discharge:** calibrate SUMO's start-up discharge against the observed queue-position headways.
- **Demand uncertainty:** represent it, for example by resampling the observed arrivals cycle by cycle, before the intervals can stand for more than one realisation.
- **Assessment:** write a baseline assessment for the replay baseline once these are settled.

## 19. Cross streets anchored to their observed discharge (2026-10-28, local only, no spend)

**Rule** (`trace_demand.anchor_to_discharge`, commit `89b3377`). This applies to a cross-street vehicle first seen within 40 m of the stop line whose stop-line crossing falls in a green or yellow of its arm's `all` head. It joins the queue of the red before that green:
- per arm and green, in observed crossing order;
- arriving 2 s apart, the last one 2 s before the onset;
- spread evenly over the red when the red is shorter;
- entering at the arm start, timed to reach the stop line at the speed limit.

Vehicles crossing outside every green (mostly right turns on red) keep the plain upstream timing. The gap is an assumption; the order and the green each vehicle used are observed.

**Baseline** (record `experiments/records/ngsim-int2-realism-baseline-20261028T132709Z.json`, commit `1da71bf`):
- 62 entries are now `discharge`, 31 stay `upstream`;
- valid on all 20 seeds, with reruns identical;
- insertion delay at most 10 s.

**Validation** (record `experiments/records/ngsim-int2-realism-validity-20261028T132709Z/`). Paired crossing lag, simulated minus observed: the range of the per-seed medians over all 20 seeds, and the pooled share within ±5 s. (Corrected 2026-10-28: the first version of this table read too few seeds and named the wrong cause for the west lefts; see §20.)

| Movement | §18 rule | Discharge-anchored |
|---|---|---|
| W through | +85.4 to +86.9 s (33%) | **−1.2 to −0.2 s** (41%: the vehicles served at the window start still wait, §20) |
| W left | +10.3 to +93.8 s (32%) | +6.2 to +13.2 s (46%; causes in §20) |
| W right (3 vehicles: 1 right turn on red, 2 in a green) | +167.4 to +168.9 s | +167.4 to +168.5 s (unchanged) |
| E left / through / right | −3.0 to −2.5 / −3.1 to −2.8 / +2.4 to +4.3 s | −3.0 to −2.7 / −3.4 to −2.7 / +1.0 to +3.5 s |
| N through | 0.0 to +0.4 s | 0.0 to +0.3 s |
| S through | −3.9 to −2.7 s | −3.8 to −3.1 s |

North and south are unchanged: north through stops 30.6% against 24.1% observed, south 79.6% against 66.9%.

**South start-up: not calibrated, on the evidence.** The observed headway at queue positions 2–4 is:
- 3.61 s on N, against SUMO's 2.87 s: SUMO is faster;
- 2.31 s on S, against SUMO's 2.69 s: SUMO is slower.

Changing SUMO's global vehicle parameters would move one arm the wrong way. Per-arm tuning would rest on about 65 S and 33 N observations, from one 10.5-minute window. The south over-stopping stays a documented model limit.

**Candidates** (record `experiments/records/ngsim-int2-realism-experiment-20261028T132818Z.json`, 0 trace problems): all three are complete on every seed. **Selected: none. Recommended: none** (the replay baseline is not assessed).

| Candidate | Mean delay | Mean S queue | Mean N queue | Mean W / E queue | Throughput |
|---|---|---|---|---|---|
| I1 | **+1.95 s [1.67, 2.24] (worsens)** | −4.8 m (Holm-confirmed improvement) | inconclusive | +7.6 / +2.7 m (worsen) | +9.7 veh/h |
| I2 | **+3.34 s [3.15, 3.52] (worsens)** | +1.6 m (worsens) | −1.1 m (Holm-confirmed) | +1.2 / +2.5 m (worsen) | −112 veh/h (worsens) |
| I3 | +0.33 s [0.16, 0.51] (worsens) | −0.3 m (Holm-confirmed) | −3.1 m (Holm-confirmed) | +4.1 / +0.6 m (worsen) | −28 veh/h (worsens) |

**What changed from §18.** With west traffic crossing in the green it really used, I1's west-green cut now worsens mean delay, as it oversaturated the west left on the repeated-cycle baseline (§17.1). All three candidates now worsen overall delay. As before, the intervals measure microscopic noise for one observed realisation only.

**Still open:**
- the west left lag (+12 s);
- west right turns (3 vehicles);
- south over-stopping;
- demand resampling;
- a baseline assessment for the replay baseline.

## 20. West left turns, and the first replay assessment (2026-10-28, local only, no spend)

**Question.** Why do west left turns still lag after discharge anchoring (§19)? Is it permissive lefts waiting behind the queue?

**Method.** A read-only probe of seed 1 of the validation rerun of `ngsim-int2-realism-baseline-20261028T132709Z`. For each west-left vehicle it lists:
- its observed stop-line crossing;
- the green of the west `all` head that crossing fell in (`trace_demand.serving_green`);
- the first simulated second it is off `W_in`.

**Finding: mostly not the queue.** 23 west-left vehicles (22 in the paired window):

| Vehicles | Observed crossing | Lag | Cause |
|---|---|---|---|
| WN.0, WN.12–13, WN.15–21 (10) | in their green | −3.6 to −1.3 s | on time |
| WN.1–3 | 126.6–132.7 s, in the green already on at the window start (118–133 s) | +186 to +269 s | **window start**: the timeline is unknown before 118 s, so there is no red to queue them in. They enter at the arm start at 0 s and reach the stop line after that green has ended. |
| WN.4–9 | 222.7–235.9 s and 323.2–327.6 s, while the timeline shows west red | +88 to +182 s | **timeline**: windows the estimator dropped (few through crossings, §17.2) |
| WN.10–11 | in the 402 s green | +12.6 to +13.0 s | queued behind WN.3–9 |
| WN.14, WN.22 | last of their green's group | +76 to +92 s | **timeline**: green ends are fitted to the last discharge, a lower bound. SUMO's green ends before they reach the line. WN.22 crosses after the window end, so it is not paired. |

The same window-start green also serves 7 west through vehicles and 1 west right turner (WE.2–8, WS.0), and they wait about 180 s too. That is why only 41% of west through vehicles are within 5 s, although their median lag is about −1 s.

**What can be fixed where.**
- **Window start (11 west vehicles): demand side.** They were in the queue when the window opened.
- **Dropped windows and conservative green ends (8 west lefts): signal side.** No demand rule can let a vehicle cross on a red that the replay shows. The fix is a new timeline version that:
  - accepts permissive-left crossings as evidence of a west green;
  - does not end each green at its last discharge.

  The committed timeline is an input record and stays as it is. Deferred.
- **Behind the queue (2 vehicles, +13 s):** this follows from the two causes above.

**Assessment of the replay baseline** (`data/geometry/ngsim-lankershim-int2/replay_assessments/ngsim-int2-realism-baseline-20261028T132709Z.json`): **partially validated**, so nothing simulated on it can be recommended.
- It reproduces the timing of Lankershim's through traffic: paired crossings within 4 s, and north through travel time 18.9 s against 17.2 s [12.3, 23.5] observed.
- North through stopping is just outside the observed interval: 30.6% against 24.1% [19.0, 30.1].
- The south over-stops: 79.6% against 66.9%.
- Turns:
  - right turners stop far more often than observed;
  - left turns are slower than observed;
  - the west vehicles above wait one to two cycles.
- Two numbers have no observed counterpart: delay, and the east and west queues.
- Throughput is circular.
- All 20 seeds replay the same 10.5 observed minutes.

The evidence is the baseline record and the validity record's `NOTICE.md`, `validation.json` and `validation.md`, each with its SHA-256.

There is one assessment per baseline, named after its run directory. `scripts/run_ngsim_int2_realism_experiment.py` loads the assessment for the baseline it runs on and checks it. With none, it reports `not_assessed`.

**Record caveat.** That record's `validation.md` lists the §18 cross-street rule among its assumptions, but the run used discharge anchoring (its `trace_vehicles.json` options). The numbers are unaffected. `scripts/validate_ngsim_int2_baseline.py` now takes the rule from the vehicle table's options.

## 21. Window-start queues, and the replay baseline assessed again (2026-10-28, local only, no spend)

**Rule** (`trace_demand`: `queued` entries, `TraceOptions.queued_at_start`; commit `24cd78f`). Some discharge-anchored arrivals fall at or before the window start: the green already on at 118 s leaves no red to queue in (§20). Each such vehicle was in the queue when the window opened. It:
- enters at rest at simulated 0 s at the stop line;
- stands behind its lane's initial vehicles, in observed crossing order, re-packed at SUMO's spacing.

A vehicle without a known lane keeps the discharge timing. The rule is an assumption; the vehicles, their lanes and their crossing order are observed.

**Baseline** (record `experiments/records/ngsim-int2-realism-baseline-20261028T135241Z.json`):
- 11 entries are now `queued` (WE.2–8, WN.1–3, WS.0); 51 stay `discharge` and 31 `upstream`;
- valid on all 20 seeds, with reruns identical;
- insertion delay at most 4 s (was 10 s).

**Validation** (record `experiments/records/ngsim-int2-realism-validity-20261028T135241Z/`). Paired crossing lag, simulated minus observed: the range of the per-seed medians, and the pooled share within ±5 s.

| Movement | §19 | Window-start queues |
|---|---|---|
| W through | −1.2 to −0.2 s (41%) | −2.0 to −0.9 s (**74%**) |
| W left | +6.2 to +13.2 s (46%) | **−2.8 to −2.1 s** (53%) |
| W right (3 vehicles) | +167.4 to +168.5 s (33%) | **−1.2 to +0.1 s** (67%) |
| E, N and S movements | (§19) | unchanged within 0.6 s |

Other quantities are unchanged within seed noise: north through stops 30.6%, south through 79.6%, and travel times. In seed 1:
- WN.1–3 now cross 3.6–4.7 s early (they were +186 to +269 s late).
- The remaining west-left lags are the signal-side ones of §20: WN.4–9 at +82 to +172 s and WN.14 at +92 s (WN.22 is not paired).
- WN.10–11, queued behind them, are now +7 s late (they were +13 s).

**Assessment** (`data/geometry/ngsim-lankershim-int2/replay_assessments/ngsim-int2-realism-baseline-20261028T135241Z.json`): **partially validated**, as in §20 except for the west findings above. Nothing simulated on it can be recommended.

**Candidates** (record `experiments/records/ngsim-int2-realism-experiment-20261028T135556Z.json`; 0 trace problems; assessment loaded and checked). All three are complete on every seed. **Selected: none. Recommended: none.**

| Candidate | Mean delay | Mean S queue | Mean N queue | Mean W / E queue | Throughput |
|---|---|---|---|---|---|
| I1 | **+5.40 s [5.13, 5.67] (worsens)** | −4.8 m (Holm-confirmed improvement) | inconclusive | +17.7 / +2.7 m (worsen) | +10.2 veh/h |
| I2 | **+3.29 s [3.13, 3.45] (worsens)** | +1.6 m (worsens) | −1.2 m (Holm-confirmed) | +1.3 / +2.5 m (worsen) | −112 veh/h (worsens) |
| I3 | **+3.74 s [3.57, 3.91] (worsens)** | −0.3 m (Holm-confirmed) | −3.2 m (Holm-confirmed) | +14.1 / +0.5 m (worsen) | −27 veh/h (worsens) |

**Where the extra delay sits.** Seconds of the mean-delay difference, by vehicle group. Each vehicle contributes timeLoss + departDelay, candidate minus baseline, averaged over the 20 seeds; this can be recomputed from the runs' `tripinfo.xml`.

| Candidate | 11 window-start vehicles | Other west | Everyone else |
|---|---|---|---|
| I1 | +2.95 s | +3.91 s | −1.54 s |
| I2 | −0.01 s | +0.26 s | +3.31 s |
| I3 | +2.77 s | +2.82 s | −2.01 s |

I1 and I3 shorten every replayed west green by 6–7 s, never below the 5 s minimum. That includes the first one, which the window edge cuts: its real onset is unknown, so the window-start vehicles lose a green whose real length is unknown. Without those vehicles, I1 and I3 still worsen delay (about +2.4 s and +0.8 s): the verdict does not depend on the edge, but its size does. Changing how candidates map onto the first replayed phase would change the experiment after seeing its result, so it stays an open caveat.

**What changed from §19.** West traffic now starts the window where it was. As a result:
- I1's delay worsening grows from +1.95 to +5.40 s, and its west queue from +7.6 to +17.7 m;
- I3's delay worsening grows from +0.33 to +3.74 s;
- I2 is unchanged.

**Still open:**
- the 8 west lefts in windows the timeline drops or ends early: a new timeline version (§20);
- the candidates' change to the first replayed phase, whose onset is the window edge;
- south over-stopping, and the right turners' stops;
- demand resampling: one realisation only.

## 22. Signal timeline version 2 (2026-10-28, local only, no spend)

**Why.** §20 found 8 west lefts lagging because of two version 1 rules:
- **Throughs only.** Version 1 builds an arm's green from its through crossings alone once they number 20 or more (W has 34). So the west windows served mostly by permissive lefts were dropped: every head shows red over 211–243 s and 322–334 s while WN.4–9 crossed.
- **Lower-bound greens.** It ends each yellow just after the last crossing, so every green is a lower bound.

**Rules** (`ngsim_junction.SignalRevision`, commit `ed20dfa`). Both rules and the acceptance criteria below were fixed before any version 2 result was seen.
- **Permissive lefts count.** An arm with no left head of its own adds its permissive left crossings to its `all` evidence. Here that is only W: E already did, and N and S have left heads.
- **Yellow starts at the last crossing.** A window's yellow starts at its last crossing (rounded up to the grid) instead of ending there. The existing conflict rules still trim it before a conflicting head.

Version 1 is untouched. `scripts/build_ngsim_int2_timeline_v2.py` derives version 2 from the committed world model, and refuses unless version 1's rules reproduce the committed timeline exactly (they do). Record: `data/geometry/ngsim-lankershim-int2/signal_timeline_v2.json` and `signal_estimate_v2.json`.

| Timeline | Version 1 | Version 2 |
|---|---|---|
| W greens | 7, median 9 s | 8, median 14.5 s |
| N / S `all` greens (median) | 46 / 31 s | 49 / 32 s |
| W lefts crossing in green or yellow | 77% | 100% |
| W throughs crossing in green or yellow | 97% | 100% |
| Seconds with conflicting heads both moving | 0 | 0 |
| Known window; representative cycle | [118, 751) s; 99 s cut at 564 s | the same |

**Acceptance criteria (pre-registered) and results.** All five pass.
1. **Version 1 reproduced exactly:** pass.
2. **No conflicting heads:** pass.
3. **Replay baseline valid on all 20 seeds, reruns identical:** pass. Record `experiments/records/ngsim-int2-realism-baseline-20261028T141919Z.json`: 58 `discharge`, 11 `queued` and 24 `upstream` entries; insertion delay at most 4 s.
4. **Over 53% of west lefts within 5 s:** pass, 57%.
5. **North and south through median lags within ±4 s:** pass, −0.1 to +0.2 s and −3.9 to −3.5 s.

**Validation** (record `experiments/records/ngsim-int2-realism-validity-20261028T141919Z/`). Both sides take every observed signal state from version 2. Paired crossing lag: the range of the per-seed medians, and the pooled share within ±5 s.

| Movement | §21 (version 1) | Version 2 |
|---|---|---|
| W left | −2.8 to −2.1 s (53%) | −4.1 to −3.6 s (57%) |
| W through | −2.0 to −0.9 s (74%) | −2.8 to −2.4 s (**87%**) |
| W right | −1.2 to +0.1 s (67%) | −1.1 to +0.3 s (67%) |
| N through | 0.0 to +0.3 s (74%) | −0.1 to +0.2 s (76%) |
| S through | −3.8 to −3.1 s (59%) | −3.9 to −3.5 s (59%) |

| Quantity | Observed | §21 (version 1) | Version 2 |
|---|---|---|---|
| N through, share stopping | 24.1% [19.0, 30.1] | 30.6% | **27.7%** (inside) |
| N through, travel time | 17.2 s [12.3, 23.5] | 18.9 s | 17.5 s |
| S through, share stopping | 66.9% [59.3, 73.7] | 79.6% | 78.2% |
| S through, travel time | 37.0 s [31.0, 40.6] | 40.8 s | 39.0 s |
| N / S right turners, share stopping | 11% / 37% | 44% / 59% | 40% / 55% |
| Queued at the window end, N / S | 16 / 9 | 23 / 14 | 20 / 13 |

**West lefts in seed 1.** Every west left now crosses in its own green, between 12.6 s early and on time. The remaining error is early: WN.4–9 queue before their restored greens, and SUMO discharges them at its saturation headway. The observed permissive lefts went 3–6 s apart, probably waiting for gaps.

**In-sample.** Version 2 is estimated from the same stop-line crossings that the paired lags compare. Better agreement is internal consistency, not an independent check of the signal timing; no signal head or controller log was read.

**Assessment** (`data/geometry/ngsim-lankershim-int2/replay_assessments/ngsim-int2-realism-baseline-20261028T141919Z.json`): **partially validated**. Nothing simulated on this baseline can be recommended.

**Candidates** (record `experiments/records/ngsim-int2-realism-experiment-20261028T142330Z.json`; 0 trace problems; the assessment loaded and checked). All three are complete on every seed. **Selected: none. Recommended: none.**

| Candidate | Mean delay | Mean S queue | Mean N queue | Mean W / E queue | Throughput |
|---|---|---|---|---|---|
| I1 | **−0.75 s [−0.90, −0.60] (Holm-confirmed improvement)** | −3.9 m (Holm-confirmed) | −1.7 m (Holm-confirmed) | +6.0 / +0.8 m (worsen) | −19 veh/h (worsens) |
| I2 | +2.82 s [2.69, 2.95] (worsens) | +1.9 m (worsens) | −1.9 m (Holm-confirmed) | +1.2 / +2.5 m (worsen) | −111 veh/h (worsens) |
| I3 | +3.36 s [3.21, 3.50] (worsens) | −2.3 m (Holm-confirmed) | −2.2 m (Holm-confirmed) | +8.8 / +3.1 m (worsen) | −161 veh/h (worsens) |

I1 is not selectable: it worsens throughput and the west and east queues, and the rule allows no metric to worsen. The candidates also map differently onto the two timelines. On version 2, I3's +4 s on the north-only green applies to no phase, because all 15 such phases are already shorter (on version 1 it applied to 9).

**The verdict depends on the timeline.** I1's mean-delay difference is +5.40 s [5.13, 5.67] on version 1 (§21) but −0.75 s [−0.90, −0.60] on version 2. Both intervals are narrow, yet they exclude each other. The seed intervals measure SUMO's noise for one timeline, not the uncertainty of the timeline estimate, so no single-timeline interval should be read as the uncertainty of a verdict. The assessment says so.

Where I1's delay difference sits on version 2, in seconds of the mean (as in §21):
- the 11 window-start vehicles: +0.47 s;
- other west vehicles: +1.65 s;
- everyone else: −2.99 s.

**Still open:**
- **Timeline uncertainty in verdicts.** It is not yet carried into candidate verdicts: an ensemble of defensible timeline versions, or a bootstrap of the estimate, would be needed before any verdict can be trusted.
- **Permissive-left discharge.** West lefts now cross early.
- **The window-edge phase mapping** (§21).
- **South over-stopping, and the right turners' stops.**
- **Demand resampling:** one realisation only.

## 23. SAM 3.1 on the real camera 2 footage (2026-10-28; paid, approved by the project owner)

**Approval.** The project owner approved three things on 2026-10-28:
- one `store: false` Muse request, at most $0.001 (D9);
- SAM 3.1 on 10 s of camera 2, prompted with `car` then `bus`, at most $0.04;
- CC BY-SA 4.0 on the published overlay (D3).

**D9, retention.** `scripts/api_smoke_test.py --case muse-store-false --send`:
- HTTP 200, request id `ae76c329-f763-4b22-a2e8-2276556a7a83`;
- the response reports **`store: false`**: the parameter is accepted and echoed;
- 15 input and 57 output tokens, **$0.000261**.

The SAM request shape was not changed: whether SAM honours `store` was not tested. Meta's retention terms were not read here.

**Excerpt.** `data/processed/clips/ngsim-lankershim-cam2-0830-t370-d010/` (Git-ignored), ingested as in §11 with `--start 370 --duration 10`:
- clip `4c0895cd84a81758`, 100 frames, 640x480, 10 fps;
- source seconds 370–380, which is trajectory clip seconds 250.3–260.3 (source 119.7 s = 08:30:00.0, §15);
- inside the replay window of §§18–22, during a north green.

**SAM 3.1** (`scripts/run_sam_cam2_excerpt.py … --spend-limit 0.04 --record`; record `experiments/records/ngsim-cam2-sam-20261028T151050Z.json`):

| Prompt | Request id | Frames billed | Observations | Frames with one | Distinct SAM ids | Parser diagnostics | Cost |
|---|---|---|---|---|---|---|---|
| `car` | `bb3683f5-747b-4719-ba62-8324cb5548cb` | 100 | 1,227 | 100 | 16 | 0 | $0.02 |
| `bus` | `8c97f245-9ea7-4795-8248-271e45bed41c` | 100 | 0 | 0 | 0 | 0 | $0.02 |

**Tracks.** Our tracker, with the default policy, built 18 tracks: 9–16 tracked cars per frame, median 11. 1 short track was removed.

**What it misses** (observed by us on frame 50, by eye, not measured):
- many visible vehicles carry no box, above all the roughly 30 queued cars on the right-hand approach, plus two cars turning in the junction;
- the `bus` prompt found nothing, although a long vehicle is visible.

The boxes that are drawn sit on cars. At this resolution (640x480, rooftop, small vehicles) SAM's recall is low. The A3 result below measures both.

**Not used downstream.** The tracks feed nothing:
- the camera 2 calibration was not accepted (ADR-018), so the intersection 2 world model stays trajectory-based (ADR-019);
- there is no comparison with the NGSIM trajectories, because the camera's footprint on the ground would need that calibration.

**Published.**
- `experiments/records/ngsim-cam2-sam-20261028T151050Z/overlay.mp4` and `overlay.json`, under CC BY-SA 4.0, with `NOTICE.md` giving the attribution and the changes.
- The demo page's step 0, which reads them through the pinned manifest `data/demo/ngsim-int2-replay-timeline-v2-sam.json`.
- The raw SAM streams stay in `cache/sam/`, Git-ignored (ADR-009). Committing them would need the same approval as the Muse reply (ADR-020).

**Spend.** This slice cost about $0.0403. The total to date is about **$0.087** over 8 paid calls.

**A3 review sheet** (2026-10-28, no spend). `scripts/build_a3_review_sheet.py` fixed the protocol before anyone marked anything. The sheet is `experiments/records/ngsim-cam2-sam-20261028T151050Z/a3_sheet.json`, sheet id `5bd3638e…`; it holds no pixels.
- **Sample:** every tracked box in frames 0, 20, 40, 60, 80 and 99. That is 73 boxes (16/16/12/11/9/9), 70 of them above image row 410.
- **Each box gets one verdict:** vehicle, not a vehicle, or partial or merged.
- **Per frame:** the motor vehicles at least half visible above row 410 with no box on them.
- **Per track:** the id switches across one crop every 10 frames (18 tracks).
- **Marking:** the reviewer marks on a private review page, whose marks are saved in the page's own database.
- **Scoring:** `scripts/score_a3_review.py` scores the exported marks. It refuses to score unless every box, frame and track carries exactly one mark from this sheet.
- **Scores:**
  - precision = vehicle / all boxes;
  - recall = vehicle boxes above the line / (those + missed);
  - id switches.

  Each comes with a Wilson 95% interval, which is too narrow, because boxes in one frame are not independent.
- **The review images** (frames with numbered boxes, crops) stay in the Git-ignored run directory; they hold CC BY-SA 4.0 pixels.

**Status:** scored (below). A3 is met.

**A3 result** (2026-10-29, no spend). The reviewer marked all 97 items: 73 boxes, 6 frames and 18 tracks. A first export lacked the missed counts for frames 80 and 99, so the scorer refused and wrote nothing; the reviewer then entered them. `scripts/score_a3_review.py`, unchanged, wrote `experiments/records/ngsim-cam2-sam-20261028T151050Z/a3_review.json`, labelled "observed by a person (the reviewer), by eye, on SAM 3.1 output". It holds every mark, the sheet's id and hash, and the scores:
- **precision 1.00** [0.95, 1.00]: all 73 sampled boxes cover one vehicle; none is a non-vehicle, and none is partial or merged;
- **recall 0.28** [0.23, 0.34]: 70 vehicle boxes above row 410, against 176 vehicles there with no box;
- **id switches: 0** in 18 tracks.

| Frame | 0 | 20 | 40 | 60 | 80 | 99 |
|---|---|---|---|---|---|---|
| Boxes | 16 | 16 | 12 | 11 | 9 | 9 |
| Missed above row 410 | 27 | 25 | 29 | 32 | 29 | 34 |

**What it means** (our reading, not a further measurement):
- Where SAM 3.1 draws a box, the box is a car, and it stays on the same car.
- It boxes fewer than a third of the vehicles in view. Its tracks start in the first frame and are rarely replaced (2 new tracks in 10 s), so coverage falls over the clip.
- Counts or queues read from these tracks would undercount several-fold. That is a second reason, besides the rejected calibration (ADR-018), to keep camera 2 out of the world model.

**Limits.**
- The Wilson intervals treat boxes and vehicles as independent. Boxes in one frame are not, so the intervals are too narrow.
- One reviewer counted, by eye, the unboxed vehicles in queues of 25–34. Those counts are approximate.
- It measures this 10 s clip of one camera, not SAM 3.1 in general.

`tests/test_a3_review.py` re-scores the record from its own marks. The demo page's step 0 shows the result under the pinned manifest `data/demo/ngsim-int2-replay-timeline-v2-sam-a3.json`; the earlier manifests still load.

## 24. Queue metrics on the replay: a shared start state and entry censoring (2026-10-29, local only, no spend)

**Question.** Why were the north and west maximum queues identical in every replay run (105.46 m and 54.33 m)? Traced from SUMO's outputs and the compiled networks, then checked a second time.

**Found** (from SUMO's outputs and the compiled networks; simulated, not observed):
- **Not a lane cap.** The approach lanes are 186.02 m (N) and 177.94 m (W) long, in the v1 and v2 baselines alike.
- **The start state.** The values occur only at t = 1 s, once per run, in all 80 runs of each experiment. At t = 2 s they are 37.96 m (N) and 23–35 m (W). Each is the queue built at t = 0 from vehicles placed at rest:
  - N: NE.38 (entry `initial`), placed at departPos 85.56 m. It was observed 83.91 m from the stop line, at rest; the re-packing in trace_demand.py moved it about 16.5 m further back.
  - W: WE.8 (entry `queued`), placed at departPos 128.61 m. In the NGSIM trajectories it was 0.1 m from the stop line moving at 8.9 m/s, and WE.6–8 were all moving at 8.5–9.2 m/s. So the 54.33 m west start queue is constructed, not observed.
- **Shared by every run.** Each candidate's signal program first differs from the baseline's at 5–9 s (I1, I3) or 41 s (I2); queue.xml rows agree until 1–2 s later. The maximum is set before any plan acts.
- **Entry censoring.** North and south vehicles enter where they were first seen, 78–99 m upstream of the stop line. A vehicle that cannot be inserted waits off the network (departDelay) and is not in queue.xml. In the version 2 experiment, rules 0.3.0 (superseded by 0.3.1; see below) counted only waits while a standing queue was at the entry:

  | Candidate | N, veh·s (seeds) | S, veh·s (seeds) |
  |---|---|---|
  | I1 | 22 (11) | 0 |
  | I2 | 599 (20) | 73 |
  | I3 | 964 (20) | 0 |

  Under 0.3.0 the baseline showed none, and on version 1 I1 had 7 veh·s on N (3 seeds), I2 874 on N and 65 on S, and I3 none. **Correction (rules 0.3.1, ADR-024 amendment):** the independent review found that 0.3.0 missed about half of the waiting, because queue.xml counts standing vehicles only. Counted from insertion delays, the baseline also waits on the north approach (82 veh·s over 20 seeds on version 2), so "the baseline has none" was wrong.
- **The lane-full rule (rules 0.2.0)** fired in no run. Neither problem is near a lane's end.

**Consequences:**
- North and west maximum queues on the replay are uninformative: they cannot detect harm there. The mean queues remain informative, because the shared steps cancel exactly in a paired mean.
- Candidates' north mean-queue improvements were Holm-confirmed on vehicles held out of view. On version 1, the hidden queue could be larger than I2's measured gain, so that confirmation was not supported. Under rules 0.3.0 such claims are untested, and asymmetric holding blocks selection (ADR-024).
- **Found in passing, not changed:**
  - The replay's constructed start queues (the north re-packing and the west queue of moving vehicles) are a further window-edge limitation of the replay baseline, beside §§20–21.
  - With a 0 s warm-up (ADR-022), that state lies inside the measurement window.
  - The replay assessments' status is unchanged: partially validated.

**Evidence:** experiments/runs/ngsim-int2-realism-experiment-20261028T{142330,135556}Z (Git-ignored queue.xml, tripinfo.xml and networks); tests/test_queue_disposition.py.

## 25. The signal-timeline ensemble (2026-10-29, local only, no spend)

**Question.** Does any recorded candidate's verdict hold across every signal timeline the evidence supports (ADR-025)?

**Design**, committed before any run on the new members (2c545fb; `data/geometry/ngsim-lankershim-int2/timeline_ensemble.json`). Four required members, the corners of the two rules the trajectories cannot settle: T1 = version 1, T2 = version 2, T3 = v2a (permissive lefts counted, yellow ending at the last crossing), T4 = v2b (not counted, yellow starting there).
- Excluded variants, each with its evidence: start-up lost time, the service gap, the minimum crossings per window, the synthetic constants, and filling red gaps.
- The version 1 and 2 results were already known when the design was written; the design record says so.

**Gate** (checked by the build and run scripts):
- T3 and T4 pass G1–G4: v1 and v2 are reproduced byte for byte, there are 0 conflicting seconds, and the window is [118, 751) s.
  - West lefts shown red: 0/23 on T3 and 5/23 on T4, against 6/23 on version 1.
  - Every other through and left movement is unchanged.
- **G5:** both baselines are valid on seeds 1–20 (`experiments/records/ngsim-int2-realism-baseline-20261029T115656Z.json` for T3 and `…115705Z.json` for T4), and reruns are identical.
- T3 and T4 are not assessed against observations. T1 and T2 keep their partially validated assessments.

**Results** (simulated, 20 paired seeds per member, 0 trace problems):
- **Current:** rules 0.3.1 (the ADR-024 amendment, made after the independent review and disclosed in the record); records `ngsim-int2-realism-experiment-20261029T{122910,122930,122948,123007}Z.json` and `ngsim-int2-timeline-ensemble-20261029T123045Z.json`.
- **Superseded:** the first run under the pre-registered rules 0.3.0 (`…20261029T{115716,115735,115755,115813}Z.json`, `ngsim-int2-timeline-ensemble-20261029T115904Z.json`) gave the same verdicts and stays committed.

| Mean delay, candidate − baseline, s [95%] | T1 (v1) | T2 (v2) | T3 (v2a) | T4 (v2b) |
|---|---|---|---|---|
| I1 | +5.40 [5.13, 5.67] | **−0.75 [−0.90, −0.60]** | +0.81 [0.58, 1.03] | +2.31 [2.18, 2.43] |
| I2 | +3.29 [3.13, 3.45] | +2.82 [2.69, 2.95] | +3.36 [3.20, 3.51] | +2.86 [2.68, 3.04] |
| I3 | +3.74 [3.57, 3.91] | +3.36 [3.21, 3.50] | +5.72 [5.41, 6.02] | +1.82 [1.66, 1.98] |

- **Cross-timeline verdict:** `no_support` for I1, I2 and I3. None is eligible on any member: each worsens at least one metric on every member. There is no robust selection, and nothing is recommended.
- **I1:** improves delay only on T2. Its robust harm (worse on every member) is the east maximum and mean queues and the west mean queue.
- **I2:** robust harm in delay, throughput, the east and south maximum queues, and the east and south mean queues.
- **I3:** not the same plan on T2 and T4, where its north-only change applies to no phase, as the design record predicted from the phase mapping alone. Its robust harm is in delay, throughput, and the east and west mean queues.
- **Disagreements are listed per metric in the record.** For example, I3's east maximum queue worsens on T1–T3 and improves on T4, a direction flip.
- **Censoring (rules 0.3.0):** the north maximum queue is uninformative on every member, and the west on most.
  - Vehicles waiting to enter, vehicle-seconds in the window over 20 seeds (rules 0.3.1, from tripinfo insertion delays). An asterisk marks an excess over the baseline that is significant (paired 95%) and blocks the candidate.

    | Run | T1 | T2 | T3 | T4 |
    |---|---|---|---|---|
    | Baseline (N) | 79 | 82 | 73 | 98 |
    | I1 (N) | 210* | 37 | 45 | 14 |
    | I2 (N / S) | 1333* / 87* | 1087* / 85* | 1327* / 86* | 1131* / 82* |
    | I3 (N / S) | 0 | 1334* | 389* | 3 / 2 |

  - North queue claims are untested on every member, because the baseline itself waits there.
- **Reproducibility (checked):** T1 and T2 reuse the recorded baseline directories; their candidates were re-simulated, and the tripinfo is identical to the 2026-10-28 records. The ensemble runner refuses unless it is, and unless each `evaluation.json` equals a fresh recomputation. An independent reviewer recomputed T1 I1's and T3 I3's delay from tripinfo and matched.

**Reading.** The version 2 delay gain of I1 is a property of that one timeline estimate, not of the plan: on the three other defensible timelines, I1 worsens delay. The ensemble turns the documented flip (ADR-023) into a handled result. No recorded candidate is supported. The four corners bracket two rule choices; they are not a probability distribution, and other uncertainty (constants, window edges, one demand realisation, south over-stopping) is still not propagated.

## 26. A synthetic known-answer test of the evaluation and selection stage (2026-10-29, local only, no spend)

**SYNTHETIC.** The junction, its demand, its signal plans and every candidate are written in code (kind `assumed`), and only the outcomes are simulated by SUMO. This section says nothing about NGSIM intersection 2 or about any real junction. No proposer runs and Muse is not called, so this is not a test of discovery.

**Question.** When a known better plan is among the candidates, does the normal evaluation and selection stage measure it, rank it, and select it? And does it refuse the candidates it should refuse? (`cairoflow_evaluation.known_answer`, `scripts/run_synthetic_known_answer.py`, ADR-026.)

**Design** (fixed in code at commit 49adee3; each run's pre-registration hashed before SUMO started and unchanged after):
- **Junction:** four arms, one through-only lane each. Two-phase fixed-time plans with 3 s yellow and 2 s all-red. 20 paired seeds; warm-up 300 s, window 900 s, drain 600 s.
- **Expected correction:** Webster, computed before simulation from the designed demand and an assumed saturation flow of 1,800 veh/h per lane. It is insensitive to 1,500–2,000 veh/h and to lost time of 3–7 s, which is recorded.
- **Candidates, ids shuffled,** all through the normal validator, `run_candidates`, `evaluate_experiment`, `assemble_report` (rules 0.3.1) and `trace_report`:
  - the correction;
  - a half-way plan;
  - an exact null: the baseline plan, which the validator rejects, as pre-registered;
  - the mirror of the correction: the closest admissible plan;
  - 8 random perturbations of the bad plan, each green moved by an integer in [−5, +5] s (seed 20261029).
- **Scenario 1, reallocation:**
  - Demand: E-W 300 veh/h, N-S 150 veh/h per approach.
  - Bad plan: E-W 20 s / N-S 40 s on a 70 s cycle.
  - Correction: 40 / 20 s at the same cycle.
  - Pre-registered: not selectable, because the axis that loses green must queue longer.
- **Scenario 2, long-cycle:**
  - Demand: E-W 450 veh/h, N-S 300 veh/h per approach.
  - Bad plan: 84 / 56 s on a 150 s cycle; the split is right, the cycle far too long.
  - Correction: Webster's optimum cycle, 35 s, with greens of 15 / 10 s.
  - Pre-registered: better on every approach.
  - **Designed after seeing scenario 1's result**, to separate "can the stage select a known improvement" from "the rule refuses trade-offs". This is disclosed in its pre-registration.

**Results** (simulated; records `experiments/records/synthetic-known-answer-reallocation-20261029T123315Z.json` and `…-long-cycle-20261029T123354Z.json`; run at commit 49adee3 with clean imported files; 0 trace problems):

| | Reallocation | Long-cycle |
|---|---|---|
| Correction, mean delay, s [95%] | −5.73 [−6.53, −4.93] (Holm-confirmed) | −14.96 [−15.89, −14.04] (Holm-confirmed) |
| Correction, N mean queue, m | +2.93 [2.48, 3.38], worsens | −13.97 [−15.24, −12.69] |
| Correction, E mean queue, m | −8.24 [−9.24, −7.24] | −13.36 [−15.02, −11.70] |
| Correction selected? | **No**: it worsens the N/S queues, as pre-registered | **Yes** |
| Half-way plan | delay −3.88 s; refused for the same reason | delay −8.80 s; eligible, ranked second |
| Exact null | rejected by the validator; never simulated | same |
| Mirror | simulated, incomplete (oversaturated); not selected | simulated; worsens delay and throughput; not selected |
| Random perturbations with a Holm-confirmed improvement | 1 of 8 (in the direction the model predicts) | 0 of 8 |
| False positives (confirmed against the pre-registered direction) | 0 of 16 (95% upper bound 0.19) | 0 of 7 (95% upper bound 0.43) |
| Agreement with the uncalibrated Webster model | 65 of 96 predicted comparisons; 0 opposite | 50 of 99; 0 opposite |

**Censoring under rules 0.3.1.** In the baselines, some vehicles waited to enter on several arms, and in long-cycle the E lane filled on seeds 2 and 8. Those arms' queue claims are untested for every candidate. That is why, in long-cycle, only mean delay is a confirmed improvement although the correction's queue intervals all exclude zero.

**Reading.**
- **The stage works on a known case.** It measures a known correction correctly, ranks it first, and selects it when it harms nothing (long-cycle). It refuses it when it trades one road's queue for another's (reallocation), because the selection rule allows no worsening and has no agreed harm margins (ADR-021). That refusal is the rule working as written, not a failure to find the improvement.
- **The controls behave as expected:** the null is rejected, the mirror is never selected, and no confirmed improvement contradicts its predicted direction.
- **The test's power is limited:**
  - the long-cycle correction moves delay by 15 s while the perturbations move it by at most about 1.5 s;
  - the false-positive bound rests on one draw of 8 perturbations;
  - the Webster model is uncalibrated: the simulated baseline delay is 21.2 s against 18.5 s analytical in reallocation, and 30.6 s against 27.0 s in long-cycle.
- **Earlier runs, kept locally and not committed:** one before the review (rules 0.3.0) and one on a tree whose rules files were changing. Both are in their run directories, and their simulated results for the correction and the half-way plan match these records.

## 27. Detector weights, read at run time (2026-11-04)

Neither file is in this repository. Each is downloaded when its detector runs and checked against the SHA-256 in `cairoflow_perception.local.detectors`. These rows were added after the bake-off used the weights, not before it.

| Input | Hugging Face repository | Licence | Use |
|---|---|---|---|
| D-FINE Large | `ustc-community/dfine-large-obj2coco-e25` | Apache-2.0 (V, model record `cardData.license`, read 2026-11-04) | Default, the `vision` extra |
| VisDrone YOLO26s | `dronefreak/visdrone-yolo26s` | AGPL-3.0 (V, model record `cardData.license`, read 2026-11-04) | Opt-in, the `vision-agpl` extra; see NOTICE section 5 |
