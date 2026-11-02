import json
from datetime import date
from typing import Annotated

from fastapi import Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from workbench.knowledge_models import Document, DocumentVersion, IngestionJob
from workbench.knowledge_schemas import PublishInput, ReadInput, SearchInput, VersionOptions
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
    @app.get("/api/v1/knowledge/vector-storage")
    def vector_storage(user=Depends(require("admin"))):
        from workbench.knowledge_index import EMBEDDING_MODEL, EMBEDDING_REVISION

        return {
            "backend": settings.vector_backend,
            "namespace": settings.vector_namespace,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_revision": EMBEDDING_REVISION,
            "switch_mode": "server_configuration_after_rebuild",
            "note": "配置不代表索引已重建；不进行自动回退。",
        }

    from workbench import knowledge_updates as updates
    from workbench.knowledge_schemas import ParseReviewInput

    @app.get("/api/v1/knowledge/versions/{version_id}/update-impact")
    def update_impact(
        version_id: str,
        candidate_id: str | None = None,
        user=Depends(require("admin", "approver")),
        session=Depends(session_dependency),
    ):
        return updates.preview(session, user, version_id, candidate_id)

    @app.get("/api/v1/knowledge/updates")
    def knowledge_updates(
        pending_only: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
        user=Depends(require("admin", "approver")),
        session=Depends(session_dependency),
    ):
        rows = session.scalars(
            select(updates.KnowledgeUpdate)
            .where(updates.KnowledgeUpdate.organization_id == user.organization_id)
            .where(
                updates.KnowledgeUpdate.id.in_(
                    select(updates.KnowledgeUpdateTask.update_id).where(
                        updates.KnowledgeUpdateTask.organization_id == user.organization_id,
                        updates.KnowledgeUpdateTask.status.in_(updates.OPEN),
                    )
                )
                if pending_only
                else True
            )
            .order_by(updates.KnowledgeUpdate.created_at.desc(), updates.KnowledgeUpdate.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        result = []
        for row in rows:
            document = session.get(Document, row.document_id)
            tasks = session.scalars(
                select(updates.KnowledgeUpdateTask)
                .where(
                    updates.KnowledgeUpdateTask.update_id == row.id,
                    updates.KnowledgeUpdateTask.organization_id == user.organization_id,
                )
                .order_by(updates.KnowledgeUpdateTask.created_at, updates.KnowledgeUpdateTask.id)
            ).all()
            result.append(
                {
                    "id": row.id,
                    "document_id": row.document_id,
                    "title": document.title,
                    "action": row.action,
                    "previous_version_id": row.previous_version_id,
                    "current_version_id": row.current_version_id,
                    "created_at": row.created_at,
                    "tasks": [updates.task_view(session, t) for t in tasks],
                }
            )
        return {"items": result, "offset": offset, "has_more": len(rows) == limit}

    @app.get("/api/v1/knowledge/update-tasks/{task_id}/faq-editor")
    def faq_update_editor(
        task_id: str,
        user=Depends(require("admin", "approver")),
        session=Depends(session_dependency),
    ):
        return updates.editor(session, user, task_id, settings)

    @app.post("/api/v1/knowledge/update-tasks/{task_id}/prepare-faq")
    def prepare_faq_update(
        task_id: str,
        body: updates.FaqPrepare,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return updates.prepare_faq(session, user, task_id, body, settings)

    @app.post("/api/v1/knowledge/update-tasks/{task_id}/prepare-extraction")
    def prepare_policy_update(
        task_id: str,
        body: updates.MaintenanceAction,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return updates.prepare_extraction(session, user, task_id, body, settings)

    @app.post("/api/v1/knowledge/update-tasks/{task_id}/resolve-rule")
    def resolve_rule_update(
        task_id: str,
        body: updates.MaintenanceAction,
        user=Depends(require("approver")),
        session=Depends(session_dependency),
    ):
        return updates.resolve_rule(session, user, task_id, body)

    @app.post("/api/v1/knowledge/update-tasks/{task_id}/dismiss")
    def dismiss_update(
        task_id: str,
        body: updates.MaintenanceAction,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return updates.dismiss(session, user, task_id, body)

    @app.post("/api/v1/knowledge/versions/{version_id}/faq-review")
    def review_faq_update(
        version_id: str,
        body: ParseReviewInput,
        user=Depends(require("approver")),
        session=Depends(session_dependency),
    ):
        return updates.review_faq(session, user, version_id, body)

    from workbench import policy_extraction as extraction

    @app.post("/api/v1/knowledge/versions/{version_id}/policy-extractions", status_code=201)
    def extract_policy(
        version_id: str, user=Depends(require("admin")), session=Depends(session_dependency)
    ):
        return extraction.create(session, user, version_id, settings)

    @app.get("/api/v1/knowledge/versions/{version_id}/policy-extractions")
    def list_extractions(
        version_id: str,
        user=Depends(require("admin", "approver")),
        session=Depends(session_dependency),
    ):
        version = scoped_version(session, user, version_id)
        document = scoped_document(session, user, version.document_id)
        rows = session.scalars(
            select(extraction.PolicyExtraction)
            .where(
                extraction.PolicyExtraction.organization_id == user.organization_id,
                extraction.PolicyExtraction.version_id == version_id,
            )
            .order_by(extraction.PolicyExtraction.number.desc())
            .limit(20)
        ).all()
        return {
            "items": [
                extraction.view(
                    row,
                    stale=document.active_version_id
                    != (version.id if row.status == "published" else row.base_active_id),
                )
                for row in rows
            ]
        }

    @app.get("/api/v1/knowledge/policy-extractions/{candidate_id}")
    def candidate_details(
        candidate_id: str,
        user=Depends(require("admin", "approver")),
        session=Depends(session_dependency),
    ):
        row = extraction.scoped(session, user, candidate_id)
        version, document, sources, digest = extraction.source(
            session, user, row.version_id, settings
        )
        active = version.id if row.status == "published" else row.base_active_id
        return extraction.view(
            row,
            stale=row.source_hash != digest or document.active_version_id != active,
            sources=sources,
        )

    @app.post("/api/v1/knowledge/policy-extractions/{candidate_id}/save")
    def save_candidate(
        candidate_id: str,
        body: extraction.CandidateEdit,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return extraction.update(session, user, candidate_id, body, settings)

    @app.post("/api/v1/knowledge/policy-extractions/{candidate_id}/submit")
    def submit_candidate(
        candidate_id: str,
        body: extraction.CandidateEdit,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return extraction.update(session, user, candidate_id, body, settings, submit=True)

    @app.post("/api/v1/knowledge/policy-extractions/{candidate_id}/review")
    def review_candidate(
        candidate_id: str,
        body: extraction.CandidateReview,
        user=Depends(require("approver")),
        session=Depends(session_dependency),
    ):
        return extraction.review(session, user, candidate_id, body, settings)

    @app.post("/api/v1/knowledge/policy-extractions/{candidate_id}/publish")
    def publish_candidate(
        candidate_id: str,
        body: extraction.CandidateAction,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        row = extraction.scoped(session, user, candidate_id)
        result = publish(
            session,
            user,
            row.version_id,
            body.acknowledge_warnings,
            candidate_id=candidate_id,
            candidate_revision=body.expected_revision,
            publication_note=body.note,
            settings=settings,
        )
        return {
            "version": result,
            "candidate": extraction.view(session.get(extraction.PolicyExtraction, candidate_id)),
        }

    @app.get("/api/v1/knowledge/versions/{version_id}/policy-summary")
    def published_policy_summary(
        version_id: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        version, document, rows, digest = extraction.source(session, user, version_id, settings)
        candidate_id = (version.parse_metadata or {}).get("policy_extraction_id")
        row = session.get(extraction.PolicyExtraction, candidate_id) if candidate_id else None
        if (
            document.active_version_id != version.id
            or row is None
            or row.status != "published"
            or row.organization_id != user.organization_id
            or row.source_hash != digest
        ):
            raise HTTPException(404, "No current reviewed policy summary")
        from workbench.knowledge_service import validated_evidence

        validated_evidence(
            session,
            user,
            [{"id": r.id, "version_id": version.id, "content": r.content} for r in rows],
        )
        return {
            "version_id": version.id,
            "cards": row.payload["cards"],
            "unknown_categories": row.payload["unknown_categories"],
            "text_origin": "human_reviewed_policy_annotations",
            "business_rule_activation": False,
        }

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

    from workbench.knowledge_revisions import (
        create_revision,
        inspect_parse,
        review_revision,
        rollback_version,
    )
    from workbench.knowledge_schemas import ParseReviewInput, ParseRevisionInput, RollbackInput

    @app.get("/api/v1/knowledge/versions/{version_id}/parse")
    def parsed_blocks(
        version_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(10, ge=1, le=20),
        user=Depends(require("admin", "approver")),
        session=Depends(session_dependency),
    ):
        return inspect_parse(session, user, version_id, settings, offset, limit)

    @app.post("/api/v1/knowledge/versions/{version_id}/revisions", status_code=201)
    def propose_revision(
        version_id: str,
        body: ParseRevisionInput,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return create_revision(session, user, version_id, body, settings)

    @app.post("/api/v1/knowledge/versions/{version_id}/review")
    def review_parse(
        version_id: str,
        body: ParseReviewInput,
        user=Depends(require("approver")),
        session=Depends(session_dependency),
    ):
        return review_revision(session, user, version_id, body)

    @app.post("/api/v1/knowledge/documents/{document_id}/rollback")
    def rollback(
        document_id: str,
        body: RollbackInput,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        return rollback_version(session, user, document_id, body)

    @app.post("/api/v1/knowledge/documents", status_code=201)
    async def upload(
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form(min_length=1, max_length=200)] = "新的政策文档",
        policy_scope: Annotated[str, Form()] = "unknown",
        chunk_strategy: Annotated[str, Form()] = "structural",
        audience: Annotated[str | None, Form()] = None,
        knowledge_kind: Annotated[str | None, Form()] = None,
        faq_sources: Annotated[str | None, Form()] = None,
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
                audience=json.loads(audience) if audience else None,
                knowledge_kind=knowledge_kind,
                faq_sources=json.loads(faq_sources) if faq_sources else None,
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
        audience: Annotated[str | None, Form()] = None,
        knowledge_kind: Annotated[str | None, Form()] = None,
        faq_sources: Annotated[str | None, Form()] = None,
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
                audience=json.loads(audience)
                if audience is not None
                else (latest.parse_metadata or {}).get("audience") or None,
                chunk_chars=chunk_chars,
                chunk_overlap=chunk_overlap,
                knowledge_kind=knowledge_kind
                if knowledge_kind is not None
                else (latest.parse_metadata or {}).get("knowledge_kind"),
                faq_sources=json.loads(faq_sources)
                if faq_sources is not None
                else (latest.parse_metadata or {}).get("faq_sources") or None,
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
        return publish(session, user, version_id, body.acknowledge_warnings, settings=settings)

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

    @app.post("/api/v1/knowledge/read")
    def read_source(
        body: ReadInput, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        from workbench.knowledge_service import read_parent

        return read_parent(session, user, body)

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

            result["matches"] = validated_evidence(
                session,
                user,
                result["matches"],
                audience=body.audience.model_dump() if body.audience else None,
            )
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
