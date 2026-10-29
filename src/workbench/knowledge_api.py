from datetime import date
from typing import Annotated

from fastapi import Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from workbench.knowledge_models import Document, DocumentVersion, IngestionJob
from workbench.knowledge_schemas import PublishInput, SearchInput, VersionOptions
from workbench.knowledge_service import (
    citation,
    create_version,
    publish,
    retry,
    scoped_document,
    scoped_version,
    stored_file,
    version_view,
    withdraw,
)
from workbench.mcp_gateway import ToolFailure
from workbench.tool_contracts import mint_grant


def attach_knowledge_routes(
    app, database, settings, session_dependency, actor_dependency, require, bearer, gateway
):
    @app.post("/api/v1/mcp/grants")
    def delegation(
        response: Response,
        user=Depends(actor_dependency),
        credentials=Depends(bearer),
        session=Depends(session_dependency),
    ):
        token, grant = mint_grant(session, credentials.credentials, settings)
        response.headers["Cache-Control"] = "no-store"
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": 300,
            "resource_url": settings.mcp_url,
            "scopes": grant.scopes,
        }

    @app.post("/api/v1/knowledge/documents", status_code=201)
    async def upload(
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form(min_length=1, max_length=200)] = "新的政策文档",
        policy_scope: Annotated[str, Form()] = "unknown",
        chunk_strategy: Annotated[str, Form()] = "structural",
        chunk_chars: Annotated[int, Form(ge=160, le=1000)] = 600,
        chunk_overlap: Annotated[int, Form(ge=0, le=200)] = 80,
        effective_from: Annotated[date | None, Form()] = None,
        effective_until: Annotated[date | None, Form()] = None,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        try:
            options = VersionOptions(
                policy_scope=policy_scope,
                chunk_strategy=chunk_strategy,
                chunk_chars=chunk_chars,
                chunk_overlap=chunk_overlap,
                effective_from=effective_from,
                effective_until=effective_until,
            )
        except ValueError:
            raise HTTPException(422, "Invalid version options") from None
        content = await file.read(settings.upload_limit_bytes + 1)
        document, version, created = create_version(
            session, user, settings, file.filename, content, options, title=title
        )
        return {
            "document_id": document.id,
            "created": created,
            "version": version_view(session, document, version),
        }

    @app.post("/api/v1/knowledge/documents/{document_id}/versions", status_code=201)
    async def upload_version(
        document_id: str,
        file: Annotated[UploadFile, File()],
        policy_scope: Annotated[str | None, Form()] = None,
        chunk_strategy: Annotated[str, Form()] = "structural",
        chunk_chars: Annotated[int, Form(ge=160, le=1000)] = 600,
        chunk_overlap: Annotated[int, Form(ge=0, le=200)] = 80,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        document = scoped_document(session, user, document_id)
        latest = session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.number.desc())
        )
        try:
            options = VersionOptions(
                policy_scope=policy_scope or latest.policy_scope,
                chunk_strategy=chunk_strategy,
                chunk_chars=chunk_chars,
                chunk_overlap=chunk_overlap,
                effective_from=latest.effective_from,
                effective_until=latest.effective_until,
            )
        except ValueError:
            raise HTTPException(422, "Invalid version options") from None
        content = await file.read(settings.upload_limit_bytes + 1)
        document, version, created = create_version(
            session, user, settings, file.filename, content, options, document_id=document_id
        )
        return {
            "document_id": document.id,
            "created": created,
            "version": version_view(session, document, version),
        }

    @app.get("/api/v1/knowledge/documents")
    def documents(
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        rows = session.scalars(
            select(Document)
            .where(Document.organization_id == user.organization_id)
            .order_by(Document.created_at.desc(), Document.id)
            .offset(offset)
            .limit(limit)
        ).all()
        if not rows:
            return {"items": []}
        documents_by_id = {row.id: row for row in rows}
        active_ids = [row.active_version_id for row in rows if row.active_version_id]
        ranked = (
            select(
                DocumentVersion.id,
                func.row_number()
                .over(
                    partition_by=DocumentVersion.document_id, order_by=DocumentVersion.number.desc()
                )
                .label("position"),
            )
            .where(
                DocumentVersion.organization_id == user.organization_id,
                DocumentVersion.document_id.in_(documents_by_id),
            )
            .subquery()
        )
        versions = session.scalars(
            select(DocumentVersion)
            .join(ranked, ranked.c.id == DocumentVersion.id)
            .where(or_(ranked.c.position <= 30, DocumentVersion.id.in_(active_ids)))
            .order_by(DocumentVersion.number.desc())
        ).all()
        jobs = {
            job.version_id: job
            for job in session.scalars(
                select(IngestionJob).where(
                    IngestionJob.organization_id == user.organization_id,
                    IngestionJob.version_id.in_([version.id for version in versions]),
                )
            )
        }
        groups = {row.id: [] for row in rows}
        for version in versions:
            document = documents_by_id[version.document_id]
            groups[document.id].append(
                version_view(session, document, version, jobs.get(version.id))
            )
        return {
            "items": [
                {
                    "id": row.id,
                    "title": row.title,
                    "active_version_id": row.active_version_id,
                    "versions": groups[row.id],
                }
                for row in rows
            ]
        }

    @app.get("/api/v1/knowledge/versions/{version_id}")
    def version_details(
        version_id: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        version = scoped_version(session, user, version_id)
        document = scoped_document(session, user, version.document_id)
        return version_view(session, document, version)

    @app.post("/api/v1/knowledge/versions/{version_id}/publish")
    def publish_version(
        version_id: str,
        body: PublishInput,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return publish(session, user, version_id, body.acknowledge_warnings)

    @app.post("/api/v1/knowledge/documents/{document_id}/withdraw")
    def withdraw_document(
        document_id: str, user=Depends(require("admin")), session=Depends(session_dependency)
    ):
        return withdraw(session, user, document_id)

    @app.post("/api/v1/knowledge/versions/{version_id}/retry")
    def retry_version(
        version_id: str, user=Depends(require("admin")), session=Depends(session_dependency)
    ):
        return retry(session, user, version_id)

    @app.get("/api/v1/knowledge/versions/{version_id}/file")
    def original(
        version_id: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        version = scoped_version(session, user, version_id)
        return FileResponse(
            stored_file(settings, version.storage_key),
            filename=version.filename,
            media_type="application/octet-stream",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
                "Cache-Control": "private, no-store",
            },
        )

    @app.get("/api/v1/knowledge/citations/{chunk_id}")
    def source(chunk_id: str, user=Depends(actor_dependency), session=Depends(session_dependency)):
        return citation(session, user, chunk_id)

    @app.post("/api/v1/knowledge/search")
    async def search(
        body: SearchInput,
        user=Depends(actor_dependency),
        credentials=Depends(bearer),
        session=Depends(session_dependency),
    ):
        token, _ = mint_grant(session, credentials.credentials, settings)
        try:
            result = await gateway.call(
                "search_knowledge_base", body.model_dump(mode="json"), token
            )
            from workbench.knowledge_service import validated_evidence

            result["matches"] = validated_evidence(session, user, result["matches"])
            from workbench.return_context import select_policy_context

            context = select_policy_context(result["matches"], [], purpose="policy_question")
            result["context_purpose"] = context["purpose"]
            result["context_selection"] = context["selection"]
            return result
        except ToolFailure as exc:
            raise HTTPException(
                {"forbidden": 403, "unauthorized": 401, "timeout": 504}.get(exc.code, 503),
                "Knowledge tool unavailable: " + exc.code,
            ) from None
