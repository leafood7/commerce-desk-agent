"""Chroma is a dedicated process; SQL publication is the permission authority."""

import hashlib
from functools import lru_cache
from urllib.parse import urlparse

EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_REVISION = "7999e1d3359715c523056ef9478215996d62a620"


@lru_cache(maxsize=1)
def embedding_function():
    import torch

    torch.set_num_threads(4)
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    from workbench.config import ROOT

    return SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL,
        device="cpu",
        normalize_embeddings=True,
        revision=EMBEDDING_REVISION,
        local_files_only=True,
        trust_remote_code=False,
        cache_folder=str(ROOT / "work/huggingface/hub"),
    )


class ChromaIndex:
    revision = EMBEDDING_REVISION

    def __init__(self, settings):
        import chromadb

        parsed = urlparse(settings.chroma_url)
        self.client = chromadb.HttpClient(
            host=parsed.hostname,
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            ssl=parsed.scheme == "https",
        )

    def warm(self, rerank=True):
        embedding_function()
        if rerank:
            from business.rag_retrieval import reranker

            reranker()

    def collection(self, organization_id):
        suffix = hashlib.sha256(EMBEDDING_REVISION.encode()).hexdigest()[:8]
        return self.client.get_or_create_collection(
            "wb_" + organization_id.replace("-", "") + "_" + suffix,
            embedding_function=embedding_function(),
            metadata={"hnsw:space": "cosine", "embedding_revision": self.revision},
        )

    def upsert(self, organization_id, version_id, chunks, heartbeat):
        collection = self.collection(organization_id)
        for start in range(0, len(chunks), 32):
            batch = chunks[start : start + 32]
            collection.upsert(
                ids=[row["id"] for row in batch],
                documents=[row["content"] for row in batch],
                metadatas=[
                    {"organization_id": organization_id, "version_id": version_id} for row in batch
                ],
            )
            heartbeat()
        stored = collection.get(ids=[row["id"] for row in chunks], include=[])
        if len(stored["ids"]) != len(chunks):
            raise RuntimeError("Vector index did not acknowledge all chunks")

    def query(self, organization_id, version_ids, query, limit):
        if not version_ids:
            return []
        collection = self.collection(organization_id)
        where = {
            "$and": [
                {"organization_id": organization_id},
                {"version_id": {"$in": list(version_ids)}},
            ]
        }
        result = collection.query(
            query_texts=[query], where=where, n_results=limit, include=["distances"]
        )
        return [
            {"id": key, "distance": float(distance)}
            for key, distance in zip(result["ids"][0], result["distances"][0])
        ]
