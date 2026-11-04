"""One case, one trail: traces, runs, tool calls, audits and business state."""

from sqlalchemy import or_, select

from workbench.after_sales_models import AfterSalesAttachment
from workbench.fulfillment_models import ReturnFulfillment
from workbench.knowledge_models import ToolInvocation
from workbench.models import AuditRecord, Order, OrderItem, Run, RunEvent
from workbench.policy_conflict_models import PolicyConflict, PolicyConflictReview
from workbench.reply_draft_models import ReplyDraft
from workbench.return_models import ReturnRequest
from workbench.trace_models import TraceRecord, TraceSpan
from workbench.tracing import span


def _request_scope(actor, statement):
    if actor.role not in {"admin", "approver"}:
        statement = statement.where(ReturnRequest.created_by == actor.id)
    return statement


def _find_request(session, actor, resource_id):
    statement = _request_scope(
        actor,
        select(ReturnRequest).where(
            ReturnRequest.id == resource_id, ReturnRequest.organization_id == actor.organization_id
        ),
    )
    request = session.scalar(statement)
    if request is not None:
        return request
    attachment = session.scalar(
        select(AfterSalesAttachment).where(
            AfterSalesAttachment.id == resource_id,
            AfterSalesAttachment.organization_id == actor.organization_id,
        )
    )
    draft = session.scalar(
        select(ReplyDraft).where(
            ReplyDraft.id == resource_id, ReplyDraft.organization_id == actor.organization_id
        )
    )
    request_id = None
    if attachment is not None:
        request_id = attachment.return_request_id
    elif draft is not None:
        request_id = draft.return_request_id
    if request_id:
        return session.scalar(
            _request_scope(
                actor,
                select(ReturnRequest).where(
                    ReturnRequest.id == request_id,
                    ReturnRequest.organization_id == actor.organization_id,
                ),
            )
        )
    return None


def _request_bundle(session, actor, request):
    order = session.get(Order, request.order_id)
    item = session.get(OrderItem, request.item_id)
    evidence = session.scalars(
        select(AfterSalesAttachment)
        .where(
            AfterSalesAttachment.organization_id == actor.organization_id,
            AfterSalesAttachment.return_request_id == request.id,
        )
        .order_by(AfterSalesAttachment.created_at)
    ).all()
    drafts = session.scalars(
        select(ReplyDraft)
        .where(
            ReplyDraft.organization_id == actor.organization_id,
            ReplyDraft.return_request_id == request.id,
        )
        .order_by(ReplyDraft.version)
    ).all()
    fulfillment = session.scalar(
        select(ReturnFulfillment).where(
            ReturnFulfillment.organization_id == actor.organization_id,
            ReturnFulfillment.return_request_id == request.id,
        )
    )
    sku = item.sku if item else None
    conflicts = [
        row
        for row in session.scalars(
            select(PolicyConflict).where(
                PolicyConflict.organization_id == actor.organization_id
            )
        ).all()
        if sku and any(rule.split(":")[-1] == sku for rule in row.rule_ids)
    ]
    return {
        "return_request": {
            "id": request.id,
            "status": request.status,
            "revision": request.revision,
            "quantity": request.quantity,
            "return_basis": request.return_basis,
            "decision_reason": request.decision_reason,
            "receipt_id": request.receipt_id,
        },
        "order": {"id": order.id, "number": order.number, "payment_status": order.payment_status}
        if order
        else None,
        "item": {"id": item.id, "sku": item.sku} if item else None,
        "fulfillment": {
            "id": fulfillment.id,
            "status": fulfillment.status,
            "tracking_number": fulfillment.tracking_number,
        }
        if fulfillment
        else None,
        "evidence": [
            {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "filename": row.filename,
                "extraction_method": row.extraction_method,
            }
            for row in evidence
        ],
        "conflicts": [
            {
                "id": row.id,
                "status": row.status,
                "rule_ids": row.rule_ids,
                "reasons": row.reasons,
            }
            for row in conflicts
        ],
        "drafts": [
            {
                "id": row.id,
                "version": row.version,
                "status": row.status,
                "requires_confirmation": row.status != "confirmed",
            }
            for row in drafts
        ],
    }


def case_trail(session, actor, resource_id):
    with span("business", "case_trail"):
        traces = session.scalars(
            select(TraceRecord)
            .where(
                TraceRecord.organization_id == actor.organization_id,
                TraceRecord.owner_id == actor.id,
                or_(
                    TraceRecord.resource_id == resource_id,
                    TraceRecord.run_id == resource_id,
                    TraceRecord.id == resource_id,
                ),
            )
            .order_by(TraceRecord.created_at.desc(), TraceRecord.id)
            .limit(50)
        ).all()
        run_ids = sorted({row.run_id for row in traces if row.run_id})
        trace_ids = [row.id for row in traces]
        spans = session.scalars(
            select(TraceSpan)
            .where(TraceSpan.trace_id.in_(trace_ids))
            .order_by(TraceSpan.started_at, TraceSpan.id)
        ).all() if trace_ids else []
        runs = session.scalars(
            select(Run)
            .where(Run.organization_id == actor.organization_id, Run.id.in_(run_ids))
            .order_by(Run.created_at.desc())
        ).all() if run_ids else []
        events = session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id.in_(run_ids))
            .order_by(RunEvent.id.asc())
        ).all() if run_ids else []
        tools = session.scalars(
            select(ToolInvocation)
            .where(
                ToolInvocation.organization_id == actor.organization_id,
                ToolInvocation.run_id.in_(run_ids),
            )
            .order_by(ToolInvocation.created_at.asc())
        ).all() if run_ids else []
        audit_statement = select(AuditRecord).where(
            AuditRecord.organization_id == actor.organization_id,
            AuditRecord.resource_id == resource_id,
        )
        if actor.role == "support":
            audit_statement = audit_statement.where(AuditRecord.actor_id == actor.id)
        audits = session.scalars(audit_statement.order_by(AuditRecord.created_at.asc())).all()

        request = _find_request(session, actor, resource_id)
        conflict = session.scalar(
            select(PolicyConflict).where(
                PolicyConflict.id == resource_id,
                PolicyConflict.organization_id == actor.organization_id,
            )
        )
        reviews = session.scalars(
            select(PolicyConflictReview)
            .where(
                PolicyConflictReview.organization_id == actor.organization_id,
                PolicyConflictReview.conflict_id == resource_id,
            )
            .order_by(PolicyConflictReview.created_at)
        ).all() if conflict else []

        business = {}
        if request is not None:
            business = _request_bundle(session, actor, request)
        elif conflict is not None:
            business = {
                "policy_conflict": {
                    "id": conflict.id,
                    "status": conflict.status,
                    "rule_ids": conflict.rule_ids,
                    "reasons": conflict.reasons,
                    "resolution": conflict.resolution,
                },
                "reviews": [
                    {
                        "decision": row.decision,
                        "preferred_rule_id": row.preferred_rule_id,
                        "resolution_note": row.resolution_note,
                        "created_at": row.created_at,
                    }
                    for row in reviews
                ],
            }

        return {
            "resource_id": resource_id,
            "traces": [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "job_id": row.job_id,
                    "resource_kind": row.resource_kind,
                    "operation": row.operation,
                    "created_at": row.created_at,
                }
                for row in traces
            ],
            "runs": [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "status": row.status,
                    "owner_id": row.owner_id,
                    "created_at": row.created_at,
                    "events": [
                        {
                            "id": event.id,
                            "type": event.type,
                            "payload": {
                                key: event.payload[key]
                                for key in ("name", "status", "error_code")
                                if key in event.payload
                            },
                        }
                        for event in events
                        if event.run_id == row.id
                    ],
                }
                for row in runs
            ],
            "tools": [
                {
                    "id": row.id,
                    "name": row.name,
                    "status": row.status,
                    "error_code": row.error_code,
                    "elapsed_ms": row.elapsed_ms,
                    "created_at": row.created_at,
                }
                for row in tools
            ],
            "spans": [
                {
                    "id": row.id,
                    "trace_id": row.trace_id,
                    "parent_id": row.parent_id,
                    "service": row.service,
                    "operation": row.operation,
                    "status": row.status,
                    "error_code": row.error_code,
                    "elapsed_ms": row.elapsed_ms,
                }
                for row in spans
            ],
            "audits": [
                {
                    "id": row.id,
                    "action": row.action,
                    "resource_id": row.resource_id,
                    "details": row.details,
                    "created_at": row.created_at,
                }
                for row in audits
            ],
            "business": business,
        }