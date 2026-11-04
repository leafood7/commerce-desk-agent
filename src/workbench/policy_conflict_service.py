"""Policy conflict checking, recording and human adjudication."""

import hashlib
from itertools import combinations

from fastapi import HTTPException
from sqlalchemy import select

from workbench.domain import audit
from workbench.knowledge_models import DocumentVersion
from workbench.models import now
from workbench.policy_conflict_models import PolicyConflict, PolicyConflictReview
from workbench.policy_conflict_schemas import PolicyRuleInput
from workbench.policy_conflicts import PolicyRule, detect_conflict
from workbench.return_models import ReturnPolicy


def rule_signature(rule_ids) -> str:
    return hashlib.sha256("|".join(sorted(rule_ids)).encode()).hexdigest()[:32]


def conflict_view(row) -> dict:
    return {
        "id": row.id,
        "conflict_type": row.conflict_type,
        "rule_ids": row.rule_ids,
        "version_ids": row.version_ids,
        "evidence_refs": row.evidence_refs,
        "reasons": row.reasons,
        "status": row.status,
        "resolution": row.resolution,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def review_view(row) -> dict:
    return {
        "id": row.id,
        "decision": row.decision,
        "preferred_rule_id": row.preferred_rule_id,
        "resolution_note": row.resolution_note,
        "reviewed_by": row.reviewed_by,
        "created_at": row.created_at,
    }


def rule_from_input(item: PolicyRuleInput) -> PolicyRule:
    version_key = item.version_id or (
        str(item.version_number) if item.version_number is not None else ""
    )
    return PolicyRule(
        id=f"{item.policy_id}:{version_key}:{item.sku}",
        sku=item.sku,
        return_basis=item.return_basis,
        sales_channel=item.sales_channel,
        effective_from=item.effective_from,
        effective_until=item.effective_until,
        window_days=item.window_days,
        requires_unused=item.requires_unused,
        priority=item.priority,
        policy_id=item.policy_id,
        version_id=item.version_id,
        version_number=item.version_number,
        source_refs=tuple(item.source_refs),
    )


def rules_from_version(session, actor, version_id):
    """Reuse administrator-confirmed policy mappings and version validity dates."""
    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.organization_id == actor.organization_id,
        )
    )
    if version is None:
        raise HTTPException(404, "Policy version not found")
    policies = session.scalars(
        select(ReturnPolicy).where(
            ReturnPolicy.organization_id == actor.organization_id,
            ReturnPolicy.version_id == version_id,
        )
    ).all()
    rules, skipped = [], []
    for policy in policies:
        sales_channel = policy.rules.get("sales_channel")
        for sku in policy.rules.get("skus", []):
            rule_id = f"{policy.id}:{version.number}:{sku}"
            missing = []
            if version.effective_from is None:
                missing.append("effective_from")
            if not sales_channel:
                missing.append("sales_channel")
            if missing:
                skipped.append(
                    {"rule_id": rule_id, "policy_id": policy.id, "missing": missing}
                )
                continue
            refs = []
            for name in ("scope_clause", "window_clause", "condition_clause", "quality_clause"):
                clause = policy.rules.get(name)
                if clause and clause.get("chunk_id"):
                    refs.append(clause["chunk_id"])
            rules.append(
                PolicyRule(
                    id=rule_id,
                    sku=sku,
                    return_basis=policy.rules.get("return_basis", "no_reason"),
                    sales_channel=sales_channel,
                    effective_from=version.effective_from,
                    effective_until=version.effective_until,
                    window_days=policy.rules["window_days"],
                    requires_unused=policy.rules["requires_unused"],
                    priority=policy.rules.get("priority"),
                    policy_id=policy.id,
                    version_id=version.id,
                    version_number=version.number,
                    source_refs=tuple(refs),
                )
            )
    return rules, skipped


def _record(session, actor, a: PolicyRule, b: PolicyRule, result: dict):
    signature = rule_signature([a.id, b.id])
    existing = session.scalar(
        select(PolicyConflict).where(
            PolicyConflict.organization_id == actor.organization_id,
            PolicyConflict.rule_signature == signature,
            PolicyConflict.status == "pending_review",
        )
    )
    if existing is not None:
        return None
    conflict = PolicyConflict(
        organization_id=actor.organization_id,
        conflict_type=",".join(result["reasons"]),
        rule_ids=[a.id, b.id],
        version_ids=[v for v in (a.version_id, b.version_id) if v],
        evidence_refs=sorted(set(a.source_refs) | set(b.source_refs)),
        reasons=result["reasons"],
        status="pending_review",
        rule_signature=signature,
        created_by=actor.id,
    )
    session.add(conflict)
    session.flush()
    audit(
        session,
        actor,
        "policy.conflict_detected",
        conflict.id,
        rule_ids=conflict.rule_ids,
        reasons=conflict.reasons,
    )
    return conflict


def pending_exists(session, actor) -> bool:
    return (
        session.scalar(
            select(PolicyConflict.id).where(
                PolicyConflict.organization_id == actor.organization_id,
                PolicyConflict.status == "pending_review",
            )
        )
        is not None
    )


def check_rules(session, actor, rules, skipped):
    unique = {rule.id: rule for rule in rules}
    ordered = [unique[key] for key in sorted(unique)]
    pairs, created = [], []
    for a, b in combinations(ordered, 2):
        result = detect_conflict(a, b)
        pairs.append(
            {
                "rule_ids": [a.id, b.id],
                "status": result["status"],
                "reasons": result["reasons"],
                "preferred_rule_id": result.get("preferred_rule_id"),
            }
        )
        if result["status"] == "POTENTIAL_CONFLICT":
            conflict = _record(session, actor, a, b, result)
            if conflict is not None:
                created.append(conflict_view(conflict))
    session.commit()
    return {
        "rules": len(ordered),
        "pairs": pairs,
        "conflicts": created,
        "skipped": skipped,
        "blocks_automatic_decision": pending_exists(session, actor),
    }


def list_conflicts(session, actor, status=None, limit=50, offset=0):
    statement = select(PolicyConflict).where(
        PolicyConflict.organization_id == actor.organization_id
    )
    if status:
        statement = statement.where(PolicyConflict.status == status)
    rows = session.scalars(
        statement.order_by(PolicyConflict.created_at.desc(), PolicyConflict.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [conflict_view(row) for row in rows],
        "blocks_automatic_decision": pending_exists(session, actor),
        "limit": limit,
        "offset": offset,
    }


def scoped_conflict(session, actor, conflict_id):
    row = session.scalar(
        select(PolicyConflict).where(
            PolicyConflict.id == conflict_id,
            PolicyConflict.organization_id == actor.organization_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Policy conflict not found")
    return row


def conflict_detail(session, actor, conflict_id):
    row = scoped_conflict(session, actor, conflict_id)
    reviews = session.scalars(
        select(PolicyConflictReview)
        .where(
            PolicyConflictReview.organization_id == actor.organization_id,
            PolicyConflictReview.conflict_id == row.id,
        )
        .order_by(PolicyConflictReview.created_at.asc(), PolicyConflictReview.id.asc())
    ).all()
    return {"conflict": conflict_view(row), "reviews": [review_view(item) for item in reviews]}


def resolve_conflict(session, actor, conflict_id, decision, preferred_rule_id, note):
    row = scoped_conflict(session, actor, conflict_id)
    if row.status != "pending_review":
        raise HTTPException(409, "Policy conflict is not pending review")
    if decision == "preferred_rule":
        if preferred_rule_id not in row.rule_ids:
            raise HTTPException(422, "Preferred rule is not part of this conflict")
    else:
        preferred_rule_id = None
    reviewed_at = now()
    review = PolicyConflictReview(
        organization_id=actor.organization_id,
        conflict_id=row.id,
        decision=decision,
        preferred_rule_id=preferred_rule_id,
        resolution_note=note,
        reviewed_by=actor.id,
        created_at=reviewed_at,
    )
    session.add(review)
    row.status = "resolved"
    row.resolution = {
        "decision": decision,
        "preferred_rule_id": preferred_rule_id,
        "note": note,
        "reviewed_by": actor.id,
        "reviewed_at": reviewed_at.isoformat(),
    }
    row.updated_at = reviewed_at
    session.flush()
    audit(
        session,
        actor,
        "policy.conflict_resolved",
        row.id,
        decision=decision,
        preferred_rule_id=preferred_rule_id,
    )
    session.commit()
    return conflict_view(row)