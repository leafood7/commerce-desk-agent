"""Build and search the customer-support policy index stored in this project."""

import json
import os
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from business.order_repository import PROJECT_ROOT

COLLECTION_NAME = "supportpilot_policies"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
MAX_DISTANCE = 0.7


def index_path() -> Path:
    path = Path(os.environ.get("SUPPORTPILOT_CHROMA_PATH", "data/supportpilot/chroma"))
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def build_chunks(directory: Path | None = None) -> list[dict]:
    directory = directory or PROJECT_ROOT / "data" / "knowledge"
    chunks = []
    for path in sorted(directory.glob("*.txt")):
        paragraphs = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                      if line.strip()]
        if len(paragraphs) < 2:
            continue
        for position, paragraph in enumerate(paragraphs[1:]):
            chunks.append({
                "id": f"{path.name}-{position}",
                "source": path.name,
                "content": f"{paragraphs[0]}\n\n{paragraph}",
            })
    return chunks


@cache
def embedding_function():
    return SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL,
        cache_folder=str(PROJECT_ROOT / "work" / "huggingface" / "hub"),
    )


def is_index_ready() -> bool:
    if not (index_path() / "chroma.sqlite3").is_file():
        return False
    client = chromadb.PersistentClient(path=str(index_path()))
    try:
        collection = client.get_collection(COLLECTION_NAME, embedding_function=None)
    except chromadb.errors.NotFoundError:
        return False
    return collection.count() > 0 and collection.metadata.get("embedding_model") == EMBEDDING_MODEL


def initialize_knowledge() -> dict:
    chunks = build_chunks()
    if not chunks:
        raise ValueError("未找到可用政策正文，请检查 data/knowledge 中的 txt 文件。")
    client = chromadb.PersistentClient(path=str(index_path()))
    collection = client.get_or_create_collection(
        COLLECTION_NAME,
        embedding_function=embedding_function(),
        metadata={"embedding_model": EMBEDDING_MODEL, "hnsw:space": "l2"},
    )
    ids = [chunk["id"] for chunk in chunks]
    collection.upsert(   #为片段计算向量，写入 Chroma   编号不存在：新增。 编号已经存在：更新
        ids=ids,   #→ 片段编号
        documents=[chunk["content"] for chunk in chunks],   #→ 这个片段的正文
        metadatas=[{"source": chunk["source"]} for chunk in chunks],   #→ 这个片段的来源
    )
    stale_ids = set(collection.get(include=[])["ids"]) - set(ids)
    if stale_ids:
        collection.delete(ids=sorted(stale_ids))
    return {"chunks": collection.count(), "sources": len({c["source"] for c in chunks}),
            "model": EMBEDDING_MODEL, "path": str(index_path())}


def search_policies_vector(query: str, top_k: int = 3) -> list[dict]:
    if not query.strip():
        return []
    if not (index_path() / "chroma.sqlite3").is_file():
        raise FileNotFoundError("政策索引尚未初始化，请运行 python scripts/init_knowledge.py。")
    client = chromadb.PersistentClient(path=str(index_path()))
    try:
        collection = client.get_collection(COLLECTION_NAME, embedding_function=embedding_function())
    except chromadb.errors.NotFoundError as exc:
        raise FileNotFoundError("政策集合不存在，请运行 python scripts/init_knowledge.py。") from exc
    if collection.count() == 0:
        return []
    results = collection.query(
        query_texts=[query], n_results=min(max(1, top_k), collection.count())
    )
    candidates = []
    for chunk_id, content, metadata, distance in zip(
        results["ids"][0], results["documents"][0],
        results["metadatas"][0], results["distances"][0],
    ):
        candidates.append({"id": chunk_id, "source": metadata["source"],
                           "content": content, "distance": float(distance),
                           "accepted": distance <= MAX_DISTANCE})
    log_path = PROJECT_ROOT / "work" / "policy-retrieval.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                 "query": query, "candidates": candidates}, ensure_ascii=False) + "\n")
    return [{key: value for key, value in item.items() if key != "accepted"}
            for item in candidates if item["accepted"]]


def search_policies(query: str, top_k: int = 3, strategy: str | None = None) -> list[dict]:
    strategy = strategy or os.getenv("SUPPORTPILOT_RETRIEVAL_STRATEGY", "vector")
    if strategy == "vector":
        return search_policies_vector(query, top_k)
    if not query.strip():
        return []
    if not (index_path() / "chroma.sqlite3").is_file():
        raise FileNotFoundError("政策索引尚未初始化，请运行 python scripts/init_knowledge.py。")
    from business.rag_retrieval import retrieve
    collection = chromadb.PersistentClient(path=str(index_path())).get_collection(
        COLLECTION_NAME, embedding_function=embedding_function())
    report = retrieve(collection, query, strategy, top_k,
                      pool_size=int(os.getenv("SUPPORTPILOT_RAG_POOL_SIZE", "12")),
                      rerank_threshold=float(os.getenv("SUPPORTPILOT_RERANK_THRESHOLD", "0")))
    log_path = PROJECT_ROOT / "work/policy-retrieval.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "query": query,
                                 "strategy": strategy, **report}, ensure_ascii=False) + "\n")
    return report["matches"]
