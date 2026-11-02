"""First-party Qdrant HTTP adapter; local embeddings, no provider-side inference."""

import hashlib
import math
from uuid import NAMESPACE_URL, uuid5

import httpx

from workbench.knowledge_index import EMBEDDING_REVISION, embedding_function
from workbench.tracing import traced
from workbench.vector_store import VectorStoreError, content_hash, namespace


class QdrantIndex:
    backend = "qdrant"
    revision = EMBEDDING_REVISION
    dimensions = 512

    def __init__(self, settings, *, encoder=None, transport=None):
        self.settings = settings
        self.namespace = namespace(settings.vector_namespace)
        self.encoder = encoder
        self.transport = transport
        self.prefix = "wb_" + (self.namespace + "_" if self.namespace else "")
        self.suffix = "_" + hashlib.sha256(self.revision.encode()).hexdigest()[:8]

    def name(self, organization_id):
        return self.prefix + hashlib.sha256(organization_id.encode()).hexdigest()[:32] + self.suffix

    def request(self, method, path, body=None, *, allow=()):
        headers = {}
        if self.settings.qdrant_api_key:
            headers["api-key"] = self.settings.qdrant_api_key.get_secret_value()
        try:
            with httpx.Client(
                base_url=self.settings.qdrant_url.rstrip("/"),
                headers=headers,
                timeout=self.settings.vector_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.request(method, path, json=body)
            if response.status_code in allow:
                return response.status_code, None
            if not response.is_success:
                raise VectorStoreError(
                    "vector_unavailable"
                    if response.status_code >= 500
                    else "vector_request_rejected"
                )
            result = response.json()
            if not isinstance(result, dict) or result.get("status") != "ok":
                raise VectorStoreError("vector_invalid_response")
            return response.status_code, result.get("result")
        except VectorStoreError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise VectorStoreError("vector_unavailable") from None

    def encode(self, texts):
        encoder = self.encoder or embedding_function()
        vectors = encoder(texts)
        if len(vectors) != len(texts):
            raise VectorStoreError("vector_embedding_mismatch")
        result = []
        for vector in vectors:
            values = [float(v) for v in vector]
            if (
                len(values) != self.dimensions
                or not all(math.isfinite(v) for v in values)
                or not any(values)
            ):
                raise VectorStoreError("vector_embedding_mismatch")
            result.append(values)
        return result

    def warm(self, rerank=True):
        if self.encoder is None:
            embedding_function()
        if rerank:
            from business.rag_retrieval import reranker

            reranker()

    def collection(self, org, *, create=False):
        path = "/collections/" + self.name(org)
        status, info = self.request("GET", path, allow=(404,))
        if status == 404:
            if not create:
                return False
            self.request(
                "PUT",
                path,
                {"vectors": {"size": self.dimensions, "distance": "Cosine"}},
                allow=(400, 409),
            )
            _, info = self.request("GET", path)
            # Tenant/version filter indexes live in the candidate store, not SQL.
            for key in ("organization_id", "version_id"):
                self.request(
                    "PUT", path + "/index?wait=true", {"field_name": key, "field_schema": "keyword"}
                )
        vectors = (info or {}).get("config", {}).get("params", {}).get("vectors", {})
        if vectors.get("size") != self.dimensions or vectors.get("distance") != "Cosine":
            raise VectorStoreError("vector_collection_mismatch")
        return True

    @staticmethod
    def point_id(org, version, key):
        return str(uuid5(NAMESPACE_URL, org + "/" + version + "/" + key))

    @traced("vector_store", "qdrant.upsert")
    def upsert(self, organization_id, version_id, chunks, heartbeat):
        if not chunks:
            return
        if len({r["id"] for r in chunks}) != len(chunks):
            raise VectorStoreError("vector_duplicate_id")
        self.collection(organization_id, create=True)
        path = "/collections/" + self.name(organization_id) + "/points?wait=true"
        for start in range(0, len(chunks), 32):
            batch = chunks[start : start + 32]
            vectors = self.encode([r["content"] for r in batch])
            points = [
                {
                    "id": self.point_id(organization_id, version_id, r["id"]),
                    "vector": vector,
                    "payload": {
                        "organization_id": organization_id,
                        "version_id": version_id,
                        "chunk_id": r["id"],
                        "content_sha256": content_hash(r["content"]),
                        "embedding_revision": self.revision,
                    },
                }
                for r, vector in zip(batch, vectors)
            ]
            _, receipt = self.request("PUT", path, {"points": points})
            if not isinstance(receipt, dict) or receipt.get("status") != "completed":
                raise VectorStoreError("vector_write_not_confirmed")
            heartbeat()
            if self.fetch(organization_id, version_id, [r["id"] for r in batch]) != {
                r["id"]: content_hash(r["content"]) for r in batch
            }:
                raise VectorStoreError("vector_write_not_confirmed")

    @traced("vector_store", "qdrant.query")
    def query(self, organization_id, version_ids, query, limit):
        if not version_ids or limit <= 0:
            return []
        if limit > 100:
            raise ValueError("Vector result limit exceeds 100")
        if not self.collection(organization_id):
            raise VectorStoreError("vector_index_not_ready")
        where = {
            "must": [
                {"key": "organization_id", "match": {"value": organization_id}},
                {"key": "version_id", "match": {"any": list(version_ids)}},
            ]
        }
        _, data = self.request(
            "POST",
            "/collections/" + self.name(organization_id) + "/points/query",
            {
                "query": self.encode([query])[0],
                "filter": where,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("points"), list)
            or len(data["points"]) > limit
        ):
            raise VectorStoreError("vector_invalid_response")
        result = []
        for row in data["points"]:
            payload = row.get("payload") or {}
            if (
                payload.get("organization_id") != organization_id
                or payload.get("version_id") not in version_ids
                or payload.get("embedding_revision") != self.revision
            ):
                raise VectorStoreError("vector_scope_mismatch")
            score = row.get("score")
            if (
                type(score) not in (int, float)
                or not math.isfinite(score)
                or not -1.00001 <= score <= 1.00001
                or not isinstance(payload.get("chunk_id"), str)
            ):
                raise VectorStoreError("vector_invalid_response")
            result.append({"id": payload["chunk_id"], "distance": max(0.0, min(2.0, 1.0 - score))})
        return result

    @traced("vector_store", "qdrant.fetch")
    def fetch(self, organization_id, version_id, ids):
        if not ids or not self.collection(organization_id):
            return {}
        found = {}
        for start in range(0, len(ids), 128):
            batch = ids[start : start + 128]
            _, rows = self.request(
                "POST",
                "/collections/" + self.name(organization_id) + "/points",
                {
                    "ids": [self.point_id(organization_id, version_id, key) for key in batch],
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            if not isinstance(rows, list):
                raise VectorStoreError("vector_invalid_response")
            for row in rows:
                p = row.get("payload") or {}
                if (
                    p.get("organization_id") != organization_id
                    or p.get("version_id") != version_id
                    or p.get("embedding_revision") != self.revision
                    or p.get("chunk_id") not in batch
                ):
                    raise VectorStoreError("vector_scope_mismatch")
                digest = p.get("content_sha256")
                if not isinstance(digest, str) or len(digest) != 64:
                    raise VectorStoreError("vector_invalid_response")
                found[p["chunk_id"]] = digest
        return found

    def is_empty(self):
        _, result = self.request("GET", "/collections")
        if not isinstance(result, dict) or not isinstance(result.get("collections"), list):
            raise VectorStoreError("vector_invalid_response")
        return not any(
            r["name"].startswith(self.prefix) and r["name"].endswith(self.suffix)
            for r in result["collections"]
        )
