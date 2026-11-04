"""Verified case facts, rule-based reply drafting and human-confirmed drafts.

Drafts are generated from a structured facts snapshot, never from raw chat
history. Only facts with a source are exposed; the real external refund is
always unknown in this project. A draft must be confirmed by a human, and a
stale facts snapshot blocks confirmation.
"""

import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select

from workbench.after_sales_models import AfterSalesAttachment
from workbench.domain import audit
from workbench.fulfillment_models import RefundRecord, ReturnFulfillment
from workbench.knowledge_models import DocumentVersion
from workbench.models import Conversation, Order, OrderItem, now
from workbench.policy_conflict_models import PolicyConflict
from workbench.reply_draft_models import ReplyDraft
from workbench.reply_validation import validate_draft
from workbench.return_models import ReturnPolicy
from workbench.returns import owned_request


@dataclass
class DraftContext:
    order_number: str
    return_status: str
    refund_status: str | None
    evidence_refs: list[str]
    fulfillment_status: str | None = None


def generate_reply_draft(ctx: DraftContext) -> dict:
    """Rule template first; it works without any model being available."""
    if ctx.return_status == "pending_approval":
        reply = f"您好，订单 {ctx.order_number} 的退货申请目前正在等待审核，暂未完成受理。"
    elif ctx.return_status == "approved":
        reply = f"您好，订单 {ctx.order_number} 的退货申请已通过审核，后续受理进度仍需进一步确认。"
    elif ctx.return_status == "accepted_simulated":
        reply = (
            f"您好，订单 {ctx.order_number} 的退货申请已完成系统模拟受理。"
            "这不代表实际退款已经完成。"
        )
        reply += {
            "SUCCEEDED": " 模拟退款流程已成功完成，实际到账结果尚未确认。",
            "UNKNOWN": " 退款结果暂未确认，我们正在进一步核验。",
            "FAILED": " 退款未执行成功，需要人工跟进处理。",
            "PENDING": " 退款正在排队处理，结果尚未确认。",
            "PROCESSING": " 退款正在处理中，结果尚未确认。",
        }.get(ctx.refund_status or "", " 目前尚未确认实际退款结果。")
    elif ctx.return_status == "rejected":
        reply = f"您好，订单 {ctx.order_number} 的退货申请未通过审核，具体原因请以审批意见为准。"
    elif ctx.return_status == "cancelled":
        reply = f"您好，订单 {ctx.order_number} 的退货申请已取消。"
    else:
        reply = f"您好，订单 {ctx.order_number} 的售后状态仍需进一步核实。"
    return {
        "content": reply,
        "status": "draft",
        "evidence_refs": ctx.evidence_refs,
        "requires_confirmation": True,
    }


def _hash(snapshot: dict) -> str:
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


SNAPSHOT_VERSION = 2
RETURN_CLAIMS = {
    "pending_approval": "您的退货申请目前正在等待审核，暂未完成受理。",
    "approved": "您的退货申请已通过审核，后续受理进度仍需进一步确认。",
    "accepted_simulated": "您的退货申请已完成系统模拟受理，这不代表实际退款已经完成。",
    "rejected": "您的退货申请未通过审核，具体原因请以审批意见为准。",
    "cancelled": "您的退货申请已取消。",
}
REFUND_CLAIMS = {
    "SUCCEEDED": "模拟退款流程已成功完成，实际到账结果尚未确认。",
    "UNKNOWN": "退款结果暂未确认，我们正在进一步核验。",
    "FAILED": "退款未执行成功，需要人工跟进处理。",
    "PENDING": "退款正在排队处理，结果尚未确认。",
    "PROCESSING": "退款正在处理中，结果尚未确认。",
}


def _allowed_claims(facts: dict) -> dict:
    claims = {"order_number": "订单 " + facts["order_number"]}
    status = facts["return_status"]
    claims["return_status_" + status] = RETURN_CLAIMS.get(
        status, "售后状态仍需进一步核实。"
    )
    refund = facts.get("refund_status")
    claims["refund_status_" + (refund or "unknown")] = (
        REFUND_CLAIMS.get(refund, "目前尚未确认实际退款结果。")
        if refund
        else "目前尚未确认实际退款结果。"
    )
    if facts.get("pending_evidence_count"):
        claims["evidence_pending_review"] = "目前仍需要进一步核验相关材料。"
    if facts.get("unresolved_conflicts"):
        claims["policy_conflict_pending"] = (
            "相关政策存在待人工裁决的冲突，暂不能给出确定结论。"
        )
    return claims


def _verified_facts_map(verified_facts: list) -> dict:
    mapping = {}
    for item in verified_facts:
        key = item["name"]
        if key in mapping:
            key = key + ":" + item["source_id"]
        mapping[key] = {**item, "verified": True}
    return mapping


def _stale(row, facts: dict) -> bool:
    """Old drafts keep their original hash scope; new drafts compare the full v2 snapshot."""
    stored = row.facts_snapshot or {}
    snapshot = stored.get("snapshot")
    current = facts["snapshot"]
    if snapshot and stored.get("snapshot_version") is None:
        comparable = {key: current.get(key) for key in snapshot}
        return _hash(comparable) != row.facts_snapshot_hash
    return facts["facts_snapshot_hash"] != row.facts_snapshot_hash


def _scoped_conversation(session, actor, conversation_id):
    if not conversation_id:
        return
    row = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == actor.organization_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Conversation not found")


def _refund(session, actor, fulfillment_id):
    return session.scalar(
        select(RefundRecord)
        .where(
            RefundRecord.fulfillment_id == fulfillment_id,
            RefundRecord.organization_id == actor.organization_id,
        )
        .order_by(RefundRecord.requested_at.desc(), RefundRecord.id.desc())
    )


def collect_case_facts(session, actor, request_id) -> dict:
    request = owned_request(session, actor, request_id)
    order = session.get(Order, request.order_id)
    item = session.get(OrderItem, request.item_id)
    fulfillment = session.scalar(
        select(ReturnFulfillment).where(
            ReturnFulfillment.return_request_id == request.id,
            ReturnFulfillment.organization_id == actor.organization_id,
        )
    )
    refund = _refund(session, actor, fulfillment.id) if fulfillment else None
    evidence = session.scalars(
        select(AfterSalesAttachment).where(
            AfterSalesAttachment.organization_id == actor.organization_id,
            AfterSalesAttachment.return_request_id == request.id,
        )
    ).all()
    verified_evidence = [
        {"id": row.id, "kind": row.kind, "filename": row.filename}
        for row in evidence
        if row.status == "VERIFIED"
    ]
    pending_evidence = sum(
        1
        for row in evidence
        if row.status in {"UPLOADED", "PROCESSING", "PENDING_REVIEW", "EXTRACTION_FAILED"}
    )
    policy = session.get(ReturnPolicy, request.policy_id)
    policy_refs = []
    policy_evidence = []
    policy_version = (
        session.get(DocumentVersion, policy.version_id) if policy else None
    )
    if policy:
        for name in ("scope_clause", "window_clause", "condition_clause", "quality_clause"):
            clause = policy.rules.get(name)
            if clause and clause.get("chunk_id"):
                policy_refs.append(clause["chunk_id"])
                policy_evidence.append(
                    {
                        "policy_id": policy.id,
                        "version_id": policy.version_id,
                        "version_number": policy_version.number if policy_version else None,
                        "chunk_id": clause["chunk_id"],
                        "purpose": name,
                        "quote": clause.get("quote"),
                    }
                )
    conflicts = session.scalars(
        select(PolicyConflict).where(
            PolicyConflict.organization_id == actor.organization_id,
            PolicyConflict.status == "pending_review",
        )
    ).all()
    sku = item.sku if item else None
    unresolved_conflicts = [
        row for row in conflicts if sku and any(rule.split(":")[-1] == sku for rule in row.rule_ids)
    ]

    fulfillment_status = fulfillment.status if fulfillment else None
    refund_status = refund.refund_status if refund else None
    refund_simulated = refund.simulated_outcome if refund else None

    evidence_refs = sorted(set(policy_refs) | {row["id"] for row in verified_evidence})
    verified_facts = [
        {
            "name": "return_status",
            "value": request.status,
            "source": "return_request",
            "source_id": request.id,
        },
        {
            "name": "order_number",
            "value": order.number,
            "source": "order",
            "source_id": order.id,
        },
        {
            "name": "return_quantity",
            "value": request.quantity,
            "source": "return_request",
            "source_id": request.id,
        },
    ]
    if request.receipt_id:
        verified_facts.append(
            {
                "name": "receipt_id",
                "value": request.receipt_id,
                "source": "return_request",
                "source_id": request.id,
            }
        )
    if request.decision_reason:
        verified_facts.append(
            {
                "name": "decision_reason",
                "value": request.decision_reason,
                "source": "return_request",
                "source_id": request.id,
            }
        )
    if fulfillment:
        verified_facts.append(
            {
                "name": "fulfillment_status",
                "value": fulfillment.status,
                "source": "return_fulfillment",
                "source_id": fulfillment.id,
            }
        )
    if refund:
        verified_facts.append(
            {
                "name": "refund_status",
                "value": refund.refund_status,
                "source": "refund_record",
                "source_id": refund.refund_no,
            }
        )
    for entry in verified_evidence:
        verified_facts.append(
            {
                "name": "verified_evidence",
                "value": entry["kind"] + ":" + entry["filename"],
                "source": "after_sales_attachment",
                "source_id": entry["id"],
            }
        )
    for ref in policy_refs:
        verified_facts.append(
            {"name": "policy_clause", "value": ref, "source": "knowledge_chunk", "source_id": ref}
        )

    allowed_claims = _allowed_claims(
        {
            "order_number": order.number,
            "return_status": request.status,
            "refund_status": refund_status,
            "pending_evidence_count": pending_evidence,
            "unresolved_conflicts": len(unresolved_conflicts),
        }
    )
    unknown_facts = [
        "actual_refund_status",
        "refund_payment_method",
        "refund_arrival_date",
    ]
    if refund_status in (None, "PENDING", "PROCESSING", "UNKNOWN"):
        unknown_facts.append("refund_result")
    if pending_evidence:
        unknown_facts.append("pending_evidence_review")
    if unresolved_conflicts:
        unknown_facts.append("policy_conflict_resolution")

    snapshot = {
        "return_status": request.status,
        "return_revision": request.revision,
        "receipt_id": request.receipt_id,
        "decision_reason": request.decision_reason,
        "order_number": order.number,
        "payment_status": order.payment_status,
        "shipping_status": order.shipping_status,
        "fulfillment_status": fulfillment_status,
        "fulfillment_version": fulfillment.version if fulfillment else None,
        "refund_status": refund_status,
        "refund_simulated_outcome": refund_simulated,
        "refund_no": refund.refund_no if refund else None,
        "refund_requested_at": (
            refund.requested_at.isoformat() if refund and refund.requested_at else None
        ),
        "refund_completed_at": (
            refund.completed_at.isoformat() if refund and refund.completed_at else None
        ),
        "verified_evidence_ids": sorted(entry["id"] for entry in verified_evidence),
        "evidence_states": [
            {
                "id": row.id,
                "status": row.status,
                "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            }
            for row in sorted(evidence, key=lambda entry: entry.id)
        ],
        "pending_evidence_count": pending_evidence,
        "policy_evidence_refs": sorted(set(policy_refs)),
        "policy_version_ids": sorted({policy.version_id} if policy else set()),
        "unresolved_conflicts": len(unresolved_conflicts),
        "snapshot_version": SNAPSHOT_VERSION,
    }
    return {
        "return_request_id": request.id,
        "order_number": order.number,
        "order_id": order.id,
        "return_status": request.status,
        "return_revision": request.revision,
        "receipt_id": request.receipt_id,
        "decision_reason": request.decision_reason,
        "quantity": request.quantity,
        "item_condition": request.item_condition,
        "return_basis": request.return_basis,
        "fulfillment_status": fulfillment_status,
        "refund_status": refund_status,
        "refund_simulated_outcome": refund_simulated,
        "verified_evidence": verified_evidence,
        "pending_evidence_count": pending_evidence,
        "policy_evidence_refs": sorted(set(policy_refs)),
        "unresolved_conflicts": len(unresolved_conflicts),
        "payment_status": order.payment_status,
        "shipping_status": order.shipping_status,
        "actual_refund_status": "not_confirmed",
        "actual_refund_executed": False,
        "external_mode": "simulation",
        "evidence_refs": evidence_refs,
        "verified_facts": verified_facts,
        "unknown_facts": unknown_facts,
        "prohibited_claims": [
            "refund_success",
            "refund_arrival_date",
            "refund_arrival_promised",
            "refund_payment_method_confirmed",
            "return_completed",
        ],
        "customer_intent": "query_return_status",
        "intent_source": "static_default",
        "snapshot_version": SNAPSHOT_VERSION,
        "policy_evidence": policy_evidence,
        "allowed_claims": allowed_claims,
        "allowed_fact_ids": sorted(allowed_claims),
        "verified_facts_map": _verified_facts_map(verified_facts),
        "snapshot": snapshot,
        "facts_snapshot_hash": _hash(snapshot),
    }


def draft_view(row, current_facts=None) -> dict:
    facts = row.facts_snapshot or {}
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "return_request_id": row.return_request_id,
        "content": row.content,
        "evidence_refs": row.evidence_refs,
        "status": row.status,
        "version": row.version,
        "created_by": row.created_by,
        "confirmed_by": row.confirmed_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "facts_snapshot": facts,
        "facts_snapshot_hash": row.facts_snapshot_hash,
        "generation": facts.get("generation")
        or {
            "mode": "template",
            "model": None,
            "prompt_version": None,
            "fallback_used": False,
            "fallback_reason": None,
            "usage": None,
        },
        "generation_mode": row.generation_mode,
        "model_name": row.model_name,
        "prompt_version": row.prompt_version,
        "validation_status": row.validation_status,
        "validation_errors": row.validation_errors or [],
        "fallback_used": row.fallback_used,
        "fallback_reason": row.fallback_reason,
        "edited_by_human": row.edited_by_human,
        "confirmed_at": row.confirmed_at,
        "validation": validate_draft(row.content, facts),
        "requires_confirmation": row.status != "confirmed",
        "stale": None if current_facts is None else _stale(row, current_facts),
    }


def _next_version(session, actor, request_id) -> int:
    latest = session.scalar(
        select(func.max(ReplyDraft.version)).where(
            ReplyDraft.organization_id == actor.organization_id,
            ReplyDraft.return_request_id == request_id,
        )
    )
    return (latest or 0) + 1


def scoped_draft(session, actor, draft_id):
    row = session.scalar(
        select(ReplyDraft).where(
            ReplyDraft.id == draft_id, ReplyDraft.organization_id == actor.organization_id
        )
    )
    if row is None:
        raise HTTPException(404, "Reply draft not found")
    return row


def generate_case_draft(
    session, actor, settings, request_id, conversation_id=None, mode="template"
):
    _scoped_conversation(session, actor, conversation_id)
    facts = collect_case_facts(session, actor, request_id)
    from workbench.reply_generation import generate_reply

    draft = generate_reply(facts, settings, mode)
    generation = draft.get("generation") or {}
    facts = {**facts, "generation": generation}
    validation = validate_draft(draft["content"], facts)
    row = ReplyDraft(
        organization_id=actor.organization_id,
        conversation_id=conversation_id,
        return_request_id=request_id,
        content=draft["content"],
        evidence_refs=facts["evidence_refs"],
        facts_snapshot=facts,
        facts_snapshot_hash=facts["facts_snapshot_hash"],
        status="draft",
        version=_next_version(session, actor, request_id),
        created_by=actor.id,
        generation_mode=generation.get("mode") or "template",
        model_name=generation.get("model"),
        prompt_version=generation.get("prompt_version"),
        validation_status="passed" if validation["valid"] else "failed",
        validation_errors=validation["issues"],
        fallback_used=bool(generation.get("fallback_used")),
        fallback_reason=generation.get("fallback_reason"),
        edited_by_human=False,
    )
    session.add(row)
    session.flush()
    audit(
        session,
        actor,
        "reply.draft_created",
        row.id,
        return_request_id=request_id,
        version=row.version,
        generation_mode=generation.get("mode"),
        fallback_used=generation.get("fallback_used"),
        validation_status=row.validation_status,
    )
    session.commit()
    return draft_view(row, facts)


def edit_draft(session, actor, draft_id, content):
    row = scoped_draft(session, actor, draft_id)
    if row.status == "confirmed":
        raise HTTPException(409, "Confirmed drafts cannot be edited")
    facts = collect_case_facts(session, actor, row.return_request_id)
    facts = {
        **facts,
        "generation": {
            "mode": "human_edit",
            "model": None,
            "prompt_version": None,
            "fallback_used": False,
            "fallback_reason": None,
            "usage": None,
        },
    }
    validation = validate_draft(content, facts)
    created = ReplyDraft(
        organization_id=actor.organization_id,
        conversation_id=row.conversation_id,
        return_request_id=row.return_request_id,
        content=content,
        evidence_refs=facts["evidence_refs"],
        facts_snapshot=facts,
        facts_snapshot_hash=facts["facts_snapshot_hash"],
        status="edited",
        version=_next_version(session, actor, row.return_request_id),
        created_by=actor.id,
        generation_mode="human_edit",
        model_name=None,
        prompt_version=None,
        validation_status="passed" if validation["valid"] else "failed",
        validation_errors=validation["issues"],
        fallback_used=False,
        fallback_reason=None,
        edited_by_human=True,
    )
    session.add(created)
    session.flush()
    audit(
        session,
        actor,
        "reply.draft_edited",
        created.id,
        return_request_id=row.return_request_id,
        version=created.version,
        validation_status=created.validation_status,
        edited_by_human=True,
    )
    session.commit()
    return draft_view(created, facts)


def confirm_draft(session, actor, draft_id, expected_hash):
    row = session.scalar(
        select(ReplyDraft)
        .where(
            ReplyDraft.id == draft_id,
            ReplyDraft.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Reply draft not found")
    if row.status not in {"draft", "edited"}:
        raise HTTPException(409, "Reply draft cannot be confirmed in its current state")
    facts = collect_case_facts(session, actor, row.return_request_id)
    if expected_hash != row.facts_snapshot_hash or _stale(row, facts):
        raise HTTPException(409, "业务状态已更新，请重新核对回复内容。")
    validation = validate_draft(row.content, facts)
    row.validation_status = "passed" if validation["valid"] else "failed"
    row.validation_errors = validation["issues"]
    if not validation["valid"]:
        audit(
            session,
            actor,
            "reply.draft_confirm_blocked",
            row.id,
            return_request_id=row.return_request_id,
            validation_errors=len(validation["issues"]),
        )
        session.commit()
        raise HTTPException(409, "草稿包含无事实依据的表述，请修改后重新核对。")
    row.status = "confirmed"
    row.confirmed_by = actor.id
    row.confirmed_at = now()
    row.updated_at = now()
    audit(
        session,
        actor,
        "reply.draft_confirmed",
        row.id,
        return_request_id=row.return_request_id,
        generation_mode=row.generation_mode,
        validation_status=row.validation_status,
        fallback_used=row.fallback_used,
        edited_by_human=row.edited_by_human,
    )
    session.commit()
    return draft_view(row, facts)


def list_drafts(session, actor, request_id=None, limit=50, offset=0):
    statement = select(ReplyDraft).where(ReplyDraft.organization_id == actor.organization_id)
    if request_id:
        statement = statement.where(ReplyDraft.return_request_id == request_id)
    rows = session.scalars(
        statement.order_by(ReplyDraft.created_at.desc(), ReplyDraft.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [draft_view(row) for row in rows],
        "limit": limit,
        "offset": offset,
    }


def draft_detail(session, actor, draft_id):
    row = scoped_draft(session, actor, draft_id)
    try:
        current = collect_case_facts(session, actor, row.return_request_id)
    except HTTPException:
        current = None
    return draft_view(row, current)