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
from workbench.tracing import traced


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
    from workbench.faq_sources import dependencies_current

    return {
        "source_review_required": not dependencies_current(
            session, version.organization_id, version.parse_metadata or {}
        ),
        "knowledge_kind": (version.parse_metadata or {}).get("knowledge_kind", "document"),
        "faq_sources": (version.parse_metadata or {}).get("faq_sources", []),
        "config_hash": version.config_hash,
        "id": version.id,
        "document_id": document.id,
        "number": version.number,
        "filename": version.filename,
        "status": version.status,
        "published": document.active_version_id == version.id,
        "published_at": version.published_at,
        "policy_scope": version.policy_scope,
        "chunk_strategy": version.chunk_strategy,
        "chunk_chars": version.chunk_chars,
        "chunk_overlap": version.chunk_overlap,
        "chunk_count": version.chunk_count,
        "effective_from": version.effective_from,
        "effective_until": version.effective_until,
        "parse_metadata": {
            **{
                k: v
                for k, v in (version.parse_metadata or {}).items()
                if k not in {"parent_child", "revision", "parse_snapshot"}
            },
            **(
                {
                    "revision": {
                        k: v
                        for k, v in version.parse_metadata["revision"].items()
                        if k not in {"changes", "patches"}
                    }
                }
                if (version.parse_metadata or {}).get("revision")
                else {}
            ),
            **(
                {
                    "parent_child": {
                        "schema": 1,
                        "parent_count": len(
                            version.parse_metadata["parent_child"].get("parents", [])
                        ),
                        "source_kind": (version.parse_metadata or {}).get(
                            "text_origin", "parsed_original"
                        ),
                    }
                }
                if (version.parse_metadata or {}).get("parent_child")
                else {}
            ),
        },
        "error_code": version.error_code,
        "job": {"id": job.id, "status": job.status, "attempts": job.attempts} if job else None,
    }


def create_version(
    session,
    actor,
    settings,
    filename,
    content,
    options,
    *,
    title=None,
    document_id=None,
    faq_review=None,
    commit=True,
):
    if actor.role != "admin":
        raise HTTPException(403, "Role is not permitted")
    if not content or len(content) > settings.upload_limit_bytes:
        raise HTTPException(413, "File is empty or exceeds upload limit")
    from workbench.faq_sources import check_faq

    initial_metadata = {
        "audience": options.audience.model_dump(exclude_none=True) if options.audience else {},
        "knowledge_kind": options.knowledge_kind or "document",
        "faq_sources": options.faq_sources or [],
    }
    if faq_review:
        if initial_metadata["knowledge_kind"] != "faq":
            raise HTTPException(422, "FAQ review context is only supported for FAQ")
        initial_metadata["faq_update_review"] = faq_review
    check_faq(session, actor.organization_id, initial_metadata)
    filename = re.split(r"[/\\]", filename or "")[-1]
    if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, "Invalid filename")
    extension = Path(filename).suffix.lower()
    if extension not in {".txt", ".md", ".pdf", ".docx"}:
        raise HTTPException(422, "Supported files: TXT, Markdown, text PDF, DOCX")
    content_hash = hashlib.sha256(content).hexdigest()
    config = options.model_dump(mode="json")
    for key in ("audience", "knowledge_kind", "faq_sources"):
        if config.get(key) in (None, {}, []) or (
            key == "knowledge_kind" and config[key] == "document"
        ):
            config.pop(key, None)
    if faq_review:
        config["faq_update_review"] = fingerprint(faq_review)
    config_hash = fingerprint(config)
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
        if duplicate and not (duplicate.parse_metadata or {}).get("source_invalidated"):
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
    if initial_metadata.get("knowledge_kind") == "faq":
        previous_reviews = [
            (v.parse_metadata or {}).get("source_invalidated", {}).get("at", "")
            for v in session.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
            )
        ]
        marker = max(previous_reviews, default="")
        if marker:
            # Explicit re-upload after invalidation is a fresh reviewed FAQ version,
            # even when the source was rolled back and file bytes are unchanged.
            config["faq_review_after"] = marker
            initial_metadata["faq_review_after"] = marker
    content_hash = hashlib.sha256(content).hexdigest()
    config_hash = fingerprint(config)
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
        parse_metadata=initial_metadata,
        **options.model_dump(exclude={"audience", "knowledge_kind", "faq_sources"}),
    )
    session.add(version)
    try:
        session.flush()
        session.add(IngestionJob(organization_id=actor.organization_id, version_id=version.id))
        audit(session, actor, "document.uploaded", version.id, document_id=document.id)
        if commit:
            session.commit()
        else:
            session.flush()
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)
        raise
    return document, version, True


@traced("business", "publish")
def publish(
    session,
    actor,
    version_id,
    acknowledge_warnings=False,
    *,
    candidate_id=None,
    candidate_revision=None,
    publication_note="",
    settings=None,
):
    from workbench.knowledge_revisions import invalidate_faqs, publication_lock

    if actor.role != "admin":
        raise HTTPException(403, "Administrator required")
    publication_lock(session, actor)
    version = scoped_version(session, actor, version_id)
    document = scoped_document(session, actor, version.document_id, lock=True)
    if version.status != "ready" or version.chunk_count < 1:
        raise HTTPException(409, "Version is not indexed and ready")
    if version.parse_metadata.get("warnings") and not acknowledge_warnings:
        raise HTTPException(409, "Review parser warnings before publishing")
    from workbench.faq_sources import check_faq

    check_faq(session, actor.organization_id, version.parse_metadata or {})
    from workbench.knowledge_updates import faq_publication_ready, finish_published, record_update

    faq_publication_ready(session, actor, version)
    previous_version_id = document.active_version_id
    previous_version = (
        session.get(DocumentVersion, previous_version_id) if previous_version_id else None
    )
    previous_annotation = (
        (previous_version.parse_metadata or {}).get("policy_extraction_id")
        if previous_version
        else None
    )
    revision = (version.parse_metadata or {}).get("revision")
    if revision and (
        revision.get("review_status") != "approved"
        or document.active_version_id not in {revision["base_version_id"], version.id}
    ):
        raise HTTPException(409, "Revision requires independent review against the current source")
    from workbench.policy_extraction import publication_candidate

    candidate = publication_candidate(session, actor, version, document, settings)
    if candidate_id is not None and (
        candidate is None
        or candidate.id != candidate_id
        or candidate.revision != candidate_revision
    ):
        raise HTTPException(409, "Candidate or revision changed before publication")
    if candidate and candidate.status != "published":
        candidate.status = "published"
        candidate.revision += 1
        candidate.updated_at = now()
        version.parse_metadata = {**version.parse_metadata, "policy_extraction_id": candidate.id}
        audit(
            session,
            actor,
            "policy.published",
            candidate.id,
            version_id=version.id,
            reviewer=candidate.reviewed_by,
            reason=publication_note,
        )
    invalidate_faqs(session, actor, document.id, document.active_version_id, version.id)
    document.active_version_id = version.id
    version.published_at = now()
    finish_published(session, actor, version, candidate)
    record_update(
        session,
        actor,
        document,
        previous_version_id,
        version.id,
        "annotation_update" if previous_version_id == version.id else "publish",
        old_annotation=previous_annotation,
        new_annotation=(version.parse_metadata or {}).get("policy_extraction_id"),
    )
    audit(session, actor, "document.published", version.id, document_id=document.id)
    session.commit()
    return version_view(session, document, version)


@traced("business", "withdraw")
def withdraw(session, actor, document_id):
    from workbench.knowledge_revisions import invalidate_faqs, publication_lock

    if actor.role != "admin":
        raise HTTPException(403, "Administrator required")
    publication_lock(session, actor)
    document = scoped_document(session, actor, document_id, lock=True)
    previous = document.active_version_id
    invalidate_faqs(session, actor, document.id, previous, None)
    document.active_version_id = None
    from workbench.knowledge_updates import record_update

    record_update(session, actor, document, previous, None, "withdraw")
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
        "text_origin": (version.parse_metadata or {}).get("text_origin", "parsed_original"),
        "currently_published": document.active_version_id == version.id,
    }


def validated_evidence(session, actor, matches, *, audience=None):
    """Validate citations again at publication, rather than trusting transport fields."""
    from datetime import UTC, datetime

    from sqlalchemy import or_

    if not isinstance(matches, list) or len(matches) > 20:
        raise HTTPException(409, "Invalid evidence collection")
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in matches):
        raise HTTPException(409, "Invalid evidence identity")
    # Read windows are reassembled from current original text, never trusted transport.
    readings = [row for row in matches if row.get("source_kind") == "source_read"]
    chunks = [row for row in matches if row.get("source_kind") != "source_read"]
    if len({row["id"] for row in matches}) != len(matches):
        raise HTTPException(409, "Duplicate evidence identity")
    checked_readings = []
    for row in readings:
        from workbench.knowledge_schemas import ReadInput
        from workbench.source_reading import reading_evidence

        body = ReadInput.model_validate(row.get("read_request"))
        if audience is not None:
            body = ReadInput.model_validate({**body.model_dump(), "audience": audience})
        current = reading_evidence(read_parent(session, actor, body), body)
        if row["id"] != current["id"] or row.get("content") != current["content"]:
            raise HTTPException(409, "Read evidence no longer matches original")
        checked_readings.append(current)
    matches = chunks
    identifiers = {row["id"] for row in matches}
    if len(identifiers) != len(matches):
        raise HTTPException(409, "Duplicate evidence identity")
    if not identifiers:
        return checked_readings
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
    if audience:
        from workbench.policy_audience import PolicyAudience, audience_filter

        audience = PolicyAudience.model_validate(audience)
        candidates = [(document, version) for _, version, document in allowed.values()]
        current, _ = audience_filter(candidates, audience, explicit_version=True)
        if {v.id for _, v in current} != {v.id for _, v, _ in allowed.values()}:
            raise HTTPException(409, "Evidence audience conflicts with current conditions")
    from workbench.parent_context import related_sections

    parent_info = {}
    for version in {v.id: v for _, v, _ in allowed.values()}.values():
        parents = (version.parse_metadata or {}).get("parent_child", {}).get("parents", [])
        if not parents:
            continue
        by_position = {
            c.position: c.id
            for c in session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)
            )
        }
        parent_info[version.id] = {
            parent["id"]: {
                "text": parent.get("text", ""),
                "complete": bool(parent["positions"])
                and all(by_position.get(p) in identifiers for p in parent["positions"]),
                "related_sections": related_sections(parents, parent["id"]),
                "unresolved_articles": parent.get("unresolved_articles", []),
            }
            for parent in parents
        }
    result = []
    for item in matches:
        chunk, version, document = allowed[item["id"]]
        from workbench.faq_sources import check_faq

        check_faq(session, actor.organization_id, version.parse_metadata or {})
        if item.get("content") != chunk.content or item.get("version_id") != version.id:
            raise HTTPException(409, "Evidence content does not match its source")
        result.append(
            {
                **item,
                "text_origin": (version.parse_metadata or {}).get("text_origin", "parsed_original"),
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
                "audience": (version.parse_metadata or {}).get("audience", {}),
                "unresolved_articles": parent_info.get(version.id, {})
                .get(
                    (version.parse_metadata or {})
                    .get("parent_child", {})
                    .get("children", {})
                    .get(str(chunk.position)),
                    {},
                )
                .get("unresolved_articles", []),
                "parent_match_offset": parent_info.get(version.id, {})
                .get(
                    (version.parse_metadata or {})
                    .get("parent_child", {})
                    .get("children", {})
                    .get(str(chunk.position)),
                    {},
                )
                .get("text", "")
                .find(chunk.content),
                "parent_complete": parent_info.get(version.id, {})
                .get(
                    (version.parse_metadata or {})
                    .get("parent_child", {})
                    .get("children", {})
                    .get(str(chunk.position)),
                    {},
                )
                .get("complete", False),
                "related_sections": parent_info.get(version.id, {})
                .get(
                    (version.parse_metadata or {})
                    .get("parent_child", {})
                    .get("children", {})
                    .get(str(chunk.position)),
                    {},
                )
                .get("related_sections", []),
                "parent_id": (version.parse_metadata or {})
                .get("parent_child", {})
                .get("children", {})
                .get(str(chunk.position)),
            }
        )
    return result + checked_readings


@traced("business", "read_parent")
def read_parent(session, actor, body):
    from datetime import UTC, datetime

    from workbench.parent_context import related_sections
    from workbench.policy_audience import audience_filter

    version = scoped_version(session, actor, body.version_id)
    document = scoped_document(session, actor, version.document_id)
    today = datetime.now(UTC).date()
    if (
        document.active_version_id != version.id
        or version.status != "ready"
        or (version.effective_from and version.effective_from > today)
        or (version.effective_until and version.effective_until < today)
    ):
        raise HTTPException(409, "Source is not currently available")
    allowed, _ = audience_filter([(document, version)], body.audience, explicit_version=True)
    if not allowed:
        raise HTTPException(409, "Source audience does not match current conditions")
    metadata = version.parse_metadata or {}
    from workbench.faq_sources import check_faq

    check_faq(session, actor.organization_id, metadata)
    if metadata.get("source_sha256") != version.content_hash:
        raise HTTPException(409, "Source provenance is unavailable; re-ingest version")
    parents = metadata.get("parent_child", {}).get("parents", [])
    parent = next((p for p in parents if p["id"] == body.parent_id), None)
    if parent is None:
        raise HTTPException(404, "Parent section not found")
    text = parent["text"]
    if body.offset >= len(text):
        raise HTTPException(422, "Reading offset is outside source")
    end = min(len(text), body.offset + body.max_chars)
    return {
        "status": "found",
        "version_id": version.id,
        "version": version.number,
        "filename": version.filename,
        "document_id": document.id,
        "policy_scope": version.policy_scope,
        "parent_id": parent["id"],
        "title": document.title,
        "section": parent["section"],
        "related_sections": related_sections(parents, parent["id"]),
        "unresolved_articles": parent.get("unresolved_articles", []),
        "source_kind": (version.parse_metadata or {}).get("text_origin", "parsed_original"),
        "source_sha256": version.content_hash,
        "audience": metadata.get("audience", {}),
        "offset": body.offset,
        "next_offset": end if end < len(text) else None,
        "total_chars": len(text),
        "complete": body.offset == 0 and end == len(text),
        "content": text[body.offset : end],
        "covered_chunk_ids": [
            chunk.id
            for chunk in session.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.version_id == version.id,
                    KnowledgeChunk.position.in_(parent["positions"]),
                )
            )
            if chunk.content in text[body.offset : end]
        ],
        "block_start": parent["block_start"],
        "block_end": parent["block_end"],
        "policy_sufficiency": "not_established",
    }
