"""Durable MySQL ingestion leases; retries upsert immutable version IDs."""

import argparse
import hashlib
import logging
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import and_, delete, or_, select, update

from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.document_parsing import IngestionError, chunks_from_blocks, parse_document
from workbench.knowledge_index import ChromaIndex
from workbench.knowledge_models import Document, DocumentVersion, IngestionJob, KnowledgeChunk
from workbench.knowledge_service import stored_file
from workbench.models import identifier, now

logger = logging.getLogger(__name__)


def claim_job(database):
    with database.sessions() as session:
        exhausted = session.scalars(
            select(IngestionJob)
            .where(
                IngestionJob.status == "processing",
                IngestionJob.lease_until < now(),
                IngestionJob.attempts >= 3,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for item in exhausted:
            item.status, item.error_code = "failed", "retry_limit_reached"
            item.lease_token = item.lease_until = None
            version = session.get(DocumentVersion, item.version_id)
            version.status, version.error_code = "failed", "retry_limit_reached"
        session.flush()
        job = session.scalar(
            select(IngestionJob)
            .where(
                or_(
                    IngestionJob.status == "queued",
                    and_(IngestionJob.status == "processing", IngestionJob.lease_until < now()),
                ),
                IngestionJob.attempts < 3,
            )
            .order_by(IngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            session.commit()
            return None
        token = identifier()
        previous_status, previous_attempts = job.status, job.attempts
        previous_token = job.lease_token
        claimed = session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job.id,
                IngestionJob.status == previous_status,
                IngestionJob.attempts == previous_attempts,
                IngestionJob.lease_token == previous_token,
            )
            .values(
                status="processing",
                lease_token=token,
                lease_until=now() + timedelta(minutes=5),
                updated_at=now(),
                attempts=previous_attempts + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            session.rollback()
            return None
        session.refresh(job)
        version = session.get(DocumentVersion, job.version_id)
        version.status, version.error_code = "processing", None
        session.commit()
        return job.id, token


def process_job(database, settings, job_id, lease_token, index):
    def heartbeat():
        with database.sessions() as session:
            job = session.scalar(
                select(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.lease_token == lease_token,
                    IngestionJob.status == "processing",
                )
                .with_for_update()
            )
            if job is None:
                raise IngestionError("lease_lost")
            job.lease_until, job.updated_at = now() + timedelta(minutes=5), now()
            session.commit()

    try:
        with database.sessions() as session:
            job = session.get(IngestionJob, job_id)
            if not job or job.lease_token != lease_token:
                return
            version = session.get(DocumentVersion, job.version_id)
            path = stored_file(settings, version.storage_key)
            organization_id, version_id = version.organization_id, version.id
            extension = Path(version.filename).suffix.lower()
            if hashlib.sha256(path.read_bytes()).hexdigest() != version.content_hash:
                raise IngestionError("source_hash_mismatch")
            blocks, metadata = parse_document(path, extension)
            chunks = chunks_from_blocks(
                blocks, version.chunk_strategy, version.chunk_chars, version.chunk_overlap
            )
            for chunk in chunks:
                chunk["id"] = version.id + "-" + str(chunk["position"])
        heartbeat()
        index.upsert(organization_id, version_id, chunks, heartbeat)
        with database.sessions() as session:
            job = session.scalar(
                select(IngestionJob)
                .where(
                    IngestionJob.id == job_id,
                    IngestionJob.lease_token == lease_token,
                    IngestionJob.status == "processing",
                )
                .with_for_update()
            )
            if job is None:
                return
            version = session.get(DocumentVersion, version_id)
            document = session.get(Document, version.document_id)
            if document.active_version_id == version_id:
                raise IngestionError("published_version_is_immutable")
            session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.organization_id == organization_id,
                    KnowledgeChunk.version_id == version_id,
                )
            )
            for chunk in chunks:
                session.add(
                    KnowledgeChunk(organization_id=organization_id, version_id=version_id, **chunk)
                )
            version.status, version.chunk_count = "ready", len(chunks)
            version.parse_metadata, version.embedding_revision = metadata, index.revision
            job.status, job.error_code = "ready", None
            job.lease_token = job.lease_until = None
            job.updated_at = now()
            session.commit()
    except Exception as exc:
        code = exc.code if isinstance(exc, IngestionError) else "ingestion_failed"
        logger.error("Ingestion job %s failed: %s", job_id, code)
        with database.sessions() as session:
            job = session.scalar(
                select(IngestionJob)
                .where(IngestionJob.id == job_id, IngestionJob.lease_token == lease_token)
                .with_for_update()
            )
            if job:
                job.status, job.error_code = "failed", code
                job.lease_token = job.lease_until = None
                version = session.get(DocumentVersion, job.version_id)
                version.status, version.error_code = "failed", code
                session.commit()


def work_once(database, settings, index):
    claimed = claim_job(database)
    if not claimed:
        return False
    process_job(database, settings, *claimed, index)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = WorkbenchSettings()
    database = Database(settings)
    database.check_schema()
    index = ChromaIndex(settings)
    # Numerical dependencies are loaded on the process main thread on Windows.
    index.warm(rerank=False)
    try:
        while True:
            processed = work_once(database, settings, index)
            if args.once:
                return
            if not processed:
                time.sleep(1)
    finally:
        database.close()


if __name__ == "__main__":
    main()
