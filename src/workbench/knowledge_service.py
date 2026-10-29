"""Version lifecycle and file access; retrieval publication is an explicit transaction."""

import hashlib
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select

from workbench.config import ROOT
from workbench.document_parsing import IngestionError, validate_container
from workbench.domain import audit, fingerprint
from workbench.knowledge_models import Document, DocumentVersion, IngestionJob, KnowledgeChunk
from workbench.models import Organization, identifier, now


def storage_root(settings):
    value = Path(settings.storage_root)
    return (value if value.is_absolute() else ROOT / value).resolve()


def stored_file(settings, key):
    root = storage_root(settings)
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("File escaped configured storage")
    return path


def scoped_document(session, actor, document_id, *, lock=False):
    statement = select(Document).where(
        Document.id == document_id, Document.organization_id == actor.organization_id
    )
    document = session.scalar(statement.with_for_update() if lock else statement)
    if not document:
        raise HTTPException(404, "Document not found")
    return document


def scoped_version(session, actor, version_id):
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.organization_id == actor.organization_id,
        )
    )
    if version is None:
        raise HTTPException(404, "Version not found")
    return version


_UNSET = object()


def version_view(session, document, version, job=_UNSET):
    if job is _UNSET:
        job = session.scalar(select(IngestionJob).where(IngestionJob.version_id == version.id))
    return {
        "id": version.id,
        "document_id": document.id,
        "number": version.number,
        "filename": version.filename,
        "status": version.status,
        "published": document.active_version_id == version.id,
        "policy_scope": version.policy_scope,
        "chunk_strategy": version.chunk_strategy,
        "chunk_chars": version.chunk_chars,
        "chunk_overlap": version.chunk_overlap,
        "chunk_count": version.chunk_count,
        "effective_from": version.effective_from,
        "effective_until": version.effective_until,
        "parse_metadata": version.parse_metadata,
        "error_code": version.error_code,
        "job": {"id": job.id, "status": job.status, "attempts": job.attempts} if job else None,
    }


def create_version(
    session, actor, settings, filename, content, options, *, title=None, document_id=None
):
    if actor.role != "admin":
        raise HTTPException(403, "Role is not permitted")
    if not content or len(content) > settings.upload_limit_bytes:
        raise HTTPException(413, "File is empty or exceeds upload limit")
    filename = re.split(r"[/\\]", filename or "")[-1]
    if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Invalid filename")
    extension = Path(filename).suffix.lower()
    if extension not in {".txt", ".md", ".pdf", ".docx"}:
        raise HTTPException(422, "Supported files: TXT, Markdown, text PDF, DOCX")
    content_hash = hashlib.sha256(content).hexdigest()
    config_hash = fingerprint(options.model_dump(mode="json"))
    # Serialize uploads within one organization to make network retries deduplicate.
    session.scalar(
        select(Organization).where(Organization.id == actor.organization_id).with_for_update()
    )
    if not document_id:
        duplicate = session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.organization_id == actor.organization_id,
                DocumentVersion.content_hash == content_hash,
                DocumentVersion.config_hash == config_hash,
            )
            .with_for_update()
        )
        if duplicate:
            existing_document = session.scalar(
                select(Document).where(Document.id == duplicate.document_id).with_for_update()
            )
            session.commit()
            return existing_document, duplicate, False
    if document_id:
        document = scoped_document(session, actor, document_id, lock=True)
    else:
        document = Document(
            id=identifier(),
            organization_id=actor.organization_id,
            title=title or filename,
            created_by=actor.id,
        )
        session.add(document)
        session.flush()
    content_hash = hashlib.sha256(content).hexdigest()
    config_hash = fingerprint(options.model_dump(mode="json"))
    existing = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.content_hash == content_hash,
            DocumentVersion.config_hash == config_hash,
        )
    )
    if existing:
        return document, existing, False
    next_number = (
        session.scalar(
            select(func.max(DocumentVersion.number)).where(
                DocumentVersion.document_id == document.id
            )
        )
        or 0
    ) + 1
    version_id = identifier()
    key = f"{actor.organization_id}/{document.id}/{version_id}/original{extension}"
    path = stored_file(settings, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    try:
        validate_container(path, extension)
    except IngestionError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(422, exc.code) from exc
    version = DocumentVersion(
        id=version_id,
        organization_id=actor.organization_id,
        document_id=document.id,
        number=next_number,
        filename=filename,
        storage_key=key,
        content_hash=content_hash,
        config_hash=config_hash,
        **options.model_dump(),
    )
    session.add(version)
    try:
        session.flush()
        session.add(IngestionJob(organization_id=actor.organization_id, version_id=version.id))
        audit(session, actor, "document.uploaded", version.id, document_id=document.id)
        session.commit()
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)
        raise
    return document, version, True


def publish(session, actor, version_id, acknowledge_warnings=False):
    version = scoped_version(session, actor, version_id)
    document = scoped_document(session, actor, version.document_id, lock=True)
    if version.status != "ready" or version.chunk_count < 1:
        raise HTTPException(409, "Version is not indexed and ready")
    if version.parse_metadata.get("warnings") and not acknowledge_warnings:
        raise HTTPException(409, "Review parser warnings before publishing")
    document.active_version_id = version.id
    version.published_at = now()
    audit(session, actor, "document.published", version.id, document_id=document.id)
    session.commit()
    return version_view(session, document, version)


def withdraw(session, actor, document_id):
    document = scoped_document(session, actor, document_id, lock=True)
    previous = document.active_version_id
    document.active_version_id = None
    audit(session, actor, "document.withdrawn", document.id, previous_version=previous)
    session.commit()
    return {"id": document.id, "active_version_id": None}


def retry(session, actor, version_id):
    version = scoped_version(session, actor, version_id)
    document = scoped_document(session, actor, version.document_id, lock=True)
    job = session.scalar(
        select(IngestionJob).where(IngestionJob.version_id == version.id).with_for_update()
    )
    if version.status != "failed" or document.active_version_id == version.id:
        raise HTTPException(409, "Only an unpublished failed version can be retried")
    if job.attempts >= 3:
        raise HTTPException(409, "Maximum ingestion attempts reached")
    job.status, job.error_code = "queued", None
    version.status, version.error_code = "queued", None
    audit(session, actor, "document.retried", version.id)
    session.commit()
    return version_view(session, document, version)


def citation(session, actor, chunk_id):
    chunk = session.scalar(
        select(KnowledgeChunk).where(
            KnowledgeChunk.id == chunk_id, KnowledgeChunk.organization_id == actor.organization_id
        )
    )
    if chunk is None:
        raise HTTPException(404, "Citation not found")
    version = scoped_version(session, actor, chunk.version_id)
    document = scoped_document(session, actor, version.document_id)
    return {
        "id": chunk.id,
        "content": chunk.content,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section": chunk.section,
        "title": document.title,
        "version_id": version.id,
        "version": version.number,
        "filename": version.filename,
        "currently_published": document.active_version_id == version.id,
    }


def validated_evidence(session, actor, matches):
    """Validate citations again at publication, rather than trusting transport fields."""
    from datetime import UTC, datetime

    from sqlalchemy import or_

    if not isinstance(matches, list) or len(matches) > 20:
        raise HTTPException(409, "Invalid evidence collection")
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in matches):
        raise HTTPException(409, "Invalid evidence identity")
    identifiers = {row["id"] for row in matches}
    if len(identifiers) != len(matches):
        raise HTTPException(409, "Duplicate evidence identity")
    if not identifiers:
        return []
    today = datetime.now(UTC).date()
    rows = session.execute(
        select(KnowledgeChunk, DocumentVersion, Document)
        .join(DocumentVersion, KnowledgeChunk.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(
            KnowledgeChunk.id.in_(identifiers),
            KnowledgeChunk.organization_id == actor.organization_id,
            Document.organization_id == actor.organization_id,
            Document.active_version_id == DocumentVersion.id,
            DocumentVersion.status == "ready",
            or_(DocumentVersion.effective_from.is_(None), DocumentVersion.effective_from <= today),
            or_(
                DocumentVersion.effective_until.is_(None), DocumentVersion.effective_until >= today
            ),
        )
    )
    allowed = {chunk.id: (chunk, version, document) for chunk, version, document in rows}
    if identifiers != set(allowed):
        raise HTTPException(409, "Evidence is unavailable or publication changed")
    result = []
    for item in matches:
        chunk, version, document = allowed[item["id"]]
        if item.get("content") != chunk.content or item.get("version_id") != version.id:
            raise HTTPException(409, "Evidence content does not match its source")
        result.append(
            {
                **item,
                "content": chunk.content,
                "version_id": version.id,
                "version": version.number,
                "document_id": document.id,
                "filename": version.filename,
                "title": document.title,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "section": chunk.section,
                "policy_scope": version.policy_scope,
            }
        )
    return result
