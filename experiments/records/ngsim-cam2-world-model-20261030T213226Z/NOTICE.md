# Camera 2 tracks placed in the intersection 2 world model (CC BY-SA 4.0)

`tracks.json` holds SAM 3.1 boxes on NGSIM Lankershim camera 2 footage (source seconds 370-380), turned into tracks by CairoFlow's procedure; `camera_layer.json` places them in the world model's frame and lanes. Both derive from the camera 2 video, U.S. Department of Transportation Federal Highway Administration. (2016). Next Generation Simulation (NGSIM) Program Lankershim Boulevard Videos. [Dataset]. Provided by ITS DataHub through Data.transportation.gov. Accessed 2026-10-27 from http://doi.org/10.21949/1504477. Licensed CC BY-SA 4.0. More: https://ops.fhwa.dot.gov/trafficanalysistools/ngsim.htm They are shared under CC BY-SA 4.0, as the overlay (decision D3). Model output and our placement, not ground truth.

`observations.json` (the SAM 3.1 boxes before tracking, same licence) and `source_clip.json` (the
excerpt's manifest: hashes, frame times and the FFmpeg command, no pixels) are kept here too, so a
fresh clone can rerun the gate: `scripts/fetch_ngsim_data.py --restore-camera-inputs` copies all
three back to the Git-ignored paths the gate pins, checking each hash.
