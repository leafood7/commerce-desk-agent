"""Immutable human parse revisions, independent review, and explicit rollback."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from difflib import unified_diff
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select

from workbench.document_parsing import MAX_TEXT, Block, IngestionError, parse_document
from workbench.domain import audit, fingerprint
from workbench.knowledge_models import DocumentVersion, IngestionJob
from workbench.models import Organization, User, identifier, now


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def publication_lock(session, actor):
    # Serialize publication, FAQ dependency decisions and review within an org.
    # Drop an authentication read snapshot before acquiring the publication mutex.
    if session.new or session.dirty or session.deleted:
        raise RuntimeError("Knowledge mutation requires a clean transaction")
    session.commit()
    session.scalar(
        select(Organization).where(Organization.id == actor.organization_id).with_for_update()
    )


def effective_blocks(session, version, settings, depth=0):
    from workbench.knowledge_service import stored_file

    if depth > 8:
        raise IngestionError("revision_depth_limit")
    original = stored_file(settings, version.storage_key)
    if hashlib.sha256(original.read_bytes()).hexdigest() != version.content_hash:
        raise IngestionError("source_hash_mismatch")
    snapshot = (version.parse_metadata or {}).get("parse_snapshot")
    if snapshot:
        payload = stored_file(settings, snapshot["key"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != snapshot["sha256"]:
            raise IngestionError("parse_snapshot_hash_mismatch")
        return [Block(**b) for b in json.loads(payload)], {
            k: v
            for k, v in (version.parse_metadata or {}).items()
            if k not in {"parent_child", "parse_snapshot"}
        }
    revision = (version.parse_metadata or {}).get("revision")
    if not revision:
        path = stored_file(settings, version.storage_key)
        if hashlib.sha256(path.read_bytes()).hexdigest() != version.content_hash:
            raise IngestionError("source_hash_mismatch")
        return parse_document(path, Path(version.filename).suffix.lower())
    base = session.get(DocumentVersion, revision["base_version_id"])
    if not base or (base.organization_id, base.document_id, base.content_hash) != (
        version.organization_id,
        version.document_id,
        version.content_hash,
    ):
        raise IngestionError("revision_base_mismatch")
    blocks, metadata = effective_blocks(session, base, settings, depth + 1)
    if fingerprint([b.__dict__ for b in blocks]) != revision["base_blocks_sha256"]:
        raise IngestionError("revision_base_parse_changed")
    blocks = list(blocks)
    for patch in revision["patches"]:
        index = patch["block_index"]
        if index >= len(blocks) or text_hash(blocks[index].text) != patch["before_sha256"]:
            raise IngestionError("revision_patch_mismatch")
        blocks[index] = replace(blocks[index], text=patch["text"])
    if sum(len(b.text) for b in blocks) > MAX_TEXT:
        raise IngestionError("revision_text_limit")
    # An annotation belongs to one exact version, not to a derived parse revision.
    metadata = {
        k: v for k, v in metadata.items() if k not in {"policy_extraction_id", "faq_update_review"}
    }
    return blocks, {**metadata, "text_origin": "human_corrected_parse"}


def reviewable_blocks(session, version, settings):
    try:
        return effective_blocks(session, version, settings)
    except (IngestionError, OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            409, "Source or parse snapshot is unavailable; restore before revising"
        ) from exc


def inspect_parse(session, actor, version_id, settings, offset=0, limit=10):
    from workbench.knowledge_service import scoped_version

    version = scoped_version(session, actor, version_id)
    blocks, _ = reviewable_blocks(session, version, settings)
    revision = dict((version.parse_metadata or {}).get("revision") or {})
    for field in ("proposed_by", "reviewed_by") if revision else ():
        person = session.get(User, revision.get(field)) if revision.get(field) else None
        revision[field + "_name"] = (
            person.email if person and person.organization_id == actor.organization_id else ""
        )
    return {
        "version_id": version.id,
        "text_origin": "human_corrected_parse"
        if (version.parse_metadata or {}).get("revision")
        else "parsed_original",
        "original_sha256": version.content_hash,
        "revision": revision or None,
        "offset": offset,
        "total": len(blocks),
        "next_offset": offset + limit if offset + limit < len(blocks) else None,
        "blocks": [
            {
                "index": i,
                "section": b.section,
                "page": b.page,
                "text": b.text[:20000],
                "sha256": text_hash(b.text),
                "editable": len(b.text) <= 20000,
                "truncated": len(b.text) > 20000,
            }
            for i, b in enumerate(blocks[offset : offset + limit], offset)
        ],
    }


def create_revision(session, actor, base_id, body, settings):
    from workbench.knowledge_service import scoped_document, scoped_version, version_view

    if actor.role != "admin":
        raise HTTPException(403, "Only administrators may propose parse revisions")
    publication_lock(session, actor)
    base = scoped_version(session, actor, base_id)
    document = scoped_document(session, actor, base.document_id, lock=True)
    if document.active_version_id != base.id or body.expected_active_version_id != base.id:
        raise HTTPException(409, "Published source changed; inspect the current version")
    if (base.parse_metadata or {}).get("revision", {}).get("depth", 0) >= 8:
        raise HTTPException(409, "Revision chain is full; upload a reviewed new source")
    blocks, _ = reviewable_blocks(session, base, settings)
    patches = [p.model_dump() for p in body.patches]
    if len({p["block_index"] for p in patches}) != len(patches):
        raise HTTPException(422, "Each block may be revised once per proposal")
    diffs = []
    for patch in patches:
        i = patch["block_index"]
        if i >= len(blocks) or text_hash(blocks[i].text) != patch["before_sha256"]:
            raise HTTPException(409, "Parsed block changed; reload before editing")
        if (
            not patch["text"].strip()
            or patch["text"] == blocks[i].text
            or len(blocks[i].text) > 20000
        ):
            raise HTTPException(422, "Revision must change a bounded nonblank block")
        diffs.append(
            {
                "block_index": i,
                "section": blocks[i].section,
                "page": blocks[i].page,
                "before": blocks[i].text,
                "after": patch["text"],
                "diff": "\n".join(
                    unified_diff(
                        blocks[i].text.splitlines(),
                        patch["text"].splitlines(),
                        fromfile="before",
                        tofile="after",
                        lineterm="",
                    )
                ),
            }
        )
    revision = {
        "base_version_id": base.id,
        "base_blocks_sha256": fingerprint([b.__dict__ for b in blocks]),
        "depth": (base.parse_metadata or {}).get("revision", {}).get("depth", 0) + 1,
        "patches": patches,
        "changes": diffs,
        "reason": body.reason,
        "proposed_by": actor.id,
        "proposed_at": now().isoformat(),
        "review_status": "pending",
    }
    config_hash = fingerprint({"base": base.id, "patches": patches, "reason": body.reason})
    duplicate = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.content_hash == base.content_hash,
            DocumentVersion.config_hash == config_hash,
        )
    )
    if duplicate:
        return version_view(session, document, duplicate)
    metadata = {
        k: v
        for k, v in (base.parse_metadata or {}).items()
        if k in {"audience", "knowledge_kind", "faq_sources"}
    }
    metadata.update(revision=revision, text_origin="human_corrected_parse")
    version = DocumentVersion(
        id=identifier(),
        organization_id=actor.organization_id,
        document_id=document.id,
        number=(
            session.scalar(
                select(func.max(DocumentVersion.number)).where(
                    DocumentVersion.document_id == document.id
                )
            )
            or 0
        )
        + 1,
        filename=base.filename,
        storage_key=base.storage_key,
        content_hash=base.content_hash,
        config_hash=config_hash,
        policy_scope=base.policy_scope,
        effective_from=base.effective_from,
        effective_until=base.effective_until,
        chunk_strategy="parent_child",
        chunk_chars=base.chunk_chars,
        chunk_overlap=base.chunk_overlap,
        parse_metadata=metadata,
    )
    session.add(version)
    session.flush()
    session.add(IngestionJob(organization_id=actor.organization_id, version_id=version.id))
    audit(
        session,
        actor,
        "document.parse_revision_proposed",
        version.id,
        base_version_id=base.id,
        reason=body.reason,
    )
    session.commit()
    return version_view(session, document, version)


def review_revision(session, actor, version_id, body):
    from workbench.knowledge_service import scoped_document, scoped_version, version_view

    if actor.role != "approver":
        raise HTTPException(403, "Independent approver required")
    publication_lock(session, actor)
    version = scoped_version(session, actor, version_id)
    document = scoped_document(session, actor, version.document_id, lock=True)
    metadata = dict(version.parse_metadata or {})
    revision = dict(metadata.get("revision") or {})
    if not revision or revision.get("proposed_by") == actor.id:
        raise HTTPException(403, "Revision author cannot review their own changes")
    if (
        version.status != "ready"
        or body.config_hash != version.config_hash
        or revision["review_status"] != "pending"
    ):
        raise HTTPException(409, "Review requires the current ready, unreviewed proposal")
    if document.active_version_id != revision["base_version_id"]:
        raise HTTPException(409, "Source publication changed; propose against the current version")
    revision.update(
        review_status="approved" if body.action == "approve" else "rejected",
        reviewed_by=actor.id,
        reviewed_at=now().isoformat(),
        review_reason=body.reason,
    )
    metadata["revision"] = revision
    version.parse_metadata = metadata
    audit(
        session,
        actor,
        "document.parse_revision_reviewed",
        version.id,
        decision=body.action,
        reason=body.reason,
    )
    session.commit()
    return version_view(session, document, version)


def invalidate_faqs(session, actor, document_id, previous, current):
    if previous == current or not previous:
        return
    rows = session.scalars(
        select(DocumentVersion)
        .where(
            DocumentVersion.organization_id == actor.organization_id,
            DocumentVersion.parse_metadata["knowledge_kind"].as_string() == "faq",
        )
        .order_by(DocumentVersion.id)
        .with_for_update()
    ).all()
    for version in rows:
        metadata = dict(version.parse_metadata or {})
        if (
            metadata.get("knowledge_kind") == "faq"
            and previous in metadata.get("faq_sources", [])
            and not metadata.get("source_invalidated")
        ):
            metadata["source_invalidated"] = {
                "document_id": document_id,
                "previous_version_id": previous,
                "current_version_id": current,
                "at": now().isoformat(),
            }
            version.parse_metadata = metadata
            audit(
                session,
                actor,
                "faq.source_invalidated",
                version.id,
                source_document_id=document_id,
                previous_version_id=previous,
            )


def rollback_version(session, actor, document_id, body):
    from workbench.faq_sources import check_faq
    from workbench.knowledge_service import scoped_document, scoped_version, version_view

    if actor.role != "admin":
        raise HTTPException(403, "Only administrators may roll back publication")
    publication_lock(session, actor)
    document = scoped_document(session, actor, document_id, lock=True)
    target = scoped_version(session, actor, body.version_id)
    if (
        not document.active_version_id
        or document.active_version_id != body.expected_active_version_id
    ):
        raise HTTPException(409, "Publication changed or was withdrawn")
    if (
        target.document_id != document.id
        or target.status != "ready"
        or not target.published_at
        or target.id == document.active_version_id
    ):
        raise HTTPException(
            409, "Rollback target must be a previously published version of this document"
        )
    if (target.parse_metadata or {}).get("revision", {}).get(
        "review_status", "approved"
    ) != "approved":
        raise HTTPException(409, "Target revision was not approved")
    today = datetime.now(UTC).date()
    if (target.effective_from and target.effective_from > today) or (
        target.effective_until and target.effective_until < today
    ):
        raise HTTPException(409, "Rollback target is not currently effective")
    check_faq(session, actor.organization_id, target.parse_metadata or {})
    previous = document.active_version_id
    invalidate_faqs(session, actor, document.id, previous, target.id)
    document.active_version_id = target.id
    from workbench.knowledge_updates import record_update

    old_version = session.get(DocumentVersion, previous)
    record_update(
        session,
        actor,
        document,
        previous,
        target.id,
        "rollback",
        old_annotation=(old_version.parse_metadata or {}).get("policy_extraction_id"),
        new_annotation=(target.parse_metadata or {}).get("policy_extraction_id"),
    )
    audit(
        session,
        actor,
        "document.rolled_back",
        target.id,
        previous_version_id=previous,
        reason=body.reason,
    )
    session.commit()
    return version_view(session, document, target)
