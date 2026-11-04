"""The ingest CLI (scripts/ingest_clip.py) on a synthetic source, and the committed licences."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import ClipLicense, SourceClip
from cairoflow_perception.ingest import IngestError

REPO = Path(__file__).resolve().parents[1]
NGSIM_LICENCE = REPO / "data" / "licences" / "ngsim-lankershim-videos.json"


def _cli():
    spec = importlib.util.spec_from_file_location("ingest_cli", REPO / "scripts" / "ingest_clip.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_ngsim_licence_record_is_valid_and_share_alike() -> None:
    licence = ClipLicense.model_validate_json(NGSIM_LICENCE.read_text("utf-8"))
    assert licence.licence_url == "https://creativecommons.org/licenses/by-sa/4.0/legalcode"
    assert licence.share_alike and not licence.synthetic
    assert "uv3e-y54k" in licence.source_url
    assert "Federal Highway Administration" in licence.attribution


def test_cli_ingests_a_window_and_never_overwrites(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
            *("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25", "-t", "5"),
            *("-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    licence = json.loads(NGSIM_LICENCE.read_text("utf-8")) | {
        "source_id": "synthetic-testsrc2",
        "synthetic": True,
    }
    licence_path = tmp_path / "licence.json"
    licence_path.write_text(json.dumps(licence), "utf-8")
    args = [str(source), "--licence", str(licence_path), "--out", str(tmp_path / "clip"),
            "--start", "1", "--duration", "2"]  # fmt: skip
    assert _cli().main(args) == 0
    assert "20 frames" in capsys.readouterr().out
    manifest = SourceClip.model_validate_json((tmp_path / "clip/source_clip.json").read_text())
    assert (manifest.trim.start_s, manifest.trim.duration_s) == (1, 2)
    assert manifest.licence.source_id == "synthetic-testsrc2"
    with pytest.raises(IngestError, match="refusing to overwrite"):
        _cli().main(args)
