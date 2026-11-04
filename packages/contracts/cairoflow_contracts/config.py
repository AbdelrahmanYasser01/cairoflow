"""Runtime configuration from the process environment and an optional .env file.

The API key is held as a SecretStr so it never appears in repr(), logs or tracebacks.
Model names and the base URL stay configurable because the hosted API may change them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr

_ENV_TO_FIELD: dict[str, str] = {
    "MODEL_API_KEY": "model_api_key",
    "META_BASE_URL": "meta_base_url",
    "MUSE_MODEL": "muse_model",
    "SAM_MODEL": "sam_model",
    "DATABASE_URL": "database_url",
    "SUMO_HOME": "sumo_home",
}


class Settings(BaseModel):
    """Validated, immutable application settings."""

    model_config = ConfigDict(frozen=True)

    model_api_key: SecretStr | None = None
    meta_base_url: str = "https://api.meta.ai/v1"
    muse_model: str = "muse-spark-1.3"
    sam_model: str = "sam-3.1"
    database_url: str = "sqlite:///./cairoflow.db"
    sumo_home: str | None = None

    @property
    def has_api_key(self) -> bool:
        """True when a non-empty API key is configured. Never exposes the key itself."""
        return self.model_api_key is not None and bool(self.model_api_key.get_secret_value())


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Blank lines, comments and malformed lines are skipped."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(
    env_file: Path | None = Path(".env"),
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings; the process environment overrides the .env file.

    Empty values are treated as unset so a blank template entry cannot erase a default
    or a value from a lower-priority source.
    """
    environment = os.environ if environ is None else environ
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update(parse_dotenv(env_file.read_text(encoding="utf-8")))
    merged.update(
        {key: value for key, value in environment.items() if key in _ENV_TO_FIELD and value != ""}
    )
    fields = {
        _ENV_TO_FIELD[key]: value
        for key, value in merged.items()
        if key in _ENV_TO_FIELD and value != ""
    }
    return Settings(**fields)
