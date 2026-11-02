"""Application-facing vector index contract. SQL remains the evidence authority."""

import hashlib
import re
from collections.abc import Callable
from typing import Protocol


class VectorStoreError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class VectorIndex(Protocol):
    backend: str
    revision: str

    def warm(self, rerank: bool = True) -> None: ...
    def upsert(
        self,
        organization_id: str,
        version_id: str,
        chunks: list[dict],
        heartbeat: Callable[[], None],
    ) -> None: ...
    def query(
        self, organization_id: str, version_ids: list[str], query: str, limit: int
    ) -> list[dict]: ...
    def fetch(self, organization_id: str, version_id: str, ids: list[str]) -> dict[str, str]: ...
    def is_empty(self) -> bool: ...


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def namespace(value):
    if value and not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value):
        raise ValueError("Invalid vector namespace")
    return value


def create_index(settings) -> VectorIndex:
    namespace(settings.vector_namespace)
    if settings.vector_backend == "chroma":
        from workbench.knowledge_index import ChromaIndex

        return ChromaIndex(settings)
    if settings.vector_backend == "qdrant":
        from workbench.qdrant_index import QdrantIndex

        return QdrantIndex(settings)
    raise ValueError("Unknown vector backend; implicit fallback is forbidden")
