# ruff: noqa: F811
"""PR-05 end-to-end: evidence -> evidence review -> policy conflict -> reply draft."""

from sqlalchemy import select
from test_after_sales_attachments import make_pdf
from test_returns import confirm, draft, return_case  # noqa: F401

from workbench.models import AuditRecord, OrderItem
from workbench.return_models import ReturnRequest

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 20


def upload(client, headers, request_id, filename, data, content_type, kind):
    return client.post(
        "/api/v1/after-sales/attachments",
        files={"file": (filename, data, content_type)},
        data={"kind": kind, "return_request_id": request_id},
        headers=headers("support", "demo-a"),
    )


def conflict_rules(sku):
    common = {
        "sku": sku,
        "return_basis": "no_reason",
        "sales_channel": "official_store",
        "effective_from": "2026-10-01",
        "effective_until": "2026-12-31",
        "requires_unused": True,
    }
    return [
        {**common, "policy_id": "POLICY-NORMAL", "window_days": 7},
        {**common, "policy_id": "POLICY-PROMO", "window_days": 3},
    ]


def test_full_after_sales_demo_chain(return_case):
    client, app, headers, body, policy = return_case
    request = draft(return_case)
    pending = confirm(return_case, request)
    assert pending.status_code == 200, pending.text
    assert pending.json()["status"] == "pending_approval"

    photo = upload(client, headers, request["id"], "photo.png", PNG, "image/png", "product_photo")
    assert photo.status_code == 201, photo.text
    assert photo.json()["status"] == "PENDING_REVIEW"
    assert photo.json()["extraction_method"] == "manual_review"

    invoice_text = f"Invoice Order Number: {body['order_number']} Amount: 199.00 Date: 2026-10-01"
    invoice = upload(
        client, headers, request["id"], "invoice.pdf", make_pdf(invoice_text), "application/pdf", "invoice"
    )
    assert invoice.status_code == 201, invoice.text
    assert invoice.json()["status"] == "PENDING_REVIEW"
    assert invoice.json()["extraction_method"] == "pdf_text_rules"
    fields = {item["field"]: item["value"] for item in invoice.json()["extracted_data"]["fields"]}
    assert fields == {"order_number": body["order_number"], "amount": "199.00", "date": "2026-10-01"}

    reviewed = client.post(
        f"/api/v1/after-sales/attachments/{photo.json()['id']}/review",
        headers=headers("support", "demo-a"),
        json={"decision": "verified", "note": "照片与申请商品一致"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "VERIFIED"

    with app.state.database.sessions() as session:
        sku = session.get(OrderItem, body["item_id"]).sku
    checked = client.post(
        "/api/v1/policies/conflicts/check",
        headers=headers("admin", "demo-a"),
        json={"rules": conflict_rules(sku)},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["blocks_automatic_decision"] is True
    conflict = checked.json()["conflicts"][0]
    assert conflict["status"] == "pending_review"
    assert conflict["evidence_refs"] == []

    resolved = client.post(
        f"/api/v1/policies/conflicts/{conflict['id']}/resolve",
        headers=headers("approver", "demo-a"),
        json={
            "decision": "preferred_rule",
            "preferred_rule_id": conflict["rule_ids"][1],
            "note": "促销政策是已审核的明确例外。",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"

    decision = client.post(
        f"/api/v1/returns/requests/{request['id']}/decision",
        headers=headers("approver", "demo-a"),
        json={"action": "approve", "reason": "证据已核验", "expected_revision": 1},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "approved"

    submitted = client.post(
        f"/api/v1/returns/requests/{request['id']}/submit-simulated",
        headers=headers("support", "demo-a"),
        json={"expected_revision": 1},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "accepted_simulated"
    assert submitted.json()["actual_refund_executed"] is False

    generated = client.post(
        "/api/v1/reply-drafts",
        headers=headers("support", "demo-a"),
        json={"return_request_id": request["id"]},
    )
    assert generated.status_code == 201, generated.text
    draft_body = generated.json()
    facts = draft_body["facts_snapshot"]
    assert facts["return_status"] == "accepted_simulated"
    assert len(facts["verified_evidence"]) == 1
    assert facts["pending_evidence_count"] == 1
    assert facts["unresolved_conflicts"] == 0
    assert "policy_conflict_resolution" not in facts["unknown_facts"]
    assert "pending_evidence_review" in facts["unknown_facts"]
    assert "系统模拟受理" in draft_body["content"]
    assert "不代表实际退款已经完成" in draft_body["content"]
    assert draft_body["validation"]["valid"] is True

    edited = client.post(
        f"/api/v1/reply-drafts/{draft_body['id']}/edit",
        headers=headers("support", "demo-a"),
        json={"content": draft_body["content"] + " 如需进一步帮助，请随时联系我们。"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    confirmed = client.post(
        f"/api/v1/reply-drafts/{edited.json()['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": edited.json()["facts_snapshot_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    with app.state.database.sessions() as session:
        organization_id = session.get(ReturnRequest, request["id"]).organization_id
        actions = {
            row.action
            for row in session.scalars(
                select(AuditRecord).where(AuditRecord.organization_id == organization_id)
            ).all()
        }
    assert {
        "attachment.uploaded",
        "evidence.reviewed",
        "policy.conflict_detected",
        "policy.conflict_resolved",
        "reply.draft_created",
        "reply.draft_edited",
        "reply.draft_confirmed",
    } <= actions


def test_failure_injection_keeps_original_and_blocks_unfounded_reply(return_case):
    client, app, headers, body, policy = return_case
    request = draft(return_case)

    broken = upload(
        client, headers, request["id"], "broken.pdf", b"not a real pdf", "application/pdf", "invoice"
    )
    assert broken.status_code == 201, broken.text
    assert broken.json()["status"] == "EXTRACTION_FAILED"
    assert broken.json()["extraction_error"] == "pdf_parse_failed"
    original = client.get(
        f"/api/v1/after-sales/attachments/{broken.json()['id']}/file",
        headers=headers("support", "demo-a"),
    )
    assert original.status_code == 200
    assert original.content == b"not a real pdf"

    assert upload(
        client, headers, request["id"], "notes.txt", b"hello", "text/plain", "other"
    ).status_code == 422
    assert upload(
        client, headers, request["id"], "empty.png", b"", "image/png", "product_photo"
    ).status_code == 413
    assert client.get(
        f"/api/v1/after-sales/attachments/{broken.json()['id']}/file",
        headers=headers("support", "demo-b"),
    ).status_code == 404

    generated = client.post(
        "/api/v1/reply-drafts",
        headers=headers("support", "demo-a"),
        json={"return_request_id": request["id"]},
    )
    assert generated.status_code == 201, generated.text
    assert "pending_evidence_review" in generated.json()["facts_snapshot"]["unknown_facts"]

    unfounded = client.post(
        f"/api/v1/reply-drafts/{generated.json()['id']}/edit",
        headers=headers("support", "demo-a"),
        json={"content": "您好，您的退款已到账，退款成功。"},
    )
    assert unfounded.status_code == 200
    assert unfounded.json()["validation"]["valid"] is False
    blocked = client.post(
        f"/api/v1/reply-drafts/{unfounded.json()['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": unfounded.json()["facts_snapshot_hash"]},
    )
    assert blocked.status_code == 409
    assert "无事实依据" in blocked.json()["detail"]


def test_permission_boundaries_across_new_modules(return_case):
    client, app, headers, body, policy = return_case
    request = draft(return_case)

    with app.state.database.sessions() as session:
        sku = session.get(OrderItem, body["item_id"]).sku
    checked = client.post(
        "/api/v1/policies/conflicts/check",
        headers=headers("admin", "demo-a"),
        json={"rules": conflict_rules(sku)},
    ).json()
    conflict_id = checked["conflicts"][0]["id"]

    assert client.post(
        f"/api/v1/policies/conflicts/{conflict_id}/resolve",
        headers=headers("support", "demo-a"),
        json={"decision": "no_conflict", "note": "无权裁决"},
    ).status_code == 403
    assert client.get(
        f"/api/v1/policies/conflicts/{conflict_id}", headers=headers("support", "demo-b")
    ).status_code == 404

    assert upload(
        client, headers, request["id"], "photo.png", PNG, "image/png", "product_photo"
    ).status_code == 201
    assert client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", PNG, "image/png")},
        data={"kind": "product_photo", "return_request_id": request["id"]},
        headers=headers("approver", "demo-a"),
    ).status_code == 403

    generated = client.post(
        "/api/v1/reply-drafts",
        headers=headers("support", "demo-a"),
        json={"return_request_id": request["id"]},
    ).json()
    assert client.post(
        "/api/v1/reply-drafts",
        headers=headers("approver", "demo-a"),
        json={"return_request_id": request["id"]},
    ).status_code == 403
    assert client.get(
        f"/api/v1/reply-drafts/{generated['id']}", headers=headers("support", "demo-b")
    ).status_code == 404