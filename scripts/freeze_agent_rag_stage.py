"""Freeze public development inputs before implementing/evaluating candidate mechanisms."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/evaluation/agent_rag_v1"
GROUPS = {
    "coupon": ["coupon_scope_policy", "coupon_usage_policy", "coupon_refund_policy"],
    "shipping": ["shipping_policy", "shipping_exception_policy", "presale_shipping_policy"],
    "returns": [
        "return_policy",
        "return_apparel_policy",
        "return_custom_policy",
        "return_digital_policy",
        "return_fresh_policy",
        "return_hygiene_policy",
        "refund_policy",
    ],
    "account": [
        "membership_policy",
        "membership_scope_policy",
        "invoice_policy",
        "invoice_amount_policy",
        "invoice_correction_policy",
    ],
}


def main():
    if OUT.exists():
        raise ValueError("Frozen inputs already exist; never overwrite")
    gold = {}
    corpus = []
    source_map = {}
    for family, names in GROUPS.items():
        pieces = ["# 开发用组合政策文档：" + family]
        originals = []
        for name in names:
            path = ROOT / "data/knowledge" / (name + ".txt")
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
            pieces += ["## " + lines[0], *lines[1:]]
            for index, clause in enumerate(lines[1:]):
                gold[path.name + "-" + str(index)] = clause
            source_map[path.name] = family
            originals.append(
                {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
        text = "\n\n".join(pieces)
        corpus.append({"id": family, "text": text, "chars": len(text), "sources": originals})
    rows = [
        json.loads(line)
        for line in (ROOT / "data/evaluation/rag_cases.jsonl")
        .read_text(encoding="utf-8-sig")
        .splitlines()
        if line.strip()
    ]
    cases = [x for x in rows if x["split"] == "dev"]
    for parent, question in [
        ("presale_shipping_policy-1", "只付定金，尾款未付，能算全款催发货吗？"),
        ("refund_policy-3", "使用现金和商品券的订单取消后，可以将券部分退成现金吗？"),
    ]:
        base = next(x for x in cases if x["id"] == parent)
        cases.append({**base, "id": parent + "-paired", "question": question, "parent_id": parent})
    for row in cases:
        row["required_text_groups"] = [
            [gold[key] for key in group] for group in row["evidence_groups"]
        ]
    OUT.mkdir(parents=True)
    for name, value in (("corpus", corpus), ("cases", cases)):
        (OUT / (name + ".json")).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    manifest = {
        "version": "agent-rag-v1",
        "split": "development_only",
        "cases": 24,
        "corpus_transform": "18 original public policy files combined into 4 thematic Markdown documents; preserve every original clause and heading, no filler",
        "corpus_chars": {x["id"]: x["chars"] for x in corpus},
        "hashes": {
            name: hashlib.sha256((OUT / (name + ".json")).read_bytes()).hexdigest()
            for name in ("corpus", "cases")
        },
        "arms": "fixed/structural at 600/80; vector/hybrid/hybrid_rerank; neighbors/section expansion; top3 budget4000",
        "metrics": [
            "source hit",
            "complete necessary-clause coverage",
            "anchor-only coverage",
            "unanswerable retrieval",
            "chars",
            "latency",
        ],
        "promotion": "experimental default off; require constraint tests and no task regression; no online-accuracy claim",
        "model_scope": "bounded read-only tool proposals, fixed write confirmation and approval; paid calls require explicit authorization",
        "limits": "correlated development variants; combined public corpus, not independent blind test; no sealed holdout",
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"frozen_cases": 24, "corpus_chars": manifest["corpus_chars"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
