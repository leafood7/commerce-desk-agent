"""Rebuild ONLY a fresh recovery index from restored, immutable SQL chunks."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.engine import make_url

from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.knowledge_index import ChromaIndex
from workbench.knowledge_models import DocumentVersion, KnowledgeChunk
from workbench.knowledge_service import stored_file
from workbench.models import Organization
from workbench.retrieval import KnowledgeRetriever


def require_recovery(settings, expected_database):
    if not re.fullmatch(r"commercedesk_verify_[a-f0-9]{12}", expected_database):
        raise ValueError("Only a uniquely named verification database is allowed")
    url = make_url(settings.database_url.get_secret_value())
    if url.drivername != "mysql+pymysql" or url.database != expected_database:
        raise ValueError("Configured database does not match the recovery target")
    if urlparse(settings.chroma_url).hostname != "restore-chroma":
        raise ValueError("Recovery index must use the isolated restore-chroma service")


def rebuild(settings, expected_database):
    require_recovery(settings, expected_database)
    database = Database(settings)
    try:
        index = ChromaIndex(settings)
        # Fail before writes: never merge a restored SQL snapshot into a reused index.
        if index.client.list_collections():
            raise ValueError("Recovery index is not empty; preserve it and use a new target")
        versions = chunks = files = 0
        inventories = []
        retriever = KnowledgeRetriever(database, settings, index)
        with database.sessions() as session:
            for version in session.scalars(select(DocumentVersion)):
                raw = stored_file(settings, version.storage_key).read_bytes()
                if hashlib.sha256(raw).hexdigest() != version.content_hash:
                    raise ValueError("Restored original file differs from SQL hash")
                files += 1
            organizations = list(session.scalars(select(Organization.id)))
            batches = []
            for org in organizations:
                for _, version in retriever.active_versions(session, org, None):
                    rows = list(
                        session.scalars(
                            select(KnowledgeChunk)
                            .where(
                                KnowledgeChunk.organization_id == org,
                                KnowledgeChunk.version_id == version.id,
                            )
                            .order_by(KnowledgeChunk.position)
                        )
                    )
                    if not rows:
                        raise ValueError("Published version has no immutable SQL chunks")
                    batches.append(
                        (org, version.id, [{"id": row.id, "content": row.content} for row in rows])
                    )
        for org, version, rows in batches:
            index.upsert(org, version, rows, lambda: None)
            stored = index.collection(org).get(ids=[r["id"] for r in rows], include=["documents"])
            if dict(zip(stored["ids"], stored["documents"])) != {
                row["id"]: row["content"] for row in rows
            }:
                raise ValueError("Rebuilt vectors do not reference the restored SQL text")
            versions += 1
            chunks += len(rows)
            inventories.extend(rows)
        digest = hashlib.sha256(
            json.dumps(
                sorted(inventories, key=lambda r: r["id"]), ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        return {
            "database": expected_database,
            "fresh_index_verified": True,
            "original_files_verified": files,
            "published_versions_rebuilt": versions,
            "chunks_rebuilt": chunks,
            "chunk_inventory_sha256": digest,
            "embedding_revision": index.revision,
            "model_provider_calls": 0,
        }
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Existing recovery evidence will not be overwritten")
    result = rebuild(WorkbenchSettings(), args.expected_database)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
