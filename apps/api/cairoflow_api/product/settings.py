"""Where the product backend reads and writes, and how it reaches the vision worker.

Everything hangs off one repository root, so a test can point the whole backend at a temporary
directory. `CAIROFLOW_VISION_PYTHON` and `CAIROFLOW_VISION_WORKER` configure the worker; `var/` is
scratch state the backend can rebuild, and it marks itself ignored by Git when it creates itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VISION_PYTHON = "~/.venvs/cairoflow-vision/bin/python"
DEFAULT_WORKER = "scripts/cv_run.py"
MAX_UPLOAD_BYTES = 2 * 1024**3
SITE_ID = "ngsim-lankershim-int2"


@dataclass(frozen=True)
class ProductSettings:
    repo_root: Path
    vision_python: Path
    worker_script: Path
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    job_poll_s: float = 0.2
    cancel_grace_s: float = 5.0
    ffprobe: str = "ffprobe"

    @classmethod
    def from_env(cls, repo_root: Path, environ: Mapping[str, str] = os.environ) -> ProductSettings:
        root = repo_root.resolve()
        python = environ.get("CAIROFLOW_VISION_PYTHON") or DEFAULT_VISION_PYTHON
        worker = Path(environ.get("CAIROFLOW_VISION_WORKER") or DEFAULT_WORKER).expanduser()
        return cls(
            repo_root=root,
            vision_python=Path(python).expanduser(),
            worker_script=worker if worker.is_absolute() else root / worker,
        )

    @property
    def var_dir(self) -> Path:
        return self.repo_root / "var"

    @property
    def db_path(self) -> Path:
        return self.var_dir / "cairoflow.db"

    @property
    def jobs_dir(self) -> Path:
        return self.var_dir / "jobs"

    @property
    def sample_analyses_dir(self) -> Path:
        return self.repo_root / "data/sample/analyses"

    @property
    def analyses_dir(self) -> Path:
        return self.repo_root / "data/analyses"

    @property
    def uploads_dir(self) -> Path:
        return self.repo_root / "data/uploads"

    @property
    def clips_dir(self) -> Path:
        return self.repo_root / "data/processed/clips"

    @property
    def camera_source(self) -> Path:
        return (
            self.repo_root
            / "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi"
        )

    @property
    def camera_calibration(self) -> Path:
        """A committed record of the camera 2 calibration work, naming the video it is for."""
        return self.repo_root / "data/geometry/ngsim-lankershim-cam2/v2/cross_clicks.json"

    @property
    def dist_dir(self) -> Path:
        return self.repo_root / "apps/web/dist"

    @property
    def site_geometry(self) -> Path:
        return self.repo_root / "data/geometry/ngsim-lankershim-int2/geometry.json"

    @property
    def image_geometry(self) -> Path:
        return self.repo_root / "data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json"

    @property
    def corridor_summary(self) -> Path:
        return self.repo_root / "data/sample/corridor_summary.json"

    @property
    def decision_summary(self) -> Path:
        return self.repo_root / "data/sample/decision_summary.json"


def ignored_directory(path: Path) -> Path:
    """Create a directory of local state and keep its contents out of Git."""
    path.mkdir(parents=True, exist_ok=True)
    marker = path / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", encoding="utf-8")
    return path
