"""The first intersection 2 runner builds only the prompt versions whose requests it can send
(ADR-032): prompt 0.3.1 names scripts/run_cairoflow.py's corrected evidence limitations, so this
runner refuses it instead of sending its camera-free limitations under that label."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _script():
    path = REPO / "scripts/run_ngsim_int2_experiment.py"
    spec = importlib.util.spec_from_file_location("run_ngsim_int2_experiment", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["run_ngsim_int2_experiment"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    """The runner with every step after the version check made to fail loudly: the scenario
    record points nowhere, and building a bundle, settings or a Muse client raises."""
    module = _script()

    def forbidden(*args, **kwargs):
        raise AssertionError("nothing may be built for a refused prompt version")

    for name in ("MuseClient", "load_settings", "build_evidence_bundle", "load_baseline"):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(module, "RECORD", REPO / "no-such-scenario-record.json")
    return module


def test_prompt_0_3_1_is_refused_with_the_runner_to_use(run, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run.main(["--prompt-version", "0.3.1"])
    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "prompt 0.3.1 is not built by this runner" in error
    assert "Use scripts/run_cairoflow.py for prompt 0.3.1" in error


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.3.0", None])
def test_the_versions_it_builds_pass_the_check_as_before(run, version) -> None:
    """Past the check, the first step reads the scenario record, which points nowhere here."""
    argv = [] if version is None else ["--prompt-version", version]
    with pytest.raises(FileNotFoundError, match="no-such-scenario-record"):
        run.main(argv)


def test_its_versions_are_the_registered_ones_but_0_3_1() -> None:
    run = _script()
    assert run.PROMPT_VERSIONS == ("0.1.0", "0.2.0", "0.3.0")
    assert run.PROMPT_VERSION == "0.3.0"  # its default, unchanged
    assert set(run.PROMPTS) - set(run.PROMPT_VERSIONS) == {"0.3.1"}


@pytest.mark.parametrize("version", ["9.9.9", ""])
def test_unknown_versions_are_refused(run, version) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run.main(["--prompt-version", version])
    assert exit_info.value.code == 2
