"""Functional adapter checks; no quality or throughput comparison."""

import json

import httpx
import pytest
from helpers import MemoryIndex

from workbench.config import WorkbenchSettings
from workbench.ingestion import work_once
from workbench.qdrant_index import QdrantIndex
from workbench.vector_admin import rebuild
from workbench.vector_store import VectorStoreError, content_hash


class CandidateIndex(MemoryIndex):
    backend = "qdrant"

    def is_empty(self):
        return not self.rows

    def warm(self, rerank=True):
        pass

    def fetch(self, org, version, ids):
        return {
            key: content_hash(row["content"])
            for key, row in self.rows.items()
            if key in ids and row["organization_id"] == org and row["version_id"] == version
        }


def test_qdrant_scope_distance_and_metadata_contract():
    calls = []

    def serve(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.url.path, body))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "config": {"params": {"vectors": {"size": 512, "distance": "Cosine"}}}
                    },
                },
            )
        if request.url.path.endswith("/query"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "points": [
                            {
                                "score": 0.75,
                                "payload": {
                                    "organization_id": "org-a",
                                    "version_id": "v1",
                                    "chunk_id": "same-original-id",
                                    "embedding_revision": QdrantIndex.revision,
                                },
                            }
                        ]
                    },
                },
            )
        raise AssertionError("unexpected operation")

    index = QdrantIndex(
        WorkbenchSettings(vector_backend="qdrant"),
        encoder=lambda texts: [[1.0] + [0.0] * 511 for _ in texts],
        transport=httpx.MockTransport(serve),
    )
    result = index.query("org-a", ["v1"], "private-question", 3)
    assert result == [{"id": "same-original-id", "distance": 0.25}]
    assert calls[-1][1]["filter"]["must"] == [
        {"key": "organization_id", "match": {"value": "org-a"}},
        {"key": "version_id", "match": {"any": ["v1"]}},
    ]
    assert "private-question" not in json.dumps(calls)
    assert index.query("org-a", [], "question", 3) == []


def test_qdrant_rejects_scope_violation_and_does_not_fallback():
    def serve(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "config": {"params": {"vectors": {"size": 512, "distance": "Cosine"}}}
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "result": {
                    "points": [
                        {
                            "score": 0.9,
                            "payload": {
                                "organization_id": "other-merchant",
                                "version_id": "v1",
                                "chunk_id": "foreign",
                                "embedding_revision": QdrantIndex.revision,
                            },
                        }
                    ]
                },
            },
        )

    index = QdrantIndex(
        WorkbenchSettings(),
        encoder=lambda texts: [[1.0] + [0.0] * 511],
        transport=httpx.MockTransport(serve),
    )
    with pytest.raises(VectorStoreError, match="vector_scope_mismatch"):
        index.query("org-a", ["v1"], "x", 2)
    index.transport = httpx.MockTransport(
        lambda request: httpx.Response(503, text="private-service-details")
    )
    with pytest.raises(VectorStoreError, match="^vector_unavailable$"):
        index.query("org-a", ["v1"], "x", 2)
    index.encoder = lambda texts: [[1.0, 0.0]]
    with pytest.raises(VectorStoreError, match="vector_embedding_mismatch"):
        index.encode(["x"])


def test_configuration_rejects_unsafe_names_and_unknown_backends():
    with pytest.raises(ValueError):
        WorkbenchSettings(vector_namespace="../live")
    with pytest.raises(ValueError):
        WorkbenchSettings(vector_backend="automatic")
    with pytest.raises(ValueError):
        WorkbenchSettings(qdrant_url="http://password:secret@example.test")


def source_case(case):
    client, app, headers = case
    response = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={"title": "演示退货条款", "policy_scope": "returns"},
        files={
            "file": ("policy.md", "未使用的演示商品在签收7天内可申请。".encode(), "text/markdown")
        },
    )
    assert response.status_code == 201, response.text
    version = response.json()["version"]
    index = CandidateIndex()
    assert work_once(app.state.database, app.state.settings, index)
    response = client.post(
        "/api/v1/knowledge/versions/" + version["id"] + "/publish",
        headers=headers("admin"),
        json={},
    )
    assert response.status_code == 200, response.text
    return version, index


def test_rebuild_reads_published_chunks_without_switching_or_overwriting(logged_in):
    client, app, headers = logged_in
    version, _ = source_case(logged_in)
    candidate = CandidateIndex()
    result = rebuild(
        app.state.settings,
        backend="qdrant",
        target_namespace="rebuild_feature",
        expected_database="business.sqlite3",
        index_factory=lambda s: candidate,
    )
    assert result["state"] == "ready_for_manual_switch" and result["versions"] == 1
    assert not result["source_database_modified"] and not result["active_configuration_modified"]
    assert app.state.settings.vector_backend == "chroma"
    with pytest.raises(VectorStoreError, match="vector_target_not_empty"):
        rebuild(
            app.state.settings,
            backend="qdrant",
            target_namespace="rebuild_feature",
            expected_database="business.sqlite3",
            index_factory=lambda s: candidate,
        )
    with pytest.raises(ValueError):
        rebuild(
            app.state.settings,
            backend="qdrant",
            target_namespace="",
            expected_database="business.sqlite3",
            index_factory=lambda s: candidate,
        )
    with pytest.raises(ValueError):
        rebuild(
            app.state.settings,
            backend="qdrant",
            target_namespace="rebuild_wrong",
            expected_database="wrong-db",
            index_factory=lambda s: CandidateIndex(),
        )
    status = client.get("/api/v1/knowledge/vector-storage", headers=headers("admin"))
    assert status.json()["backend"] == "chroma" and "url" not in status.text
    assert client.get("/api/v1/knowledge/vector-storage", headers=headers()).status_code == 403


def test_rebuild_rejects_publication_change_during_write(logged_in):
    client, app, headers = logged_in
    version, _ = source_case(logged_in)

    class UpdatingIndex(CandidateIndex):
        def upsert(self, *args):
            super().upsert(*args)
            response = client.post(
                "/api/v1/knowledge/documents/" + version["document_id"] + "/withdraw",
                headers=headers("admin"),
                json={},
            )
            assert response.status_code == 200

    with pytest.raises(VectorStoreError, match="vector_source_changed"):
        rebuild(
            app.state.settings,
            backend="qdrant",
            target_namespace="rebuild_changed",
            expected_database="business.sqlite3",
            index_factory=lambda s: UpdatingIndex(),
        )


def test_missing_candidate_index_is_not_silently_created_for_queries():
    index = QdrantIndex(
        WorkbenchSettings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={"status": "error"})
        ),
    )
    with pytest.raises(VectorStoreError, match="vector_index_not_ready"):
        index.query("org-a", ["published-version"], "question", 2)
