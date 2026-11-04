"""Authenticated after-sales evidence upload, extraction, review and download.

Reuses the existing on-disk file store (storage_root / stored_file) and the
organization-scoped session dependency. Extraction is rule-based candidate
extraction only (no OCR/vision in this version); extracted values never become
business facts until a human reviews them. No policy work happens here.
"""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field, StrictStr
from sqlalchemy import select

from workbench.after_sales_models import AfterSalesAttachment
from workbench.domain import audit
from workbench.evidence_extraction import ExtractionError, extract
from workbench.knowledge_service import stored_file
from workbench.models import Order, identifier, now
from workbench.return_models import ReturnRequest
from workbench.schemas import Input

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}
KINDS = {"product_photo", "express_waybill", "invoice", "quality_photo", "other"}
REVIEWABLE = {"PENDING_REVIEW", "EXTRACTION_FAILED"}
DECISIONS = {
    "verified": "VERIFIED",
    "rejected": "REJECTED",
    "needs_supplement": "NEEDS_SUPPLEMENT",
}


class ReviewDecision(Input):
    decision: Literal["verified", "rejected", "needs_supplement"]
    note: StrictStr = Field(min_length=1, max_length=500)


def _view(row):
    return {
        "id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "kind": row.kind,
        "return_request_id": row.return_request_id,
        "order_id": row.order_id,
        "note": row.note,
        "uploaded_by": row.uploaded_by,
        "status": row.status,
        "extracted_data": row.extracted_data,
        "extraction_method": row.extraction_method,
        "extraction_error": row.extraction_error,
        "reviewed_by": row.reviewed_by,
        "review_note": row.review_note,
        "reviewed_at": row.reviewed_at,
        "created_at": row.created_at,
    }


def evidence_for_request(session, actor, request_id):
    request = session.scalar(
        select(ReturnRequest).where(
            ReturnRequest.id == request_id,
            ReturnRequest.organization_id == actor.organization_id,
        )
    )
    if request is None:
        raise HTTPException(404, "Return request not found")
    rows = session.scalars(
        select(AfterSalesAttachment)
        .where(
            AfterSalesAttachment.organization_id == actor.organization_id,
            AfterSalesAttachment.return_request_id == request_id,
        )
        .order_by(AfterSalesAttachment.created_at.desc(), AfterSalesAttachment.id.desc())
    ).all()
    return [_view(row) for row in rows]


def _scoped_attachment(session, actor, attachment_id):
    row = session.scalar(
        select(AfterSalesAttachment).where(
            AfterSalesAttachment.id == attachment_id,
            AfterSalesAttachment.organization_id == actor.organization_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Attachment not found")
    return row


def _scoped_request(session, actor, request_id):
    if not request_id:
        return
    row = session.scalar(
        select(ReturnRequest).where(
            ReturnRequest.id == request_id,
            ReturnRequest.organization_id == actor.organization_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Return request not found")


def _scoped_order(session, actor, order_id):
    if not order_id:
        return
    row = session.scalar(
        select(Order).where(Order.id == order_id, Order.organization_id == actor.organization_id)
    )
    if row is None:
        raise HTTPException(404, "Order not found")


def attach_after_sales_routes(app, settings, session_dependency, actor_dependency, require):
    @app.post("/api/v1/after-sales/attachments", status_code=201)
    async def upload(
        file: Annotated[UploadFile, File()],
        kind: Annotated[str, Form()] = "other",
        return_request_id: Annotated[str | None, Form()] = None,
        order_id: Annotated[str | None, Form()] = None,
        note: Annotated[str | None, Form()] = None,
        user=Depends(require("support", "admin")),
        session=Depends(session_dependency),
    ):
        content = await file.read(settings.upload_limit_bytes + 1)
        if not content or len(content) > settings.upload_limit_bytes:
            raise HTTPException(413, "File is empty or exceeds upload limit")
        filename = re.split(r"[/\\]", file.filename or "")[-1]
        if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
            raise HTTPException(422, "Invalid filename")
        extension = Path(filename).suffix.lower()
        if extension not in EXTENSIONS:
            raise HTTPException(422, "Supported files: PNG, JPG, JPEG, WEBP, GIF, PDF")
        validated_kind = kind if kind in KINDS else "other"
        request_id = (return_request_id or "").strip() or None
        order_id_value = (order_id or "").strip() or None
        note_value = (note or "").strip()[:500] or None
        _scoped_request(session, user, request_id)
        _scoped_order(session, user, order_id_value)

        attachment_id = identifier()
        key = f"{user.organization_id}/{attachment_id}/original{extension}"
        path = stored_file(settings, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        row = AfterSalesAttachment(
            id=attachment_id,
            organization_id=user.organization_id,
            filename=filename,
            storage_key=key,
            content_type=MEDIA_TYPES[extension],
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            kind=validated_kind,
            return_request_id=request_id,
            order_id=order_id_value,
            note=note_value,
            uploaded_by=user.id,
        )
        session.add(row)
        try:
            session.flush()
            audit(session, user, "attachment.uploaded", row.id, kind=validated_kind, filename=filename)
            session.commit()
        except Exception:
            session.rollback()
            path.unlink(missing_ok=True)
            raise
        # Extraction runs only after the upload is durable; failure keeps the
        # original file and record, marking the evidence EXTRACTION_FAILED.
        row.status = "PROCESSING"
        session.commit()
        try:
            method, data = extract(validated_kind, path, extension)
            row.extraction_method = method
            row.extracted_data = data
            row.extraction_error = None
            row.status = "PENDING_REVIEW"
        except ExtractionError as exc:
            row.extraction_method = None
            row.extraction_error = str(exc)[:300]
            row.status = "EXTRACTION_FAILED"
        session.commit()
        return _view(row)

    @app.get("/api/v1/after-sales/attachments")
    def attachments(
        return_request_id: str | None = None,
        order_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        statement = select(AfterSalesAttachment).where(
            AfterSalesAttachment.organization_id == user.organization_id
        )
        if return_request_id:
            statement = statement.where(AfterSalesAttachment.return_request_id == return_request_id)
        if order_id:
            statement = statement.where(AfterSalesAttachment.order_id == order_id)
        rows = session.scalars(
            statement.order_by(
                AfterSalesAttachment.created_at.desc(), AfterSalesAttachment.id.desc()
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return {"items": [_view(row) for row in rows], "limit": limit, "offset": offset}

    @app.get("/api/v1/after-sales/attachments/{attachment_id}")
    def detail(
        attachment_id: str,
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        return _view(_scoped_attachment(session, user, attachment_id))

    @app.post("/api/v1/after-sales/attachments/{attachment_id}/review")
    def review(
        attachment_id: str,
        body: ReviewDecision,
        user=Depends(require("support", "admin")),
        session=Depends(session_dependency),
    ):
        row = _scoped_attachment(session, user, attachment_id)
        if row.status not in REVIEWABLE:
            raise HTTPException(409, "Evidence is not awaiting review")
        row.status = DECISIONS[body.decision]
        row.reviewed_by = user.id
        row.review_note = body.note.strip()
        row.reviewed_at = now()
        audit(
            session,
            user,
            "evidence.reviewed",
            row.id,
            decision=body.decision,
            status=row.status,
        )
        session.commit()
        return _view(row)

    @app.get("/api/v1/after-sales/attachments/{attachment_id}/file")
    def original(
        attachment_id: str,
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        row = _scoped_attachment(session, user, attachment_id)
        return FileResponse(
            stored_file(settings, row.storage_key),
            filename=row.filename,
            media_type=row.content_type,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
                "Cache-Control": "private, no-store",
            },
        )