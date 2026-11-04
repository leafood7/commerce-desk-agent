"""Policy conflict check, list, detail and human adjudication endpoints."""

from fastapi import Depends, HTTPException, Query

from workbench.policy_conflict_schemas import CheckConflicts, ResolveConflict
from workbench.policy_conflict_service import (
    check_rules,
    conflict_detail,
    list_conflicts,
    pending_exists,
    resolve_conflict,
    rule_from_input,
    rules_from_version,
)


def attach_policy_conflict_routes(app, session_dependency, actor_dependency, require):
    @app.post("/api/v1/policies/conflicts/check")
    def check(
        body: CheckConflicts,
        user=Depends(require("admin")),
        session=Depends(session_dependency),
    ):
        rules = [rule_from_input(item) for item in body.rules]
        skipped = []
        for version_id in body.version_ids:
            version_rules, version_skipped = rules_from_version(session, user, version_id)
            rules.extend(version_rules)
            skipped.extend(version_skipped)
        if not rules:
            if not skipped:
                raise HTTPException(422, "No comparable policy rules")
            # Undeclared scope is reported, never guessed into a conflict.
            return {
                "rules": 0,
                "pairs": [],
                "conflicts": [],
                "skipped": skipped,
                "blocks_automatic_decision": pending_exists(session, user),
            }
        return check_rules(session, user, rules, skipped)

    @app.get("/api/v1/policies/conflicts")
    def conflicts(
        status: str | None = Query(default=None),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        if status not in (None, "pending_review", "resolved"):
            raise HTTPException(422, "Unsupported conflict status")
        return list_conflicts(session, user, status, limit, offset)

    @app.get("/api/v1/policies/conflicts/{conflict_id}")
    def detail(
        conflict_id: str,
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        return conflict_detail(session, user, conflict_id)

    @app.post("/api/v1/policies/conflicts/{conflict_id}/resolve")
    def resolve(
        conflict_id: str,
        body: ResolveConflict,
        user=Depends(require("approver")),
        session=Depends(session_dependency),
    ):
        return resolve_conflict(
            session, user, conflict_id, body.decision, body.preferred_rule_id, body.note
        )