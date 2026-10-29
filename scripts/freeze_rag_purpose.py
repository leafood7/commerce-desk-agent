"""Freeze explicit task metadata and operator policy mapping, without scoring labels."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "data/evaluation/rag_purpose_v1"
    if target.exists():
        raise ValueError("Frozen dataset exists")
    parent = ROOT / "data/evaluation/rag_long_scale_v2"
    corpus = json.loads((parent / "corpus.json").read_text(encoding="utf-8"))
    # Source document structure, not the per-question scoring file.
    sections = {}
    for section in corpus[0]["text"].split("## ")[1:]:
        heading, body = section.split("\n", 1)
        number = int(heading.split("条", 1)[0].replace("第", ""))
        sections[number] = body.strip().split("\n\n", 1)[0]
    catalog = {
        basis: {
            "version_id": "long-0",
            "mapping": "operator_authored_synthetic_policy",
            "clauses": [
                {"article": n, "purpose": f"article_{n}", "quote": sections[n]} for n in numbers
            ],
        }
        for basis, numbers in {"no_reason": [2, 3, 22], "quality_issue": [2, 4, 12, 16, 20]}.items()
    }
    tasks = [
        {
            "task_id": f"qa-{n:03}",
            "source_case_id": f"long-dev-{n:03}",
            "purpose": "policy_question",
        }
        for n in range(1, 33)
    ]
    tasks += [
        {"task_id": f"app-{n:03}", "source_case_id": f"long-dev-{n:03}", "purpose": "application"}
        for n in [5, 9, 13, 17, 21, 22, 23, 24]
    ]
    target.mkdir(parents=True)
    hashes = {}
    for name, value in [("catalog.json", catalog), ("tasks.json", tasks)]:
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        (target / name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    manifest = {
        "split": "authored_development_replay",
        "tasks": 40,
        "expected_rows": 720,
        "hashes": hashes,
        "parent_trace_sha256": hashlib.sha256(
            (ROOT / "docs/fullstack/rag-long-scale-evaluation/full/traces.jsonl").read_bytes()
        ).hexdigest(),
        "parent_cases_sha256": hashlib.sha256((parent / "cases.json").read_bytes()).hexdigest(),
        "rubric_sha256": hashlib.sha256(
            (ROOT / "data/evaluation/rag_long_scale_review_v1/rubric.json").read_bytes()
        ).hexdigest(),
        "protocol_sha256": hashlib.sha256(
            (ROOT / "docs/fullstack/rag-purpose-protocol.md").read_bytes()
        ).hexdigest(),
        "new_model_calls": 0,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Frozen 40 explicit tasks, operator source catalog and immutable parent evidence hashes")


if __name__ == "__main__":
    main()
