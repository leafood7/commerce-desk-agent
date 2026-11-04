# ruff: noqa: F811
"""PR-03: source and consistency validation layers, plus blocked edited drafts."""

from test_reply_drafts import accepted_case, generate  # noqa: F401
from test_returns import return_case  # noqa: F401

from workbench.reply_drafts import DraftContext, generate_reply_draft
from workbench.reply_generation import ReplyPlan
from workbench.reply_validation import validate_draft, validate_reply_plan


def facts_base(**overrides):
    values = {
        "order_number": "A1003",
        "return_status": "accepted_simulated",
        "verified_evidence": [],
        "unresolved_conflicts": 0,
        "actual_refund_status": "not_confirmed",
    }
    values.update(overrides)
    return values


def test_source_layer_rejects_unapproved_fact_ids():
    context = {"allowed_claims": {"order_number": "订单 A1003"}}
    valid = validate_reply_plan(
        ReplyPlan(tone="neutral", selected_fact_ids=["order_number"]), context
    )
    assert valid["valid"] is True
    invalid = validate_reply_plan(
        ReplyPlan(tone="neutral", selected_fact_ids=["refund_success"]), context
    )
    assert invalid["valid"] is False
    assert invalid["issues"][0]["layer"] == "source"


def test_consistency_rejects_other_order_number():
    result = validate_draft("订单 A9999 的退货申请已通过审核。", facts_base())
    assert result["valid"] is False
    assert any(issue["claim"] == "A9999" for issue in result["issues"])
    assert any(issue["layer"] == "consistency" for issue in result["issues"])


def test_consistency_rejects_status_mismatch():
    result = validate_draft(
        "您的退货申请已通过审核。", facts_base(return_status="pending_approval")
    )
    assert result["valid"] is False
    assert any(issue["reason"] == "声称的退货状态与事实不一致" for issue in result["issues"])


def test_consistency_rejects_unverified_evidence_claim():
    result = validate_draft("相关凭证已核验。", facts_base())
    assert result["valid"] is False
    assert any(issue["reason"] == "没有已核验凭证" for issue in result["issues"])


def test_consistency_rejects_resolved_conflict_claim():
    result = validate_draft("政策冲突已解决。", facts_base(unresolved_conflicts=1))
    assert result["valid"] is False
    assert any(issue["reason"] == "仍有未裁决的政策冲突" for issue in result["issues"])


def test_consistency_rejects_amount_claim():
    result = validate_draft("退款金额为 199 元。", facts_base())
    assert result["valid"] is False
    assert any(issue["reason"] == "退款金额尚未核实" for issue in result["issues"])


def test_templates_pass_consistency_layer():
    for status in (
        "pending_approval",
        "approved",
        "accepted_simulated",
        "rejected",
        "cancelled",
    ):
        content = generate_reply_draft(DraftContext("A1003", status, None, []))["content"]
        assert validate_draft(content, facts_base(return_status=status))["valid"] is True


def test_edited_consistency_violation_blocks_confirmation(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    generated = generate(client, headers, request["id"]).json()
    edited = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/edit",
        headers=headers("support", "demo-a"),
        json={"content": "订单 A9999 的退货申请已通过审核。"},
    )
    assert edited.status_code == 200
    assert edited.json()["validation"]["valid"] is False
    assert edited.json()["validation_status"] == "failed"
    blocked = client.post(
        f"/api/v1/reply-drafts/{edited.json()['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": edited.json()["facts_snapshot_hash"]},
    )
    assert blocked.status_code == 409
    assert "无事实依据" in blocked.json()["detail"]
    detail = client.get(
        f"/api/v1/reply-drafts/{edited.json()['id']}", headers=headers("support", "demo-a")
    ).json()
    assert detail["status"] == "edited"
    assert detail["validation_status"] == "failed"