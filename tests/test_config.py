from pathlib import Path

import pytest
from pydantic import ValidationError

from cairoflow_contracts.config import Settings, load_settings, parse_dotenv

SYNTHETIC_KEY = "test-key-123"


def test_defaults_when_nothing_is_configured(tmp_path: Path) -> None:
    settings = load_settings(env_file=tmp_path / "missing.env", environ={})
    assert settings.meta_base_url == "https://api.meta.ai/v1"
    assert settings.muse_model == "muse-spark-1.3"
    assert settings.sam_model == "sam-3.1"
    assert settings.sumo_home is None
    assert not settings.has_api_key


def test_dotenv_parsing_skips_comments_blanks_and_malformed_lines() -> None:
    text = '# comment\n\nMODEL_API_KEY="abc"\nMUSE_MODEL = muse-spark-1.2\nnot a pair\nSUMO_HOME=\n'
    assert parse_dotenv(text) == {
        "MODEL_API_KEY": "abc",
        "MUSE_MODEL": "muse-spark-1.2",
        "SUMO_HOME": "",
    }


def test_environment_overrides_dotenv_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MUSE_MODEL=muse-spark-1.1\nSAM_MODEL=sam-file\n", encoding="utf-8")
    settings = load_settings(env_file=env_file, environ={"MUSE_MODEL": "muse-spark-1.3"})
    assert settings.muse_model == "muse-spark-1.3"
    assert settings.sam_model == "sam-file"


def test_empty_values_do_not_erase_defaults_or_file_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MUSE_MODEL=muse-spark-1.2\nSUMO_HOME=\n", encoding="utf-8")
    settings = load_settings(env_file=env_file, environ={"MUSE_MODEL": ""})
    assert settings.muse_model == "muse-spark-1.2"
    assert settings.sumo_home is None


def test_api_key_never_appears_in_repr_str_or_json_dump() -> None:
    settings = load_settings(env_file=None, environ={"MODEL_API_KEY": SYNTHETIC_KEY})
    assert settings.has_api_key
    assert SYNTHETIC_KEY not in repr(settings)
    assert SYNTHETIC_KEY not in str(settings)
    assert SYNTHETIC_KEY not in settings.model_dump_json()


def test_settings_are_immutable() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.muse_model = "other"  # type: ignore[misc]
