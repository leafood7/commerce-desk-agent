"""Vector maintenance: rebuild immutable published SQL chunks into a fresh namespace."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select

from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.knowledge_models import KnowledgeChunk
from workbench.knowledge_service import stored_file
from workbench.models import Organization
from workbench.retrieval import KnowledgeRetriever
from workbench.vector_store import VectorStoreError, content_hash, create_index


def inventory(database, settings, index, max_chunks):
    retriever = KnowledgeRetriever(database, settings, index)
    result = []
    count = 0
    with database.sessions() as session:
        orgs = session.scalars(select(Organization.id).order_by(Organization.id)).all()
        for org in orgs:
            for doc, version in retriever.active_versions(session, org, None):
                if version.embedding_revision != index.revision:
                    raise VectorStoreError("vector_embedding_revision_mismatch")
                try:
                    source_bytes = stored_file(settings, version.storage_key).read_bytes()
                except OSError:
                    raise VectorStoreError("vector_source_file_missing") from None
                if content_hash_bytes(source_bytes) != version.content_hash:
                    raise VectorStoreError("vector_source_file_mismatch")
                rows = session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.organization_id == org,
                        KnowledgeChunk.version_id == version.id,
                    )
                    .order_by(KnowledgeChunk.id)
                    .limit(max_chunks - count + 1)
                ).all()
                count += len(rows)
                if count > max_chunks:
                    raise VectorStoreError("vector_rebuild_limit")
                if len(rows) != version.chunk_count:
                    raise VectorStoreError("vector_source_chunks_incomplete")
                if not rows:
                    raise VectorStoreError("vector_source_chunks_missing")
                result.append(
                    {
                        "organization_id": org,
                        "document_id": doc.id,
                        "version_id": version.id,
                        "annotation_id": (version.parse_metadata or {}).get("policy_extraction_id"),
                        "chunks": [{"id": x.id, "content": x.content} for x in rows],
                    }
                )
    return sorted(result, key=lambda x: (x["organization_id"], x["version_id"]))


def content_hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(items):
    records = [
        {
            **{k: v for k, v in x.items() if k != "chunks"},
            "chunks": [{"id": r["id"], "sha256": content_hash(r["content"])} for r in x["chunks"]],
        }
        for x in items
    ]
    return content_hash(json.dumps(records, ensure_ascii=False, sort_keys=True))


def rebuild(
    settings,
    *,
    backend,
    target_namespace,
    expected_database,
    max_chunks=50000,
    index_factory=create_index,
):
    if not re.fullmatch(r"(?:rebuild|restore)_[a-z0-9_]{1,24}", target_namespace):
        raise ValueError("Use a fresh rebuild_ or restore_ namespace")
    if not 1 <= max_chunks <= 100000:
        raise ValueError("Invalid chunk bound")
    configured = type(settings).model_validate(
        {**settings.model_dump(), "vector_backend": backend, "vector_namespace": target_namespace}
    )
    database = Database(settings)
    try:
        database.check_schema()
        actual = database.engine.url.database
        if database.engine.url.drivername == "sqlite":
            actual = Path(actual).name
        if not expected_database or actual != expected_database:
            raise ValueError("Source database confirmation does not match")
        index = index_factory(configured)
        if not index.is_empty():
            raise VectorStoreError("vector_target_not_empty")
        before = inventory(database, settings, index, max_chunks)
        if not before:
            raise VectorStoreError("vector_source_empty")
        frozen = fingerprint(before)
        index.warm(rerank=False)
        for item in before:
            index.upsert(item["organization_id"], item["version_id"], item["chunks"], lambda: None)
            if index.fetch(
                item["organization_id"], item["version_id"], [r["id"] for r in item["chunks"]]
            ) != {r["id"]: content_hash(r["content"]) for r in item["chunks"]}:
                raise VectorStoreError("vector_rebuild_not_confirmed")
        if fingerprint(inventory(database, settings, index, max_chunks)) != frozen:
            raise VectorStoreError("vector_source_changed")
        return {
            "state": "ready_for_manual_switch",
            "backend": backend,
            "namespace": target_namespace,
            "embedding_revision": index.revision,
            "source_inventory_sha256": frozen,
            "versions": len(before),
            "chunks": sum(len(x["chunks"]) for x in before),
            "source_database_modified": False,
            "active_configuration_modified": False,
            "provider_calls": 0,
        }
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    build = sub.add_parser("rebuild")
    build.add_argument("--backend", choices=["chroma", "qdrant"], required=True)
    build.add_argument("--target-namespace", required=True)
    build.add_argument("--expected-database", required=True)
    build.add_argument("--max-chunks", type=int, default=50000)
    build.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = WorkbenchSettings()
    try:
        if args.action == "status":
            index = create_index(settings)
            result = {
                "backend": index.backend,
                "namespace": settings.vector_namespace,
                "embedding_revision": index.revision,
                "namespace_unused": index.is_empty(),
                "note": "Namespace presence alone does not establish readiness",
            }
        else:
            if args.output.exists():
                raise ValueError("Do not overwrite an existing rebuild manifest")
            result = rebuild(
                settings,
                backend=args.backend,
                target_namespace=args.target_namespace,
                expected_database=args.expected_database,
                max_chunks=args.max_chunks,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
    except (VectorStoreError, ValueError) as exc:
        # Never print credentials, endpoint URLs, response bodies or connection strings.
        parser.exit(
            1,
            "Vector maintenance stopped: "
            + (
                exc.code
                if isinstance(exc, VectorStoreError)
                else "invalid_configuration_or_confirmation"
            )
            + "\n",
        )


if __name__ == "__main__":
    main()
