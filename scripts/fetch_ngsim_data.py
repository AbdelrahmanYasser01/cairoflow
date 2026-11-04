"""Fetch and verify the NGSIM Lankershim inputs a fresh clone needs (P0-5); nothing is substituted.

    uv run --frozen python scripts/fetch_ngsim_data.py --check                   # no network
    uv run --frozen python scripts/fetch_ngsim_data.py --download trajectories   # about 54 MB
    uv run --frozen python scripts/fetch_ngsim_data.py --download camera2        # about 449 MB
    uv run --frozen python scripts/fetch_ngsim_data.py --restore-camera-inputs   # no network

A file is accepted only if its SHA-256 equals the pin recorded at its first download
(DATA_REGISTER.md §§10, 13). A download that differs stays as `<name>.part`, is reported with the
hash it has, and is not used; an existing file is never overwritten.

- trajectories: U.S. DOT FHWA NGSIM, data.transportation.gov table 8ect-6jqj, CC BY-SA 3.0. The
  query of 2026-10-27: Lankershim, 08:30-08:45 PDT, ordered by time and vehicle, 50,000 rows a
  page (DATA_REGISTER.md §13). Then: scripts/build_ngsim_int2_scenario.py rebuilds the world
  model and baseline, scripts/build_ngsim_int2_realism.py --timeline v2 the replay baseline.
- camera2: NGSIM Lankershim Videos (uv3e-y54k, CC BY-SA 4.0), only the camera 2 08:30 member, by
  byte range, inflated and checked against the archive's CRC32 (§10). Then scripts/ingest_clip.py
  for the excerpts (their re-encoding depends on the FFmpeg build, so clip hashes may differ).
- restore-camera-inputs: the SAM tracks, observations and clip manifest the camera gate pins
  (P1-1), copied from their committed record into the Git-ignored paths the gate names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zlib
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
TRAJECTORIES = {
    "path": "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv",
    "sha256": "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e",
    "rows": 616749,
    "url": "https://data.transportation.gov/resource/8ect-6jqj.csv",
    "select": "vehicle_id,frame_id,global_time,global_x,global_y,v_length,v_width,lane_id,"
    "int_id,section_id,direction,movement",
    "where": "location='lankershim' AND global_time >= 1118935800000 AND global_time < "
    "1118936700000",
    "order": "global_time,vehicle_id",
    "page": 50000,
}
CAMERA2 = {
    "path": "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi",
    "sha256": "04ef7435be3a107e7321900c384bb1ee7703ce753a122559cd2937fef341f530",
    "url": "https://data.transportation.gov/download/uv3e-y54k/application%2Fx-zip-compressed",
    "range": (2449745454, 2898928787),
    "range_sha256": "11f84d3821578b21e0a8a83a1d1971f1edac5dd74629100c1f71a83eb818ae2a",
    "local_header_bytes": 138,
    "crc32": 0xF8BE5340,
}
CAMERA_RECORD = REPO / "experiments/records/ngsim-cam2-world-model-20261030T213226Z"
GATE = REPO / "data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate.json"
RESTORED = {"sam_tracks": "tracks.json", "sam_observations": "observations.json",
            "camera_clip_manifest": "source_clip.json"}  # fmt: skip


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


MISSING, DIFFERS = 3, 1  # exit codes of --check; a file that differs outranks a missing one


def _worse(status: int, found: int) -> int:
    return DIFFERS if DIFFERS in (status, found) else max(status, found)


def check() -> int:
    """Print each input's state; 0 all ok, 3 something missing, 1 something differs."""
    status = 0
    for name, pin in (("trajectories", TRAJECTORIES), ("camera2", CAMERA2)):
        path = REPO / pin["path"]
        if not path.is_file():
            print(f"{name}: missing ({pin['path']}); --download {name}")
            status = _worse(status, MISSING)
        elif sha256_file(path) != pin["sha256"]:
            print(f"{name}: {pin['path']} does NOT match its pin")
            status = _worse(status, DIFFERS)
        else:
            print(f"{name}: ok")
    gate = json.loads(GATE.read_text(encoding="utf-8"))["inputs"]
    for name in RESTORED:
        path = REPO / gate[name]["path"]
        if not path.is_file():
            state, status = "missing; --restore-camera-inputs", _worse(status, MISSING)
        elif sha256_file(path) != gate[name]["sha256"]:
            state, status = "does NOT match", _worse(status, DIFFERS)
        else:
            state = "ok"
        print(f"camera gate input {name}: {state}")
    return status


def _accept(part: Path, target: Path, sha256: str) -> int:
    found = sha256_file(part)
    if found != sha256:
        print(f"{part} has SHA-256 {found}, the pin is {sha256}: kept as .part, not used",
              file=sys.stderr)  # fmt: skip
        return 1
    part.rename(target)
    print(f"accepted {target.relative_to(REPO)}")
    return 0


def download_trajectories(client: httpx.Client) -> int:
    target = REPO / TRAJECTORIES["path"]
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with part.open("x", encoding="utf-8", newline="") as out:
        for offset in range(0, 10**7, TRAJECTORIES["page"]):
            params = {"$select": TRAJECTORIES["select"], "$where": TRAJECTORIES["where"],
                      "$order": TRAJECTORIES["order"], "$limit": TRAJECTORIES["page"],
                      "$offset": offset}  # fmt: skip
            response = client.get(TRAJECTORIES["url"], params=params)
            response.raise_for_status()
            lines = response.text.splitlines(keepends=True)
            out.writelines(lines if offset == 0 else lines[1:])  # one header
            rows += len(lines) - 1
            if len(lines) - 1 < TRAJECTORIES["page"]:
                break
    if rows != TRAJECTORIES["rows"]:
        print(f"got {rows} rows, the pin is {TRAJECTORIES['rows']}", file=sys.stderr)
    return _accept(part, target, TRAJECTORIES["sha256"])


def download_camera2(client: httpx.Client) -> int:
    target = REPO / CAMERA2["path"]
    member = target.with_name("camera2-0830.member.range")
    part = member.with_name(member.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    first, last = CAMERA2["range"]
    with (
        part.open("xb") as out,
        client.stream(
            "GET", CAMERA2["url"], headers={"Range": f"bytes={first}-{last}"}
        ) as response,
    ):
        if response.status_code != 206:
            print(f"expected 206 Partial Content, got {response.status_code}", file=sys.stderr)
            return 1
        for block in response.iter_bytes(1 << 20):
            out.write(block)
    if _accept(part, member, CAMERA2["range_sha256"]):
        return 1
    inflater, crc = zlib.decompressobj(-15), 0
    avi_part = target.with_name(target.name + ".part")
    with member.open("rb") as source, avi_part.open("xb") as out:
        source.seek(CAMERA2["local_header_bytes"])
        for block in iter(lambda: source.read(1 << 20), b""):
            data = inflater.decompress(block)
            crc = zlib.crc32(data, crc)
            out.write(data)
        tail = inflater.flush()
        crc = zlib.crc32(tail, crc)
        out.write(tail)
    if crc != CAMERA2["crc32"]:
        print(f"inflated member CRC32 {crc:08x}, the archive says {CAMERA2['crc32']:08x}",
              file=sys.stderr)  # fmt: skip
        return 1
    return _accept(avi_part, target, CAMERA2["sha256"])


def restore_camera_inputs() -> int:
    gate = json.loads(GATE.read_text(encoding="utf-8"))["inputs"]
    status = 0
    for name, file in RESTORED.items():
        source, target = CAMERA_RECORD / file, REPO / gate[name]["path"]
        if target.is_file():
            print(f"{name}: present, left as it is")
            continue
        if sha256_file(source) != gate[name]["sha256"]:
            print(f"{name}: the committed copy does not match the gate's pin", file=sys.stderr)
            status = 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        print(f"{name}: restored {gate[name]['path']}")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--download", choices=("trajectories", "camera2"))
    action.add_argument("--restore-camera-inputs", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.restore_camera_inputs:
        return restore_camera_inputs()
    target = REPO / (TRAJECTORIES if args.download == "trajectories" else CAMERA2)["path"]
    if target.exists():
        print(f"{target.relative_to(REPO)} exists; run --check", file=sys.stderr)
        return 2
    timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        if args.download == "trajectories":
            return download_trajectories(client)
        return download_camera2(client)


if __name__ == "__main__":
    raise SystemExit(main())
