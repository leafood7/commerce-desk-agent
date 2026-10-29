"""Real local BGE/BM25/reranker comparison on frozen multi-section development documents."""

import hashlib
import json
import statistics
import time
from pathlib import Path

import chromadb

from business.rag_retrieval import BM25Index, reciprocal_rank_fusion, rerank, reranker
from workbench.document_parsing import chunks_from_blocks, text_blocks
from workbench.evidence_context import expand_context
from workbench.knowledge_index import embedding_function

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/evaluation/agent_rag_v1"
OUT = ROOT / "work/agent-rag-v1/rag"


def coverage(case, rows):
    if not case["answerable"]:
        return {"unsupported_retrieval": bool(rows)}
    text = "".join("".join(x["content"].split()) for x in rows)
    groups = case["required_text_groups"]
    return {
        "clause_coverage": sum(any("".join(c.split()) in text for c in group) for group in groups)
        / len(groups)
    }


def main():
    if OUT.exists():
        raise ValueError("Do not overwrite traces")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["hashes"].items():
        assert hashlib.sha256((DATA / (name + ".json")).read_bytes()).hexdigest() == digest
    OUT.mkdir(parents=True)
    embedding = embedding_function()
    reranker()
    client = chromadb.PersistentClient(path=str(OUT / "index"))
    corpora = json.loads((DATA / "corpus.json").read_text(encoding="utf-8"))
    arms = {}
    for chunk in ("fixed", "structural"):
        docs = []
        for doc in corpora:
            for row in chunks_from_blocks(
                text_blocks(doc["text"]), strategy=chunk, size=600, overlap=80
            ):
                docs.append(
                    {
                        **row,
                        "id": doc["id"] + "-" + str(row["position"]),
                        "version_id": doc["id"],
                        "organization_id": "isolated-experiment",
                    }
                )
        collection = client.get_or_create_collection(
            "long_" + chunk, embedding_function=embedding, metadata={"hnsw:space": "cosine"}
        )
        collection.upsert(ids=[x["id"] for x in docs], documents=[x["content"] for x in docs])
        arms[chunk] = (docs, collection, BM25Index(docs))
    rows = []
    cases = json.loads((DATA / "cases.json").read_text(encoding="utf-8"))
    for index, case in enumerate(cases):
        for chunk in ("fixed", "structural") if index % 2 == 0 else ("structural", "fixed"):
            docs, collection, sparse_index = arms[chunk]
            by_id = {x["id"]: x for x in docs}
            start = time.perf_counter()
            found = collection.query(query_texts=[case["question"]], n_results=min(12, len(docs)))
            dense = [
                {**by_id[key], "distance": distance}
                for key, distance in zip(found["ids"][0], found["distances"][0])
                if distance <= 0.65
            ]
            sparse = sparse_index.search(case["question"], 12)
            fusion = reciprocal_rank_fusion([dense, sparse], 12)
            ranked = {"vector": dense, "hybrid": fusion}
            ranked["hybrid_rerank"] = [
                x for x in rerank(case["question"], fusion) if x["rerank_score"] >= -2
            ]
            for strategy, candidates in ranked.items():
                anchors = candidates[:3]
                for mode in ("neighbors", "section"):
                    selected = expand_context(anchors, docs, 4000, mode)
                    row = {
                        "id": case["id"],
                        "arm": chunk + "/" + strategy + "/" + mode,
                        "answerable": case["answerable"],
                        "matches": selected,
                        "anchor_metrics": coverage(case, anchors),
                        "metrics": coverage(case, selected),
                        "context_chars": sum(len(x["content"]) for x in selected),
                        "shared_pipeline_ms": (time.perf_counter() - start) * 1000,
                    }
                    rows.append(row)
                    with (OUT / "traces.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{index + 1}/{len(cases)}", flush=True)
    summary = {
        "chunks": {name: len(value[0]) for name, value in arms.items()},
        "arms": {},
        "paid_model_calls": 0,
        "latency_note": "shared retrieval pipeline incl rerank; not per-arm latency",
    }
    for arm in sorted({row["arm"] for row in rows}):
        selected = [r for r in rows if r["arm"] == arm]
        positive = [r for r in selected if r["answerable"]]
        negative = [r for r in selected if not r["answerable"]]
        summary["arms"][arm] = {
            "clause_coverage": statistics.mean(r["metrics"]["clause_coverage"] for r in positive),
            "anchor_coverage": statistics.mean(
                r["anchor_metrics"]["clause_coverage"] for r in positive
            ),
            "unsupported_retrieval": statistics.mean(
                r["metrics"]["unsupported_retrieval"] for r in negative
            ),
            "mean_chars": statistics.mean(r["context_chars"] for r in selected),
        }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
