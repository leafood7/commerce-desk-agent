import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_rag_long_scale.py"


@pytest.mark.parametrize(
    "args",
    [
        ["--case-id", "sealed-unknown"],
        ["--case-id", "long-dev-001", "--case-id", "long-dev-001"],
        ["--version", "invented"],
        ["--tier", "999"],
        ["--tier", "12", "--tier", "12"],
    ],
)
def test_invalid_selection_fails_before_output_or_model_load(tmp_path, args):
    output = tmp_path / "must-not-exist"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--output", str(output), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=20,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not output.exists()


def test_validate_and_refuse_output_overwrite(tmp_path):
    output = tmp_path / "retained"
    output.mkdir()
    marker = output / "do-not-change.txt"
    marker.write_text("retained")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert marker.read_text() == "retained"
    assert list(output.iterdir()) == [marker]
