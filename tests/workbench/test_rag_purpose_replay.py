import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("identifiers", [["unknown"], ["qa-001", "qa-001"]])
def test_replay_rejects_invalid_task_before_output(tmp_path, identifiers):
    output = tmp_path / "must-not-exist"
    args = [sys.executable, str(ROOT / "scripts/evaluate_rag_purpose.py"), "--output", str(output)]
    for identifier in identifiers:
        args += ["--task-id", identifier]
    result = subprocess.run(
        args, capture_output=True, text=True, env={**os.environ, "PYTHONUTF8": "1"}, timeout=20
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not output.exists()


def test_replay_small_pair_shows_loss_without_new_retrieval(tmp_path):
    output = tmp_path / "replay"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/evaluate_rag_purpose.py"),
            "--output",
            str(output),
            "--task-id",
            "qa-032",
            "--task-id",
            "app-009",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert (
        summary["rows"] == 36 and summary["new_model_calls"] == summary["new_retrieval_calls"] == 0
    )
    rows = [
        json.loads(line)
        for line in (output / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    qa = [
        r
        for r in rows
        if r["task_id"] == "qa-032"
        and r["tier"] == 180
        and r["retrieval_arm"] == "scoped_references"
    ]
    assert next(r for r in qa if r["strategy"] == "forced_application")["metrics"][
        "reviewed_evidence_newly_lost"
    ]
    assert not next(r for r in qa if r["strategy"] == "purpose_routed")["metrics"][
        "reviewed_evidence_newly_lost"
    ]
