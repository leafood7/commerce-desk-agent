"""Guard the evaluation boundary before any database or model dispatch."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "rag_real_scale", ROOT / "scripts/evaluate_rag_real_scale.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_frozen_inputs_and_unknown_or_duplicate_ids():
    directory = ROOT / "data/evaluation/rag_real_v1"
    _, _, cases = module.load(directory, None)
    assert len(cases) == 24
    for ids in [["real-999"], ["real-001", "real-001"]]:
        with pytest.raises(ValueError, match="Unknown or duplicate"):
            module.load(directory, ids)


def test_manifest_rejects_content_change_before_run(tmp_path):
    directory = ROOT / "data/evaluation/rag_real_v1"
    for name in ["manifest.json", "corpus.json", "cases.json"]:
        (tmp_path / name).write_bytes((directory / name).read_bytes())
    data = json.loads((tmp_path / "corpus.json").read_text(encoding="utf-8"))
    data[0]["text"] = "changed"
    (tmp_path / "corpus.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen file changed"):
        module.load(tmp_path, None)


def test_capacity_generator_uses_real_parser_without_count_padding():
    from workbench.document_parsing import chunks_from_blocks, text_blocks

    seen = set()
    for number in range(100):
        chunks = chunks_from_blocks(text_blocks(module.synthetic(number)), size=600, overlap=80)
        assert len(chunks) == 100
        for chunk in chunks:
            assert chunk["content"] not in seen
            seen.add(chunk["content"])
    assert len(seen) == 10000


def test_nearest_rank_p95_does_not_understate_small_sample_tail():
    assert module.nearest_rank([6, 1, 5, 2, 4, 3], 0.95) == 6
