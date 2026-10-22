"""三个漏检案例的阶段诊断及固定锚点上下文回归；不调用外部模型。"""

import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import chromadb
from business.knowledge import COLLECTION_NAME, build_chunks, embedding_function, index_path
from business import rag_retrieval as live
from business.policy_context import CONTEXT_CHAR_BUDGET, expand_policy_context, flatten_policy_matches
from evaluate_query_rewrite import raw_query

BASELINE_REF = "e71f35fe2a7ce333bd7271a8ddedb57ffa0e5cd1"
TARGETS = [
    {"id": "phone", "query": "手机拆封后能否退货 七天无理由退货", "evidence_groups": [["return_digital_policy.txt-1"]]},
    {"id": "gold", "query": "金卡会员购买普通商品，页面未标注延长，退货申请期是多少天？", "evidence_groups": [["membership_policy.txt-1"], ["membership_policy.txt-2"]]},
    {"id": "coupon", "query": "食品券在有效期内能否用于购买显示器？优惠券适用商品范围如何规定？", "evidence_groups": [["coupon_scope_policy.txt-0"]]},
]


def coverage(groups, evidence):
    ids = {row["id"] for row in evidence}
    return sum(bool(set(group) & ids) for group in groups) / len(groups)


def load_baseline():
    source = subprocess.check_output(["git", "show", BASELINE_REF + ":src/business/rag_retrieval.py"], cwd=ROOT)
    old = types.ModuleType("retrieval_before_context_fix")
    exec(compile(source, "retrieval_before_context_fix.py", "exec"), old.__dict__)
    # 共享完全相同的权重实例，不因基线加载第二套模型。
    old.reranker = live.reranker
    return old, source


def main():
    old, source = load_baseline()
    client = chromadb.PersistentClient(path=str(index_path()))
    collection = client.get_collection(COLLECTION_NAME, embedding_function=embedding_function())
    chunks = build_chunks()
    stored = collection.get(include=["documents", "metadatas"])
    documents = [{"id": key, "content": content, "source": meta["source"]}
                 for key, content, meta in zip(stored["ids"], stored["documents"], stored["metadatas"])]
    if {r["id"]: r["content"] for r in chunks} != {r["id"]: r["content"] for r in documents}:
        raise ValueError("索引与语料不一致")
    snapshot = tuple(sorted((r["id"], r["content"], r["source"]) for r in documents))
    target_rows = []
    for case in TARGETS:
        dense = old.vector_candidates(collection, case["query"], 12)
        sparse = old.cached_bm25(snapshot).search(case["query"], 12)
        fused = old.reciprocal_rank_fusion([dense, sparse], 12)
        ranked = old.rerank(case["query"], fused)
        oracle = old.rerank(case["query"], chunks)
        before = [row for row in ranked if row["rerank_score"] >= -2][:3]
        actual = live.retrieve(collection, case["query"], "hybrid_rerank", rerank_threshold=-2)
        assert [r["id"] for r in actual["anchor_matches"]] == [r["id"] for r in before]
        after = flatten_policy_matches(actual["matches"])
        stages = {"dense": dense, "bm25": sparse, "fusion": fused, "rerank": ranked, "all_chunks_rerank": oracle}
        traces = {key: {name: next(({"rank": i, "score": row.get("rerank_score")} for i, row in enumerate(values, 1) if row["id"] == key), None)
                       for name, values in stages.items()}
                  for group in case["evidence_groups"] for key in group}
        target_rows.append({**case, "before_coverage": coverage(case["evidence_groups"], before),
            "after_coverage": coverage(case["evidence_groups"], after), "diagnosis": traces,
            "before_ids": [r["id"] for r in before], "after_ids": [r["id"] for r in after],
            "before_chars": sum(len(r["content"]) for r in before),
            "after_chars": sum(len(r["content"]) for r in after)})
        print(json.dumps(target_rows[-1], ensure_ascii=False), flush=True)
    cases = []
    for file, group in [("rag_cases.jsonl", "rag"), ("query_rewrite_cases.jsonl", "dialogue"), ("policy_refresh_holdout.jsonl", "previous_new_cases")]:
        for line in (ROOT / "data/evaluation" / file).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                case = json.loads(line)
                cases.append({**case, "set": group + ("/" + case["split"] if "split" in case else ""),
                              "query": case.get("question") or raw_query(case["turns"])})
    results = []
    for index, case in enumerate(cases, 1):
        report = old.retrieve(collection, case["query"], "hybrid_rerank", rerank_threshold=-2)
        anchors = report["matches"]
        # 生产只改变上下文组装，固定同一锚点进行对照，避免重复重排随机误差。
        expanded = expand_policy_context(anchors, documents)
        after = flatten_policy_matches(expanded)
        row = {"id": case["id"], "set": case["set"], "answerable": case["answerable"],
               "before_ids": [r["id"] for r in anchors], "after_ids": [r["id"] for r in after],
               "before_chars": sum(len(r["content"]) for r in anchors),
               "after_chars": sum(len(r["content"]) for r in after)}
        if case["answerable"]:
            row.update(before_coverage=coverage(case["evidence_groups"], anchors),
                       after_coverage=coverage(case["evidence_groups"], after))
        else:
            row.update(before_empty=not anchors, after_empty=not after)
        results.append(row)
        if index % 10 == 0:
            print(f"offline regression {index}/{len(cases)}", flush=True)
    summary = {}
    for group in sorted({r["set"] for r in results}):
        selected = [r for r in results if r["set"] == group]
        known = [r for r in selected if r["answerable"]]
        unknown = [r for r in selected if not r["answerable"]]
        summary[group] = {"count": len(selected), "answerable": len(known),
            "before_complete": sum(r["before_coverage"] == 1 for r in known),
            "after_context_complete": sum(r["after_coverage"] == 1 for r in known),
            "before_empty_unknown": sum(r["before_empty"] for r in unknown),
            "after_empty_unknown": sum(r["after_empty"] for r in unknown),
            "unknown_count": len(unknown),
            "mean_before_chars": sum(r["before_chars"] for r in selected) / len(selected),
            "mean_after_chars": sum(r["after_chars"] for r in selected) / len(selected),
            "coverage_losses": [r["id"] for r in known if r["after_coverage"] < r["before_coverage"]]}
    metadata = {"baseline_ref": BASELINE_REF, "baseline_sha256": hashlib.sha256(source).hexdigest(),
        "context_sha256": hashlib.sha256((ROOT / "src/business/policy_context.py").read_bytes()).hexdigest(),
        "retriever_sha256": hashlib.sha256((ROOT / "src/business/rag_retrieval.py").read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(json.dumps(sorted((r["id"],r["content"]) for r in documents),ensure_ascii=False).encode()).hexdigest(),
        "top_k_anchors": 3, "pool_size": 12, "threshold": -2, "context_char_budget": CONTEXT_CHAR_BUDGET,
        "method": "三个固定查询真实运行新旧检索；其余116题固定旧版锚点后组装同源上下文，锚点排名不变。均为既有题回归，非新留出集；补证覆盖不等于Top3排序提升。"}
    output = {"metadata": metadata, "targets": target_rows, "summary": summary, "rows": results}
    (ROOT / "work/rag-miss-fix-offline.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
