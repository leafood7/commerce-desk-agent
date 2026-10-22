"""中文 BM25、RRF 融合和交叉编码器重排。"""

import math
import re
from collections import Counter
from functools import lru_cache
from time import perf_counter
from threading import Lock

RERANKER_MODEL = "BAAI/bge-reranker-base"
RERANKER_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
STRATEGIES = ("vector", "hybrid", "hybrid_rerank")
_RERANKER_LOCK = Lock()


def tokenize(text):
    tokens = []
    for part in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.extend(part)
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
        else:
            tokens.append(part)
    return tokens


class BM25Index:
    def __init__(self, documents, k1=1.5, b=0.75):
        self.documents = documents
        self.k1, self.b = k1, b
        self.terms = [Counter(tokenize(item["content"])) for item in documents]
        self.lengths = [sum(terms.values()) for terms in self.terms]
        self.average = sum(self.lengths) / max(len(documents), 1)
        self.df = Counter(term for terms in self.terms for term in terms)

    def search(self, query, limit):
        scored = []
        for document, terms, length in zip(self.documents, self.terms, self.lengths):
            score = 0.0
            for term in set(tokenize(query)):
                frequency = terms.get(term, 0)
                if not frequency:
                    continue
                idf = math.log(1 + (len(self.documents) - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denominator = frequency + self.k1 * (1 - self.b + self.b * length / (self.average or 1))
                score += idf * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append({**document, "bm25_score": score})
        return sorted(scored, key=lambda row: (-row["bm25_score"], row["id"]))[:limit]


@lru_cache(maxsize=2)
def cached_bm25(snapshot):
    return BM25Index([{"id": row[0], "content": row[1], "source": row[2]} for row in snapshot])


def reciprocal_rank_fusion(rankings, limit=12, constant=60):
    merged = {}
    for ranking in rankings:
        seen = set()
        for rank, document in enumerate(ranking, 1):
            key = document["id"]
            if key in seen:
                continue
            seen.add(key)
            if key not in merged:
                merged[key] = {**document, "rrf_score": 0.0}
            merged[key].update({k: v for k, v in document.items() if k != "rrf_score"})
            merged[key]["rrf_score"] += 1 / (constant + rank)
    return sorted(merged.values(), key=lambda row: (-row["rrf_score"], row["id"]))[:limit]


@lru_cache(maxsize=1)
def _load_reranker():
    import torch
    from sentence_transformers import CrossEncoder
    from business.order_repository import PROJECT_ROOT

    torch.set_num_threads(4)
    return CrossEncoder(RERANKER_MODEL, device="cpu", max_length=512,
                        cache_folder=str(PROJECT_ROOT / "work/huggingface/hub"),
                        activation_fn=torch.nn.Identity(), local_files_only=True, revision=RERANKER_REVISION,
                        trust_remote_code=False)


def reranker():
    with _RERANKER_LOCK:
        return _load_reranker()


def rerank(query, candidates):
    if not candidates:
        return []
    scores = reranker().predict([(query, row["content"]) for row in candidates],
                               batch_size=8, show_progress_bar=False)
    rows = [{**row, "rerank_score": float(score)} for row, score in zip(candidates, scores)]
    return sorted(rows, key=lambda row: (-row["rerank_score"], row["id"]))


def vector_candidates(collection, query, limit):
    if collection.count() == 0:
        return []
    result = collection.query(query_texts=[query], n_results=min(limit, collection.count()))
    return [{"id": key, "content": text, "source": metadata["source"], "distance": float(distance)}
            for key, text, metadata, distance in zip(result["ids"][0], result["documents"][0],
                                                    result["metadatas"][0], result["distances"][0])]


def retrieve(collection, query, strategy, top_k=3, pool_size=12, rerank_threshold=0.0):
    if strategy not in STRATEGIES:
        raise ValueError(f"未知检索策略：{strategy}")
    if top_k < 1 or pool_size < top_k:
        raise ValueError("候选数必须不少于返回数，且返回数至少为1。")
    started = perf_counter()
    dense = vector_candidates(collection, query, top_k if strategy == "vector" else pool_size)
    vector_ms = (perf_counter() - started) * 1000
    if strategy == "vector":
        return {"matches": [row for row in dense if row["distance"] <= 0.7],
                "candidates": dense, "timing_ms": {"vector": vector_ms}}
    snapshot = collection.get(include=["documents", "metadatas"])
    fingerprint = tuple(sorted((key, text, metadata["source"]) for key, text, metadata in
                               zip(snapshot["ids"], snapshot["documents"], snapshot["metadatas"])))
    fusion_start = perf_counter()
    sparse = cached_bm25(fingerprint).search(query, pool_size)
    fused = reciprocal_rank_fusion([dense, sparse], pool_size)
    fusion_ms = (perf_counter() - fusion_start) * 1000
    if strategy == "hybrid":
        return {"matches": fused[:top_k], "candidates": fused,
                "timing_ms": {"vector": vector_ms, "bm25_rrf": fusion_ms}}
    rerank_start = perf_counter()
    ranked = rerank(query, fused)
    return {"matches": [row for row in ranked if row["rerank_score"] >= rerank_threshold][:top_k],
            "candidates": ranked,
            "timing_ms": {"vector": vector_ms, "bm25_rrf": fusion_ms,
                          "rerank": (perf_counter() - rerank_start) * 1000}}
