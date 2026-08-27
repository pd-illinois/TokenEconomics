"""Tests for reproducible test-runner source provenance."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"
SPEC = spec_from_file_location("future_token_predictor_test_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
RUNNER = module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_capture_pins_nested_source_first(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return Completed()

    monkeypatch.setattr(RUNNER.subprocess, "run", fake_run)

    RUNNER.capture(["python", "-m", "pytest"])

    source = str(RUNNER.ROOT / "src")
    path_entries = captured["env"]["PYTHONPATH"].split(RUNNER.os.pathsep)
    assert path_entries[0] == source
