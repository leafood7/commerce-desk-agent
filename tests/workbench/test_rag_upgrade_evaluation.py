"""Input boundaries for the new frozen mechanical replay."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "rag_upgrade", ROOT / "scripts/evaluate_rag_upgrade.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
DATA = ROOT / "data/evaluation/rag_upgrade_v1"
BASE = DATA / "baseline_evidence_context.py.txt"


def test_frozen_contracts_and_replay_scope():
    cases = module.load(DATA, BASE, None)
    assert len(cases) == 24
    assert len({c["family"] for c in cases}) == 12
    assert all(c["split"] == "development" for c in cases)
    for ids in [["ragup-001"], ["assembly-999"], ["assembly-001", "assembly-001"]]:
        with pytest.raises(ValueError, match="Unknown or duplicate"):
            module.load(DATA, BASE, ids)


def test_changed_input_refused(tmp_path):
    for name in ["manifest.json", "cases.json", "baseline_evidence_context.py.txt"]:
        (tmp_path / name).write_bytes((DATA / name).read_bytes())
    (tmp_path / "cases.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen input"):
        module.load(tmp_path, BASE, None)


def test_unapproved_baseline_refused(tmp_path):
    altered = tmp_path / "source.txt"
    altered.write_text("raise RuntimeError('must not execute')", encoding="utf-8")
    with pytest.raises(ValueError, match="Unapproved baseline"):
        module.load(DATA, altered, None)
