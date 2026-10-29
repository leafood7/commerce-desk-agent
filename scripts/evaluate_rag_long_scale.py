"""Author-authored local RAG benchmark. Scoring labels never enter retrieval."""

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(directory, ids=None):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["hashes"].items():
        if digest(directory / name) != expected:
            raise ValueError("Fixture hash mismatch: " + name)
    if digest(ROOT / "docs/fullstack/rag-long-scale-protocol.md") != manifest["protocol_sha256"]:
        raise ValueError("Protocol mismatch")
    values = {
        name[:-5]: json.loads((directory / name).read_text(encoding="utf-8"))
        for name in manifest["hashes"]
    }
    valid = {case["id"] for case in values["cases"]}
    if len(valid) != manifest["cases"] or (ids and (len(ids) != len(set(ids)) or set(ids) - valid)):
        raise ValueError("Invalid or duplicate sample IDs")
    if not all(8000 <= len(doc["text"]) <= 15000 for doc in values["corpus"][:6]):
        raise ValueError("Long-document fixture is outside frozen range")
    values["cases"] = [case for case in values["cases"] if not ids or case["id"] in ids]
    return manifest, values


def score(case, selected, available, target_version):
    def normalize(text):
        return "".join(text.split())

    relevant = "\n".join(r["content"] for r in selected if r["version_id"] == target_version)
    raw = "\n".join(r["content"] for r in selected)
    required = case["required_quotes"]
    hits = sum(normalize(q) in normalize(relevant) for q in required)
    return {
        "necessary_coverage": hits / len(required) if required else None,
        "full_coverage": hits == len(required) if required else None,
        "representable_required": sum(
            any(
                normalize(q) in normalize(r["content"])
                for r in available
                if r["version_id"] == target_version
            )
            for q in required
        ),
        "required_count": len(required),
        "wrong_branch_hits": sum(
            normalize(q) in normalize(raw) for q in case["wrong_branch_quotes"]
        ),
        "wrong_document_chunks": sum(r["version_id"] != target_version for r in selected),
        "forbidden_chunks": sum(
            r["organization_id"] != case["organization_id"]
            or not r["published"]
            or not r["effective"]
            for r in selected
        ),
        "unknown_fact_returns_policy": bool(selected) if not case["answerable"] else None,
        "empty": not selected,
    }


def percentile(values, fraction):
    if not values:
        return None
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction))]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["validate", "run"])
    p.add_argument("--dataset", type=Path, default=ROOT / "data/evaluation/rag_long_scale_v2")
    p.add_argument("--case-id", action="append")
    p.add_argument("--version", action="append")
    p.add_argument("--tier", type=int, action="append")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--max-seconds", type=int, default=1800)
    a = p.parse_args()
    try:
        frozen, data = load(a.dataset, a.case_id)
        config = data["config"]
        arms = a.version or list(config["arms"])
        tiers = a.tier or config["tiers"]
        if len(set(arms)) != len(arms) or set(arms) - set(config["arms"]):
            raise ValueError("Unknown or duplicate configuration")
        if len(set(tiers)) != len(tiers) or set(tiers) - set(config["tiers"]):
            raise ValueError("Unknown or duplicate tier")
        if a.max_seconds < 1:
            raise ValueError("Invalid time ceiling")
        if a.baseline and not a.baseline.is_file():
            raise ValueError("Missing baseline reference")
        if a.command == "validate":
            print(
                json.dumps(
                    {"cases": len(data["cases"]), "tiers": tiers, "arms": arms, "frozen": True}
                )
            )
            return
        if a.output.exists():
            raise ValueError("Output exists; retain old experiments")
    except ValueError as error:
        p.error(str(error))
    import chromadb

    from business.rag_retrieval import (
        RERANKER_MODEL,
        RERANKER_REVISION,
        BM25Index,
        reciprocal_rank_fusion,
        rerank,
        reranker,
    )
    from workbench.document_parsing import chunks_from_blocks, text_blocks
    from workbench.evidence_context import expand_context
    from workbench.knowledge_index import EMBEDDING_MODEL, EMBEDDING_REVISION, embedding_function
    from workbench.reference_context import expand_references

    a.output.mkdir(parents=True)
    started = time.perf_counter()
    deadline = started + a.max_seconds
    model_started = time.perf_counter()
    embedding = embedding_function()
    reranker()
    rerank("预热", [{"id": "warmup", "content": "本地隔离演示，不执行退款。"}])
    model_ms = (time.perf_counter() - model_started) * 1000
    client = chromadb.PersistentClient(path=str(a.output / "index"))
    layouts = {(config["arms"][arm]["chunk"], config["arms"][arm]["size"]) for arm in arms}
    parsed = {}
    collections = {}
    indexed = {}
    for strategy, size in sorted(layouts):
        key = f"{strategy}{size}"
        rows = []
        for doc in data["corpus"][: max(tiers)]:
            for chunk in chunks_from_blocks(
                text_blocks(doc["text"]), strategy=strategy, size=size, overlap=40
            ):
                rows.append(
                    {
                        **chunk,
                        "id": doc["id"] + "-" + key + "-" + str(chunk["position"]),
                        "version_id": doc["id"],
                        "organization_id": doc["organization_id"],
                        "published": doc["published"],
                        "effective": doc["effective"],
                    }
                )
        parsed[key] = rows
        indexed[key] = set()
        collections[key] = client.get_or_create_collection(
            "long_" + key, embedding_function=embedding, metadata={"hnsw:space": "cosine"}
        )
    manifest = {
        **frozen,
        "dataset": str(a.dataset),
        "selected_cases": [c["id"] for c in data["cases"]],
        "tiers": sorted(tiers),
        "arms": arms,
        "config": config,
        "embedding": [EMBEDDING_MODEL, EMBEDDING_REVISION],
        "reranker": [RERANKER_MODEL, RERANKER_REVISION],
        "model_startup_ms": model_ms,
        "source_sha256": digest(Path(__file__)),
        "reference_helper_sha256": digest(ROOT / "src/workbench/reference_context.py"),
        "baseline_reference": str(a.baseline) if a.baseline else None,
        "baseline_sha256": digest(a.baseline) if a.baseline else None,
        "max_seconds": a.max_seconds,
        "indexing": [],
    }
    records = []
    stopped = False
    for tier in sorted(tiers):
        allowed_docs = {d["id"] for d in data["corpus"][:tier]}
        scoped_rows = {}
        for key, all_rows in parsed.items():
            rows = [r for r in all_rows if r["version_id"] in allowed_docs]
            missing = [r for r in rows if r["id"] not in indexed[key]]
            t = time.perf_counter()
            for start in range(0, len(missing), 64):
                batch = missing[start : start + 64]
                collections[key].upsert(
                    ids=[r["id"] for r in batch],
                    documents=[r["content"] for r in batch],
                    metadatas=[{"version_id": r["version_id"]} for r in batch],
                )
                indexed[key].update(r["id"] for r in batch)
            active = [
                r
                for r in rows
                if r["published"] and r["effective"] and r["organization_id"] == "merchant-a"
            ]
            scoped_rows[key] = (rows, active, BM25Index(active))
            manifest["indexing"].append(
                {
                    "tier_documents": tier,
                    "layout": key,
                    "total_chunks": len(rows),
                    "active_chunks": len(active),
                    "active_documents": len({r["version_id"] for r in active}),
                    "new_chunks": len(missing),
                    "incremental_index_ms": (time.perf_counter() - t) * 1000,
                    "characters": sum(len(d["text"]) for d in data["corpus"][:tier]),
                }
            )
        for number, case in enumerate(data["cases"]):
            rotation = (number + tier) % len(arms)
            order = arms[rotation:] + arms[:rotation]
            for arm in order:
                if time.perf_counter() > deadline:
                    stopped = True
                    break
                selected_config = config["arms"][arm]
                key = f"{selected_config['chunk']}{selected_config['size']}"
                all_rows, active, sparse_global = scoped_rows[key]
                version = data["bindings"][case["latest_binding"]]["version_id"]
                candidates = [
                    r for r in active if not selected_config["scoped"] or r["version_id"] == version
                ]
                versions = sorted({r["version_id"] for r in candidates})
                by_id = {r["id"]: r for r in candidates}
                pool = selected_config["pool"]
                t = time.perf_counter()
                dense_raw = collections[key].query(
                    query_texts=[case["question"]],
                    n_results=min(pool, len(candidates)),
                    where={"version_id": {"$in": versions}},
                    include=["distances"],
                )
                dense = [
                    {**by_id[k], "distance": distance}
                    for k, distance in zip(dense_raw["ids"][0], dense_raw["distances"][0])
                    if k in by_id and distance <= config["distance"]
                ]
                sparse = (
                    BM25Index(candidates) if selected_config["scoped"] else sparse_global
                ).search(case["question"], pool)
                fused = reciprocal_rank_fusion([dense, sparse], pool)
                ranked = (
                    [
                        r
                        for r in rerank(case["question"], fused)
                        if r["rerank_score"] >= config["rerank_threshold"]
                    ]
                    if selected_config["rerank"]
                    else fused
                )
                anchors = []
                chars = 0
                for row in ranked[: selected_config["top_k"]]:
                    if chars + len(row["content"]) <= config["budget"]:
                        anchors.append(row)
                        chars += len(row["content"])
                if selected_config["context"] == "references":
                    expanded = expand_references(anchors, candidates, config["budget"])
                    selected = expanded["matches"]
                    ref_trace = expanded["trace"]
                else:
                    selected = expand_context(anchors, candidates, config["budget"], "neighbors")
                    ref_trace = None
                elapsed = (time.perf_counter() - t) * 1000
                result = {
                    "tier": tier,
                    "arm": arm,
                    "case_id": case["id"],
                    "family": case["family"],
                    "question": case["question"],
                    "latest_binding": case["latest_binding"],
                    "elapsed_ms": elapsed,
                    "context_chars": sum(len(r["content"]) for r in selected),
                    "metrics": score(case, selected, candidates, version),
                    "anchor_metrics": score(case, anchors, candidates, version),
                    "trace": {
                        "dense": [r["id"] for r in dense],
                        "sparse": [r["id"] for r in sparse],
                        "fusion": [r["id"] for r in fused],
                        "reranked": [r["id"] for r in ranked],
                        "anchors": [r["id"] for r in anchors],
                        "references": ref_trace,
                    },
                    "evidence": selected,
                }
                records.append(result)
                with (a.output / "traces.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(
                f"tier={tier} case={number + 1}/{len(data['cases'])} rows={len(records)}",
                flush=True,
            )
            if stopped:
                break
        if stopped:
            break
    summaries = []
    for tier in sorted(tiers):
        for arm in arms:
            values = [r for r in records if r["tier"] == tier and r["arm"] == arm]
            if not values:
                continue
            positive = [r for r in values if r["metrics"]["full_coverage"] is not None]
            negative = [
                r for r in values if r["metrics"]["unknown_fact_returns_policy"] is not None
            ]
            times = [r["elapsed_ms"] for r in values]
            summaries.append(
                {
                    "tier": tier,
                    "arm": arm,
                    "evaluated": len(values),
                    "positive_cases": len(positive),
                    "full_cases": sum(r["metrics"]["full_coverage"] for r in positive),
                    "mean_coverage": statistics.mean(
                        r["metrics"]["necessary_coverage"] for r in positive
                    )
                    if positive
                    else None,
                    "anchor_mean_coverage": statistics.mean(
                        r["anchor_metrics"]["necessary_coverage"] for r in positive
                    )
                    if positive
                    else None,
                    "wrong_branch_cases": sum(
                        r["metrics"]["wrong_branch_hits"] > 0 for r in positive
                    ),
                    "wrong_document_chunks": sum(
                        r["metrics"]["wrong_document_chunks"] for r in values
                    ),
                    "forbidden_chunks": sum(r["metrics"]["forbidden_chunks"] for r in values),
                    "negative_cases": len(negative),
                    "negative_returns_policy": sum(
                        r["metrics"]["unknown_fact_returns_policy"] for r in negative
                    ),
                    "empty_cases": sum(r["metrics"]["empty"] for r in values),
                    "mean_chars": statistics.mean(r["context_chars"] for r in values),
                    "p50_ms": percentile(times, 0.5),
                    "p95_ms": percentile(times, 0.95),
                    "reference_limited_cases": sum(
                        bool((r["trace"]["references"] or {}).get("limited")) for r in values
                    ),
                    "reference_missing_cases": sum(
                        bool((r["trace"]["references"] or {}).get("missing")) for r in values
                    ),
                }
            )
    summary = {
        "completed": not stopped,
        "records": len(records),
        "expected_records": len(tiers) * len(arms) * len(data["cases"]),
        "runtime_seconds": time.perf_counter() - started,
        "new_paid_calls": 0,
        "results": summaries,
        "limits": [
            "Template synthetic authored development corpus",
            "Source and quote coverage, not generated answer accuracy",
            "Global includes current merchant active documents only",
            "Scaling tiers use nested incremental indexes; caches and hardware affect latency",
            "Human semantic review not completed",
        ],
    }
    manifest["index_bytes"] = sum(
        p.stat().st_size for p in (a.output / "index").rglob("*") if p.is_file()
    )
    for name, value in [("manifest.json", manifest), ("summary.json", summary)]:
        (a.output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "completed": not stopped,
                "records": len(records),
                "seconds": summary["runtime_seconds"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
