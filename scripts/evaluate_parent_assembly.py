"""Synthetic fixed-anchor assembly comparison; not retrieval or answer accuracy."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from workbench.document_parsing import text_blocks
    from workbench.evidence_context import expand_context
    from workbench.parent_context import expand_parents, parent_child_chunks

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; preserve evidence")
    layouts = {
        "adjacent": "# 第1条 退货\n\n七天内可申请。\n\n定制商品除外。",
        "long_same_section": "# 第1条 退货\n\n七天内可申请。\n\n"
        + "流程说明。" * 150
        + "\n\n定制商品除外。",
        "explicit_cross_section": "# 第1条 退货\n\n七天内可申请，例外参见第3条。\n\n# 第2条 流程\n\n"
        + "说明。" * 100
        + "\n\n# 第3条 例外\n\n定制商品除外。",
        "unlinked_cross_section": "# 退货\n\n七天内可申请。\n\n"
        + "说明。" * 100
        + "\n\n# 特殊商品\n\n定制商品除外。",
        "tight_budget": "# 第1条 退货\n\n七天内可申请。\n\n"
        + "流程说明。" * 150
        + "\n\n定制商品除外。",
    }
    traces = []
    for name, text in layouts.items():
        chunks, structure = parent_child_chunks(text_blocks(text), 160, 20)
        parents = {p["id"]: p for p in structure["parents"]}
        rows = [
            {
                **c,
                "id": str(c["position"]),
                "version_id": "v",
                "organization_id": "demo",
                "parent_id": structure["children"][str(c["position"])],
                "parent_links": parents[structure["children"][str(c["position"])]]["links"],
            }
            for c in chunks
        ]
        budget = len(rows[0]["content"]) if name == "tight_budget" else 4000
        for mode in ("neighbors", "section", "parent_child"):
            if mode == "parent_child":
                selected, trace = expand_parents([rows[0]], rows, budget)
            else:
                selected = expand_context([rows[0]], rows, budget, mode)
                trace = {}
            traces.append(
                {
                    "case": name,
                    "mode": mode,
                    "anchor_ids": [rows[0]["id"]],
                    "selected_ids": [r["id"] for r in selected],
                    "exception_present": any("定制商品除外" in r["content"] for r in selected),
                    "chars": sum(len(r["content"]) for r in selected),
                    "budget": budget,
                    "trace": trace,
                }
            )
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "traces.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in traces),
        encoding="utf-8",
        newline="\n",
    )
    summary = {
        "kind": __doc__,
        "cases": 5,
        "records": 15,
        "modes": {
            m: sum(t["exception_present"] for t in traces if t["mode"] == m)
            for m in ("neighbors", "section", "parent_child")
        },
        "model_calls": 0,
        "development_diagnostic_after_implementation": True,
        "limits": [
            "unlinked cross-section exception unresolved",
            "tight budget deliberately incomplete",
            "not independent semantic test",
        ],
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
