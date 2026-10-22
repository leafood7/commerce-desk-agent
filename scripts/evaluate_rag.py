"""固定语料与题集，对比三种检索策略；只允许开发集选择阈值。"""

import argparse
import copy
import hashlib
import json
import platform
from importlib.metadata import version
import statistics
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import chromadb
from business.knowledge import COLLECTION_NAME, EMBEDDING_MODEL, build_chunks, embedding_function, index_path
from business.rag_retrieval import RERANKER_MODEL, STRATEGIES, retrieve, reranker

DATASET = ROOT / "data/evaluation/rag_cases.jsonl"


def load_cases(split):
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    ids = {row["id"] for row in build_chunks()}
    seen = set()
    for case in cases:
        if case["id"] in seen:
            raise ValueError("题目编号重复")
        seen.add(case["id"])
        if case["answerable"] != bool(case["evidence_groups"]):
            raise ValueError("可回答标注与证据不一致")
        for group in case["evidence_groups"]:
            if not group or not set(group) <= ids:
                raise ValueError(f"证据编号不存在：{case['id']}")
    dev_groups = {c["group"] for c in cases if c["split"] == "dev"}
    test_groups = {c["group"] for c in cases if c["split"] == "test"}
    if dev_groups & test_groups:
        raise ValueError("开发与测试主题组交叉")
    return [c for c in cases if c["split"] == split]


def case_metrics(case, matches):
    if not case["answerable"]:
        return {"empty": not matches}
    ids = [row["id"] for row in matches[:3]]
    groups = case["evidence_groups"]
    recall = sum(bool(set(group) & set(ids)) for group in groups) / len(groups)
    relevant = set(key for group in groups for key in group)
    rr = next((1 / rank for rank, key in enumerate(ids, 1) if key in relevant), 0)
    return {"recall": recall, "hit": rr > 0, "mrr": rr}


def aggregate(rows, strategy, threshold):
    answerable, unknown, times, pool_hits = [], [], [], []
    for row in rows:
        details = row["strategies"][strategy]
        matches = details["matches"]
        if strategy == "hybrid_rerank":
            matches = [r for r in details["candidates"] if r["rerank_score"] >= threshold][:3]
        metric = case_metrics(row["case"], matches)
        (answerable if row["case"]["answerable"] else unknown).append(metric)
        if row["case"]["answerable"]:
            relevant = {key for g in row["case"]["evidence_groups"] for key in g}
            pool_hits.append(bool(relevant & {r["id"] for r in details["candidates"]}))
        times.append(sum(details["timing_ms"].values()))
    mean = lambda values: statistics.mean(values) if values else 0
    return {"answerable_n": len(answerable), "unanswerable_n": len(unknown),
            "evidence_recall_at_3": mean([r["recall"] for r in answerable]),
            "hit_at_3": mean([r["hit"] for r in answerable]),
            "mrr_at_3": mean([r["mrr"] for r in answerable]),
            "candidate_hit": mean(pool_hits),
            "unanswerable_empty_rate": mean([r["empty"] for r in unknown]),
            "latency_mean_ms": mean(times), "latency_p50_ms": statistics.median(times),
            "latency_p95_ms": sorted(times)[min(len(times)-1, int(len(times)*0.95))]}


def evaluate(args):
    if args.calibrate and args.split != "dev":
        raise ValueError("只允许开发集调参，禁止使用测试集选择阈值。")
    cases = load_cases(args.split)
    collection = chromadb.PersistentClient(path=str(index_path())).get_collection(COLLECTION_NAME, embedding_function=embedding_function())
    stored = collection.get(include=["documents"])
    corpus = dict(zip(stored["ids"], stored["documents"]))
    expected = {c["id"]: c["content"] for c in build_chunks()}
    if corpus != expected:
        raise ValueError("索引与当前政策正文不一致，请先重建索引。")
    cold = perf_counter()
    retrieve(collection, "退款到账时间", "vector")
    reranker().predict([("退款到账时间", "退款按原支付渠道返还。")], show_progress_bar=False)
    cold_ms = (perf_counter() - cold) * 1000
    rows = []
    for index, case in enumerate(cases, 1):
        results = {mode: retrieve(collection, case["question"], mode, rerank_threshold=float("-inf")) for mode in STRATEGIES}
        rows.append({"case": case, "strategies": results})
        print(f"{args.split} {index}/{len(cases)} {case['id']}", flush=True)
    threshold = args.threshold
    calibration = []
    if args.calibrate:
        for value in [-8, -6, -4, -2, 0, 2, 4, 6, 8]:
            metrics = aggregate(rows, "hybrid_rerank", value)
            calibration.append({"threshold": value, "objective": (metrics["hit_at_3"] + metrics["unanswerable_empty_rate"]) / 2, "metrics": metrics})
        eligible = [item for item in calibration if item["metrics"]["evidence_recall_at_3"] >= 0.9]
        if not eligible:
            raise ValueError("开发集无阈值满足90%证据召回要求，不能自动启用重排。")
        best = max(eligible, key=lambda item: (item["metrics"]["unanswerable_empty_rate"],
                                               item["metrics"]["mrr_at_3"], -abs(item["threshold"])))
        threshold = best["threshold"]
    metrics = {mode: aggregate(rows, mode, threshold) for mode in STRATEGIES}
    for row in rows:
        details = row["strategies"]["hybrid_rerank"]
        details["matches"] = [r for r in details["candidates"] if r["rerank_score"] >= threshold][:3]
    unfiltered_rows = copy.deepcopy(rows)
    for row in unfiltered_rows:
        row["strategies"]["vector"]["matches"] = row["strategies"]["vector"]["candidates"][:3]
    diagnostics = {"vector_top3_without_old_threshold": aggregate(unfiltered_rows, "vector", threshold),
                   "rerank_top3_without_threshold": aggregate(rows, "hybrid_rerank", float("-inf"))}
    revision = getattr(reranker().model.config, "_commit_hash", None)
    report = {"split": args.split, "threshold": threshold, "metrics": metrics,
              "metadata": {"embedding": EMBEDDING_MODEL, "reranker": RERANKER_MODEL,
                           "reranker_revision": revision, "pool_size": 12, "top_k": 3, "rrf_constant": 60,
                           "vector_threshold": 0.7, "device": "cpu", "threads": 4, "platform": platform.platform(), "python": platform.python_version(),
                           "dependencies": {name: version(name) for name in ["sentence-transformers", "chromadb", "torch"]},
                           "cold_start_ms": cold_ms,
                           "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                           "corpus_sha256": hashlib.sha256(json.dumps(sorted(corpus.items()), ensure_ascii=False).encode()).hexdigest()},
              "selection_rule": "仅开发集：证据Recall@3至少0.90，再最大化无答案空返回率、MRR，最后选择绝对值较小阈值",
              "calibration": calibration, "diagnostics": diagnostics, "rows": rows}
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"split": args.split, "threshold": threshold, "metrics": metrics}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.0)
    parser.add_argument("--output", required=True)
    evaluate(parser.parse_args())
