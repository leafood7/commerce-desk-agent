"""Chroma is a dedicated process; SQL publication is the permission authority."""

import hashlib
from functools import lru_cache
from urllib.parse import urlparse

from workbench.tracing import traced
from workbench.vector_store import VectorStoreError, content_hash, namespace

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
    backend = "chroma"
    revision = EMBEDDING_REVISION

    def __init__(self, settings):
        import chromadb

        self.namespace = namespace(settings.vector_namespace)
        self.prefix = "wb_" + (self.namespace + "_" if self.namespace else "")
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

    def collection(self, organization_id, *, create=True):
        from chromadb.errors import NotFoundError

        suffix = hashlib.sha256(EMBEDDING_REVISION.encode()).hexdigest()[:8]
        name = self.prefix + organization_id.replace("-", "") + "_" + suffix
        try:
            if create:
                collection = self.client.get_or_create_collection(
                    name,
                    embedding_function=embedding_function(),
                    metadata={"hnsw:space": "cosine", "embedding_revision": self.revision},
                )
            else:
                collection = self.client.get_collection(
                    name, embedding_function=embedding_function()
                )
        except NotFoundError:
            if create:
                raise
            return None

        if (collection.metadata or {}).get("embedding_revision") != self.revision:
            raise VectorStoreError("vector_collection_mismatch")
        return collection

    @traced("vector_store", "chroma.upsert")
    def upsert(self, organization_id, version_id, chunks, heartbeat):
        if not chunks:
            return
        if len({row["id"] for row in chunks}) != len(chunks):
            raise VectorStoreError("vector_duplicate_id")
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
            raise VectorStoreError("vector_write_not_confirmed")
        if self.fetch(organization_id, version_id, [row["id"] for row in chunks]) != {
            row["id"]: content_hash(row["content"]) for row in chunks
        }:
            raise VectorStoreError("vector_write_not_confirmed")

    @traced("vector_store", "chroma.query")
    def query(self, organization_id, version_ids, query, limit):
        if not version_ids:
            return []
        collection = self.collection(organization_id, create=False)
        if collection is None:
            raise VectorStoreError("vector_index_not_ready")
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

    @traced("vector_store", "chroma.fetch")
    def fetch(self, organization_id, version_id, ids):
        if not ids:
            return {}
        collection = self.collection(organization_id, create=False)
        if collection is None:
            return {}
        result = {}
        where = {"$and": [{"organization_id": organization_id}, {"version_id": version_id}]}
        for start in range(0, len(ids), 128):
            stored = collection.get(
                ids=ids[start : start + 128], where=where, include=["documents", "metadatas"]
            )
            for key, text, metadata in zip(stored["ids"], stored["documents"], stored["metadatas"]):
                if (
                    metadata.get("organization_id") != organization_id
                    or metadata.get("version_id") != version_id
                ):
                    raise VectorStoreError("vector_scope_mismatch")
                result[key] = content_hash(text)
        return result

    def is_empty(self):
        suffix = "_" + hashlib.sha256(self.revision.encode()).hexdigest()[:8]
        return not any(
            (c if isinstance(c, str) else c.name).startswith(self.prefix)
            and (c if isinstance(c, str) else c.name).endswith(suffix)
            for c in self.client.list_collections()
        )
