"""Thin authenticated return APIs; MCP and Agent reuse the same domain functions."""

from fastapi import Depends, Query
from sqlalchemy import select

from workbench import returns
from workbench.knowledge_models import DocumentVersion, KnowledgeChunk
from workbench.return_models import ReturnPolicy, ReturnRequest
from workbench.return_schemas import (
    ReturnConfirm,
    ReturnCorrection,
    ReturnDecision,
    ReturnInput,
    ReturnPolicyInput,
    ReturnRevision,
)


def attach_return_routes(app, session_dependency, actor_dependency, require, key_dependency):
    @app.get("/api/v1/returns/policy-sources/{version_id}")
    def policy_sources(
        version_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=20),
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        from fastapi import HTTPException

        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.organization_id == user.organization_id,
                DocumentVersion.policy_scope == "returns",
            )
        )
        if not version:
            raise HTTPException(404, "Return document not found")
        chunks = session.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.version_id == version_id,
                KnowledgeChunk.organization_id == user.organization_id,
            )
            .order_by(KnowledgeChunk.position)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        from workbench.knowledge_service import validated_evidence

        evidence = validated_evidence(
            session,
            user,
            [
                {"id": chunk.id, "version_id": version.id, "content": chunk.content}
                for chunk in chunks[:limit]
            ],
        )
        # Bound the picker, not a full-document export. Existing file download remains available.
        return {
            "items": evidence,
            "limit": limit,
            "offset": offset,
            "has_more": len(chunks) > limit,
        }

    @app.post("/api/v1/returns/policies", status_code=201)
    def register(
        body: ReturnPolicyInput, user=Depends(require("admin")), session=Depends(session_dependency)
    ):
        policy = returns.register_policy(session, user, body)
        return {
            "id": policy.id,
            "version_id": policy.version_id,
            "rules": policy.rules,
            "evidence": returns.policy_evidence(session, user, policy),
        }

    @app.get("/api/v1/returns/policies")
    def policies(user=Depends(actor_dependency), session=Depends(session_dependency)):
        rows = session.scalars(
            select(ReturnPolicy)
            .where(ReturnPolicy.organization_id == user.organization_id)
            .order_by(ReturnPolicy.created_at.desc())
            .limit(100)
        ).all()
        from fastapi import HTTPException

        result = []
        for row in rows:
            try:
                evidence = returns.policy_evidence(session, user, row)
            except HTTPException:
                continue  # Withdrawn/expired rules cannot be offered for a new application.
            result.append(
                {
                    "id": row.id,
                    "version_id": row.version_id,
                    "rules": row.rules,
                    "evidence": evidence,
                }
            )
        return {"items": result}

    @app.post("/api/v1/returns/evaluate")
    def evaluate(
        body: ReturnInput, user=Depends(require("support")), session=Depends(session_dependency)
    ):
        return returns.evaluate(session, user, body)[2]

    @app.post("/api/v1/returns/requests", status_code=201)
    def create(
        body: ReturnInput,
        user=Depends(require("support")),
        session=Depends(session_dependency),
        key=Depends(key_dependency),
    ):
        return returns.request_view(session, user, returns.create_request(session, user, body, key))

    @app.get("/api/v1/returns/requests")
    def requests(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        statement = select(ReturnRequest).where(
            ReturnRequest.organization_id == user.organization_id
        )
        if user.role not in {"admin", "approver"}:
            statement = statement.where(ReturnRequest.created_by == user.id)
        rows = session.scalars(
            statement.order_by(ReturnRequest.created_at.desc(), ReturnRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return {"items": [returns.request_view(session, user, row) for row in rows]}

    @app.get("/api/v1/returns/requests/{identifier}")
    def status(
        identifier: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        return returns.request_view(session, user, returns.owned_request(session, user, identifier))

    @app.get("/api/v1/returns/requests/{identifier}/preview")
    def preview(
        identifier: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        return returns.preview_request(
            session, user, returns.owned_request(session, user, identifier)
        )

    @app.post("/api/v1/returns/requests/{identifier}/correct")
    def correct(
        identifier: str,
        body: ReturnCorrection,
        user=Depends(require("support")),
        session=Depends(session_dependency),
    ):
        return returns.request_view(
            session, user, returns.correct_request(session, user, identifier, body)
        )

    @app.post("/api/v1/returns/requests/{identifier}/confirm")
    def confirm(
        identifier: str,
        body: ReturnConfirm,
        user=Depends(require("support")),
        session=Depends(session_dependency),
    ):
        return returns.request_view(
            session, user, returns.confirm_request(session, user, identifier, body)
        )

    @app.post("/api/v1/returns/requests/{identifier}/decision")
    def decision(
        identifier: str,
        body: ReturnDecision,
        user=Depends(require("approver")),
        session=Depends(session_dependency),
    ):
        return returns.request_view(
            session, user, returns.decide_request(session, user, identifier, body)
        )

    @app.post("/api/v1/returns/requests/{identifier}/submit-simulated")
    def submit(
        identifier: str,
        body: ReturnRevision,
        user=Depends(require("support")),
        session=Depends(session_dependency),
    ):
        return returns.request_view(
            session, user, returns.submit_simulated(session, user, identifier, body)
        )

    @app.post("/api/v1/returns/requests/{identifier}/cancel")
    def cancel(
        identifier: str,
        body: ReturnRevision,
        user=Depends(require("support")),
        session=Depends(session_dependency),
    ):
        return returns.request_view(
            session, user, returns.cancel_request(session, user, identifier, body)
        )
