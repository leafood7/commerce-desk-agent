"""Cached published SQL snapshots, hybrid retrieval and late publication validation."""

from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter

from sqlalchemy import or_, select

from business.rag_retrieval import BM25Index, reciprocal_rank_fusion, rerank
from workbench.evidence_context import expand_context
from workbench.knowledge_models import Document, DocumentVersion, KnowledgeChunk
from workbench.reference_context import expand_references


class KnowledgeRetriever:
    def __init__(self, database, settings, index):
        self.database, self.settings, self.index = database, settings, index
        self.cache = OrderedDict()
        self.lock = Lock()
        self.snapshot_builds = 0

    def active_versions(self, session, organization_id, policy_scope, version_id=None):
        today = datetime.now(UTC).date()
        statement = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
            .where(
                Document.organization_id == organization_id,
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.status == "ready",
                or_(
                    DocumentVersion.effective_from.is_(None),
                    DocumentVersion.effective_from <= today,
                ),
                or_(
                    DocumentVersion.effective_until.is_(None),
                    DocumentVersion.effective_until >= today,
                ),
            )
        )
        if policy_scope:
            statement = statement.where(
                DocumentVersion.policy_scope.in_(["unknown", "general", policy_scope])
            )
        if version_id:
            statement = statement.where(DocumentVersion.id == version_id)
        return session.execute(statement).all()

    def snapshot(self, organization_id, policy_scope, version_id=None):
        with self.database.sessions() as session:
            versions = self.active_versions(session, organization_id, policy_scope, version_id)
            ids = tuple(sorted(version.id for _, version in versions))
            key = organization_id, policy_scope, ids
            with self.lock:
                cached = self.cache.get(key)
                if cached:
                    self.cache.move_to_end(key)
                    return ids, cached
            if not ids:
                return ids, ([], BM25Index([]))
            metadata = {version.id: (document, version) for document, version in versions}
            rows = session.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.organization_id == organization_id,
                    KnowledgeChunk.version_id.in_(ids),
                )
                .order_by(KnowledgeChunk.version_id, KnowledgeChunk.position)
            )
            documents = []
            for row in rows:
                document, version = metadata[row.version_id]
                documents.append(
                    {
                        "id": row.id,
                        "source": version.id,
                        "content": row.content,
                        "version_id": version.id,
                        "version": version.number,
                        "document_id": document.id,
                        "title": document.title,
                        "filename": version.filename,
                        "policy_scope": version.policy_scope,
                        "page_start": row.page_start,
                        "page_end": row.page_end,
                        "section": row.section,
                        "position": row.position,
                    }
                )
            cached = documents, BM25Index(documents)
        with self.lock:
            self.snapshot_builds += 1
            self.cache[key] = cached
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        return ids, cached

    def search(self, organization_id, request):
        start = perf_counter()
        version_ids, (documents, sparse_index) = self.snapshot(
            organization_id, request.policy_scope, request.version_id
        )
        if not version_ids:
            return {
                "status": "no_evidence",
                "matches": [],
                "coverage": "no_published_documents",
                "trace": {"version_ids": [], "strategy": request.strategy},
            }
        pool = max(12, request.top_k)
        by_id = {row["id"]: row for row in documents}
        dense_raw = self.index.query(
            organization_id, version_ids, request.query, min(pool, len(documents))
        )
        dense = [
            {**by_id[row["id"]], "distance": row["distance"]}
            for row in dense_raw
            if row["id"] in by_id and row["distance"] <= self.settings.vector_max_distance
        ]
        sparse = sparse_index.search(request.query, pool)
        fusion = reciprocal_rank_fusion([dense, sparse], pool)
        if request.strategy == "vector":
            ranked = dense
        elif request.strategy == "hybrid":
            ranked = fusion
        else:
            ranked = [
                row
                for row in rerank(request.query, fusion)
                if row["rerank_score"] >= self.settings.rerank_threshold
            ]
        anchors = []
        total_chars = 0
        for candidate in ranked[: request.top_k]:
            if total_chars + len(candidate["content"]) <= self.settings.context_chars:
                anchors.append(candidate)
                total_chars += len(candidate["content"])
        reference_trace = None
        if self.settings.retrieval_context_mode == "references":
            expanded = expand_references(anchors, documents, self.settings.context_chars)
            selected, reference_trace = expanded["matches"], expanded["trace"]
        else:
            selected = expand_context(
                anchors,
                documents,
                self.settings.context_chars,
                self.settings.retrieval_context_mode,
            )
        # A new transaction avoids returning an old publication snapshot after inference.
        with self.database.sessions() as session:
            current = {
                version.id
                for _, version in self.active_versions(
                    session, organization_id, request.policy_scope, request.version_id
                )
            }
        selected = [row for row in selected if row["version_id"] in current]
        return {
            "status": "found" if selected else "no_evidence",
            "matches": selected,
            "coverage": "retrieved_evidence_only" if selected else "no_supported_match",
            "trace": {
                "strategy": request.strategy,
                "context_mode": self.settings.retrieval_context_mode,
                "reference_expansion": reference_trace,
                "version_ids": list(version_ids),
                "dense_ids": [row["id"] for row in dense],
                "sparse_ids": [row["id"] for row in sparse],
                "fusion_ids": [row["id"] for row in fusion],
                "anchor_ids": [row["id"] for row in anchors],
                "elapsed_ms": (perf_counter() - start) * 1000,
                "snapshot_builds": self.snapshot_builds,
            },
        }
