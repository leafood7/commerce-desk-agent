# ruff: noqa: F811
import asyncio

from sqlalchemy import select
from test_returns import confirm, draft, return_case  # noqa: F401
from test_scoped_mcp import grant, scoped_mcp  # noqa: F401

from workbench.models import Order
from workbench.reply_draft_models import ReplyDraft
from workbench.reply_drafts import DraftContext, _hash, generate_reply_draft
from workbench.reply_validation import validate_draft


def accepted_case(case):
    client, app, headers, _, _ = case
    row = draft(case)
    assert confirm(case, row).json()["status"] == "pending_approval"
    decision = client.post(
        f"/api/v1/returns/requests/{row['id']}/decision",
        headers=headers("approver"),
        json={"action": "approve", "reason": "demo evidence", "expected_revision": 1},
    )
    assert decision.status_code == 200, decision.text
    submitted = client.post(
        f"/api/v1/returns/requests/{row['id']}/submit-simulated",
        headers=headers(),
        json={"expected_revision": 1},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "accepted_simulated"
    return submitted.json()


def generate(client, headers, request_id, **kwargs):
    return client.post(
        "/api/v1/reply-drafts",
        headers=headers("support", "demo-a"),
        json={"return_request_id": request_id, **kwargs},
    )


def test_generate_pending_approval_template(return_case):
    client, app, headers, _, _ = return_case
    row = draft(return_case)
    assert confirm(return_case, row).json()["status"] == "pending_approval"
    response = generate(client, headers, row["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "draft" and body["version"] == 1
    assert "等待审核" in body["content"]
    assert "退款成功" not in body["content"]
    assert body["facts_snapshot"]["return_status"] == "pending_approval"
    assert body["facts_snapshot"]["actual_refund_status"] == "not_confirmed"
    assert body["facts_snapshot"]["actual_refund_executed"] is False
    assert body["validation"]["valid"] is True
    assert any(fact["source"] == "order" for fact in body["facts_snapshot"]["verified_facts"])
    assert body["requires_confirmation"] is True


def test_generate_accepted_simulated_never_claims_refund(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    response = generate(client, headers, request["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert "系统模拟受理" in body["content"]
    assert "不代表实际退款已经完成" in body["content"]
    assert "退款成功" not in body["content"]
    assert "已到账" not in body["content"]
    assert body["facts_snapshot"]["return_status"] == "accepted_simulated"
    assert body["facts_snapshot"]["refund_status"] is None
    assert "refund_result" in body["facts_snapshot"]["unknown_facts"]
    assert "actual_refund_status" in body["facts_snapshot"]["unknown_facts"]
    assert body["facts_snapshot"]["prohibited_claims"] == [
        "refund_success",
        "refund_arrival_date",
        "refund_arrival_promised",
        "refund_payment_method_confirmed",
        "return_completed",
    ]


def test_rule_template_covers_states_and_refund_results():
    pending = generate_reply_draft(DraftContext("A1001", "approved", None, []))
    assert "已通过审核" in pending["content"]
    assert "仍需进一步确认" in pending["content"]

    succeeded = generate_reply_draft(DraftContext("A1001", "accepted_simulated", "SUCCEEDED", []))
    assert "模拟退款流程已成功完成" in succeeded["content"]
    assert "实际到账结果尚未确认" in succeeded["content"]

    unknown = generate_reply_draft(DraftContext("A1001", "accepted_simulated", "UNKNOWN", []))
    assert "退款结果暂未确认" in unknown["content"]

    rejected = generate_reply_draft(DraftContext("A1001", "rejected", None, []))
    assert "未通过审核" in rejected["content"]

    fallback = generate_reply_draft(DraftContext("A1001", "draft", None, []))
    assert "仍需进一步核实" in fallback["content"]


def test_validation_blocks_unfounded_claims():
    facts = {"actual_refund_status": "not_confirmed"}
    bad = validate_draft("您好，您的退款已到账，退款成功。", facts)
    assert bad["valid"] is False
    assert any("已到账" in issue["claim"] for issue in bad["issues"])
    promised = validate_draft("我们保证退款，3天内到账。", facts)
    assert promised["valid"] is False
    good = validate_draft("已完成系统模拟受理，这不代表实际退款已经完成。", facts)
    assert good["valid"] is True


def test_soft_refund_commitments_are_blocked():
    facts = {"actual_refund_status": "not_confirmed"}
    model_output = (
        "您好，我们已收到您的退货申请，正在为您处理。"
        "退款将在退货审核完成后按原支付方式退回，"
        "具体到账时间请以银行或支付平台通知为准。"
    )
    result = validate_draft(model_output, facts)
    assert result["valid"] is False
    assert any(
        "退款流程" in issue["reason"] or "退回路径" in issue["reason"]
        for issue in result["issues"]
    )

    assert validate_draft("退款未执行成功，需要人工跟进处理。", facts)["valid"] is True
    assert (
        validate_draft(
            generate_reply_draft(DraftContext("A1003", "accepted_simulated", "UNKNOWN", []))["content"],
            facts,
        )["valid"]
        is True
    )


def test_reply_refund_unknown_001_soft_commitment_blocked_with_safe_template():
    facts = {
        "actual_refund_status": "not_confirmed",
        "unknown_facts": [
            "actual_refund_status",
            "refund_result",
            "refund_payment_method",
            "refund_arrival_date",
        ],
    }
    model_output = "您的退款将按原支付方式退回，请耐心等待。"
    assert validate_draft(model_output, facts)["valid"] is False
    fallback = generate_reply_draft(DraftContext("A1003", "accepted_simulated", "UNKNOWN", []))
    assert validate_draft(fallback["content"], facts)["valid"] is True


def test_reply_context_pr01_fields(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    facts = generated["facts_snapshot"]
    assert facts["snapshot_version"] == 2
    assert facts["customer_intent"] == "query_return_status"
    assert facts["intent_source"] == "static_default"
    assert facts["verified_facts_map"]["order_number"]["verified"] is True
    assert facts["allowed_fact_ids"]
    assert facts["allowed_claims"]["order_number"].startswith("订单 ")
    assert "return_status_accepted_simulated" in facts["allowed_claims"]
    assert "refund_status_unknown" in facts["allowed_claims"]
    assert facts["policy_evidence"]
    assert facts["policy_evidence"][0]["version_id"]
    assert facts["snapshot"]["policy_version_ids"]
    assert facts["snapshot"]["evidence_states"] == []
    assert "refund_payment_method" in facts["unknown_facts"]
    assert "refund_arrival_date" in facts["unknown_facts"]
    assert "refund_payment_method_confirmed" in facts["prohibited_claims"]


def test_legacy_snapshot_v1_draft_still_confirms(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    legacy_keys = [
        "return_status",
        "return_revision",
        "receipt_id",
        "decision_reason",
        "order_number",
        "payment_status",
        "shipping_status",
        "fulfillment_status",
        "refund_status",
        "refund_simulated_outcome",
        "verified_evidence_ids",
        "pending_evidence_count",
        "policy_evidence_refs",
        "unresolved_conflicts",
    ]
    with app.state.database.sessions() as session:
        row = session.get(ReplyDraft, generated["id"])
        old_snapshot = {key: row.facts_snapshot["snapshot"][key] for key in legacy_keys}
        legacy_facts = {
            key: value
            for key, value in row.facts_snapshot.items()
            if key
            not in {
                "snapshot_version",
                "policy_evidence",
                "allowed_claims",
                "allowed_fact_ids",
                "verified_facts_map",
                "customer_intent",
                "intent_source",
            }
        }
        legacy_facts["snapshot"] = old_snapshot
        row.facts_snapshot = legacy_facts
        row.facts_snapshot_hash = _hash(old_snapshot)
        row.status = "draft"
        session.commit()
        legacy_hash = row.facts_snapshot_hash

    confirmed = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": legacy_hash},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"


def test_edit_creates_new_version_and_confirm_records_actor(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()

    edited = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/edit",
        headers=headers("support", "demo-a"),
        json={
            "content": "您好，订单 A1003 的退货申请已完成系统模拟受理，实际退款结果尚未确认，我们会继续跟进。"
        },
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["status"] == "edited" and body["version"] == 2
    assert body["return_request_id"] == request["id"]

    confirmed = client.post(
        f"/api/v1/reply-drafts/{body['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": body["facts_snapshot_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["status"] == "confirmed"
    assert result["confirmed_by"]
    assert result["requires_confirmation"] is False

    history = client.get(
        f"/api/v1/reply-drafts?return_request_id={request['id']}",
        headers=headers("support", "demo-a"),
    )
    assert history.status_code == 200
    versions = sorted(item["version"] for item in history.json()["items"])
    assert versions == [1, 2]


def test_stale_facts_block_confirmation(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()

    with app.state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == "A1003"))
        order.payment_status = "refunded"
        session.commit()

    stale = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": generated["facts_snapshot_hash"]},
    )
    assert stale.status_code == 409
    assert "业务状态已更新" in stale.json()["detail"]


def test_unfounded_edit_cannot_confirm(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    edited = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/edit",
        headers=headers("support", "demo-a"),
        json={"content": "您好，您的退款已到账，退款成功。"},
    )
    assert edited.status_code == 200
    assert edited.json()["validation"]["valid"] is False
    blocked = client.post(
        f"/api/v1/reply-drafts/{edited.json()['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": edited.json()["facts_snapshot_hash"]},
    )
    assert blocked.status_code == 409
    assert "无事实依据" in blocked.json()["detail"]


def test_confirm_rejects_wrong_hash(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    wrong = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": "0" * 64},
    )
    assert wrong.status_code == 409


def test_cross_organization_and_roles(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()

    assert client.get(
        f"/api/v1/reply-drafts/{generated['id']}", headers=headers("support", "demo-b")
    ).status_code == 404
    assert (
        client.post(
            "/api/v1/reply-drafts",
            headers=headers("approver", "demo-a"),
            json={"return_request_id": request["id"]},
        ).status_code
        == 403
    )
    assert client.get(
        f"/api/v1/reply-drafts/{generated['id']}", headers=headers("approver", "demo-a")
    ).status_code == 200


def test_pending_evidence_becomes_unknown_fact(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    upload = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"e" * 8, "image/png")},
        data={"kind": "product_photo", "return_request_id": request["id"]},
        headers=headers("support", "demo-a"),
    )
    assert upload.status_code == 201, upload.text
    generated = generate(client, headers, request["id"]).json()
    facts = generated["facts_snapshot"]
    assert facts["pending_evidence_count"] == 1
    assert facts["verified_evidence"] == []
    assert "pending_evidence_review" in facts["unknown_facts"]


def test_reply_draft_audit_fields(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    assert generated["generation_mode"] == "template"
    assert generated["validation_status"] == "passed"
    assert generated["edited_by_human"] is False
    assert generated["fallback_used"] is False
    assert generated["confirmed_at"] is None

    edited = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/edit",
        headers=headers("support", "demo-a"),
        json={"content": generated["content"]},
    ).json()
    assert edited["generation_mode"] == "human_edit"
    assert edited["edited_by_human"] is True
    assert edited["validation_status"] == "passed"

    confirmed = client.post(
        f"/api/v1/reply-drafts/{edited['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": edited["facts_snapshot_hash"]},
    ).json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"]
    assert confirmed["validation_status"] == "passed"


def test_failed_confirmation_records_validation_errors(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    edited = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/edit",
        headers=headers("support", "demo-a"),
        json={"content": "订单 A9999 的退款已到账。"},
    ).json()
    assert edited["validation_status"] == "failed"
    assert edited["validation_errors"]
    blocked = client.post(
        f"/api/v1/reply-drafts/{edited['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": edited["facts_snapshot_hash"]},
    )
    assert blocked.status_code == 409
    detail = client.get(
        f"/api/v1/reply-drafts/{edited['id']}", headers=headers("support", "demo-a")
    ).json()
    assert detail["status"] == "edited"
    assert detail["validation_status"] == "failed"
    assert detail["validation_errors"]


def test_mcp_verified_case_facts(scoped_mcp, return_case):
    request = accepted_case(return_case)
    client, app, headers, gateway, _ = scoped_mcp
    token = grant(client, headers("support"))
    data = asyncio.run(
        gateway.call("get_verified_case_facts", {"return_request_id": request["id"]}, token)
    )
    assert data["status"] == "found"
    facts = data["facts"]
    assert facts["return_status"] == "accepted_simulated"
    assert facts["actual_refund_status"] == "not_confirmed"
    assert facts["actual_refund_executed"] is False
    assert facts["verified_facts"]
    assert "actual_refund_status" in facts["unknown_facts"]
    assert "prohibited_claims" in facts