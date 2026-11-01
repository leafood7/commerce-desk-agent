"""Frozen evidence-assembly replay only; no retrieval, model, or business writes."""

import argparse
import hashlib
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(dataset, baseline, ids):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["sha256"].items():
        if hashlib.sha256((dataset / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen input changed")
    protocol = ROOT / "docs/fullstack/rag-upgrade-v1/protocol.md"
    if hashlib.sha256(protocol.read_bytes()).hexdigest() != manifest["protocol_sha256"]:
        raise ValueError("Frozen protocol changed")
    if (
        hashlib.sha256(baseline.read_bytes()).hexdigest()
        != manifest["sha256"]["baseline_evidence_context.py.txt"]
    ):
        raise ValueError("Unapproved baseline")
    cases = json.loads((dataset / "cases.json").read_text(encoding="utf-8"))
    supported = {f"assembly-{n:03d}" for n in range(1, 7)}
    if ids and (len(ids) != len(set(ids)) or set(ids) - supported):
        raise ValueError(
            "Unknown or duplicate replay case ID; development contracts are not replay IDs"
        )
    return cases


def fixtures():
    for number in range(1, 7):
        rows = []
        for pos in range(5):
            rows.append(
                {
                    "id": f"c{pos}",
                    "version_id": "allowed",
                    "organization_id": "demo-a",
                    "position": pos,
                    "section": "return",
                    "content": f"演示条款{pos}。",
                }
            )
        rows[0]["content"] = "普通商品七天内可申请退货。"
        required = ["c0", "c1"]
        budget = 4000
        if number == 1:
            rows[1]["content"] = "定制商品不适用上述无理由规则。"
        elif number == 2:
            rows[4]["content"] = "跨段例外：定制商品不适用。"
            required = ["c0", "c4"]
        elif number == 3:
            rows[1]["content"] = "例外：软件许可条款须单独核实。"
            rows[1]["section"] = "software"
        elif number == 4:
            budget = len(rows[0]["content"])
        elif number == 5:
            rows[1]["version_id"] = "withdrawn"
            required = ["c0"]
        else:
            rows[1]["organization_id"] = "demo-b"
            required = ["c0"]
        yield f"assembly-{number:03d}", rows, required, budget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/rag_upgrade_v1")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--version", action="append", choices=["neighbors", "section"])
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline or args.dataset / "baseline_evidence_context.py.txt"
    try:
        cases = load(args.dataset, baseline, args.case_id)
        modes = args.version or ["neighbors", "section"]
        if len(modes) != len(set(modes)):
            raise ValueError("Duplicate version")
        if args.output.exists():
            raise ValueError("Output already exists; do not overwrite evidence")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    expand = runpy.run_path(str(baseline))["expand_context"]
    traces = []
    for identifier, rows, required, budget in fixtures():
        if args.case_id and identifier not in args.case_id:
            continue
        for mode in modes:
            selected = expand([rows[0]], rows, budget, mode)
            ids = {r["id"] for r in selected}
            traces.append(
                {
                    "case_id": identifier,
                    "version": mode,
                    "budget_chars": budget,
                    "anchor_ids": ["c0"],
                    "required_ids": required,
                    "documents": rows,
                    "selected_ids": sorted(ids),
                    "required_complete": set(required) <= ids,
                    "foreign_or_wrong_version": any(
                        r["organization_id"] != "demo-a" or r["version_id"] != "allowed"
                        for r in selected
                    ),
                    "within_budget": sum(len(r["content"]) for r in selected) <= budget,
                }
            )
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "traces.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in traces),
        encoding="utf-8",
        newline="\n",
    )
    summary = {
        "kind": "synthetic fixed-anchor assembly, not RAG answer evaluation",
        "development_contracts": len(cases),
        "records": len(traces),
        "model_calls": 0,
        "modes": {
            m: {
                "records": sum(t["version"] == m for t in traces),
                "required_complete": sum(
                    t["version"] == m and t["required_complete"] for t in traces
                ),
            }
            for m in modes
        },
        "safety_violations": sum(
            t["foreign_or_wrong_version"] or not t["within_budget"] for t in traces
        ),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
