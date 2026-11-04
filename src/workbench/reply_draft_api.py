"""Customer reply draft generation, editing, confirmation and history."""

from fastapi import Depends, Query

from workbench.reply_draft_schemas import ConfirmDraft, EditDraft, GenerateDraft
from workbench.reply_drafts import (
    confirm_draft,
    draft_detail,
    edit_draft,
    generate_case_draft,
    list_drafts,
)


def attach_reply_draft_routes(app, settings, session_dependency, actor_dependency, require):
    @app.post("/api/v1/reply-drafts", status_code=201)
    def create(
        body: GenerateDraft,
        user=Depends(require("support", "admin")),
        session=Depends(session_dependency),
    ):
        return generate_case_draft(
            session,
            user,
            settings,
            body.return_request_id,
            body.conversation_id,
            body.mode,
        )

    @app.get("/api/v1/reply-drafts")
    def drafts(
        return_request_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        return list_drafts(session, user, return_request_id, limit, offset)

    @app.get("/api/v1/reply-drafts/{draft_id}")
    def detail(
        draft_id: str,
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        return draft_detail(session, user, draft_id)

    @app.post("/api/v1/reply-drafts/{draft_id}/edit")
    def edit(
        draft_id: str,
        body: EditDraft,
        user=Depends(require("support", "admin")),
        session=Depends(session_dependency),
    ):
        return edit_draft(session, user, draft_id, body.content)

    @app.post("/api/v1/reply-drafts/{draft_id}/confirm")
    def confirm(
        draft_id: str,
        body: ConfirmDraft,
        user=Depends(require("support", "admin")),
        session=Depends(session_dependency),
    ):
        return confirm_draft(session, user, draft_id, body.expected_facts_snapshot_hash)