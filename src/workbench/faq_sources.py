"""FAQ answers depend on current, scoped, reviewed source versions."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from workbench.knowledge_models import Document, DocumentVersion


def dependencies_current(session, organization_id, metadata):
    if metadata.get("knowledge_kind") != "faq":
        return True
    if metadata.get("source_invalidated"):
        return False
    references = metadata.get("faq_sources") or []
    if not references:
        return False
    rows = session.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
        .where(
            Document.organization_id == organization_id,
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.id.in_(references),
            DocumentVersion.status == "ready",
        )
    ).all()
    today = datetime.now(UTC).date()
    allowed = {
        v.id
        for _, v in rows
        if (not v.effective_from or v.effective_from <= today)
        and (not v.effective_until or v.effective_until >= today)
        and (v.parse_metadata or {}).get("knowledge_kind") != "faq"
    }
    if allowed != set(references):
        return False
    review = metadata.get("faq_update_review") or {}
    bindings = review.get("source_bindings")
    if bindings is not None:
        expected = {(b["document_id"], b["version_id"], b.get("annotation_id")) for b in bindings}
        actual = {
            (d.id, v.id, (v.parse_metadata or {}).get("policy_extraction_id")) for d, v in rows
        }
        if expected != actual:
            return False
    return True


def check_faq(session, organization_id, metadata):
    if not dependencies_current(session, organization_id, metadata):
        raise HTTPException(409, "FAQ sources changed; review and publish a new FAQ version")
    if metadata.get("knowledge_kind") == "faq":
        audience = metadata.get("audience", {})
        for version in session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.id.in_(metadata["faq_sources"]),
                DocumentVersion.organization_id == organization_id,
            )
        ):
            source = (version.parse_metadata or {}).get("audience", {})
            if any(audience.get(k) != value for k, value in source.items()):
                raise HTTPException(409, "FAQ must preserve source audience conditions")
