# ruff: noqa: F811
"""PR-05: four core controlled-reply scenarios."""

from test_reply_drafts import accepted_case, generate  # noqa: F401
from test_reply_generation import enable_llm, install
from test_returns import return_case  # noqa: F401

from workbench.reply_drafts import DraftContext, generate_reply_draft
from workbench.reply_validation import validate_draft

PNG = b"\x89PNG\r\n\x1a\n" + b"s" * 16


def test_scenario_normal_model_generation_and_confirm(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    enable_llm(app.state.settings)
    install(
        monkeypatch,
        plan={
            "tone": "friendly",
            "selected_fact_ids": [
                "order_number",
                "return_status_accepted_simulated",
                "refund_status_unknown",
            ],
            "opening_style": "friendly",
            "closing_style": "neutral",
        },
    )
    generated = generate(client, headers, request["id"], mode="llm").json()
    assert generated["generation_mode"] == "llm"
    assert generated["validation_status"] == "passed"
    assert generated["fallback_used"] is False
    confirmed = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": generated["facts_snapshot_hash"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


def test_scenario_insufficient_information_does_not_invent(return_case):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    upload = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", PNG, "image/png")},
        data={"kind": "product_photo", "return_request_id": request["id"]},
        headers=headers("support", "demo-a"),
    )
    assert upload.status_code == 201, upload.text
    generated = generate(client, headers, request["id"]).json()
    facts = generated["facts_snapshot"]
    assert facts["pending_evidence_count"] == 1
    assert "pending_evidence_review" in facts["unknown_facts"]
    assert "系统模拟受理" in generated["content"]
    assert "已核验" not in generated["content"]
    assert generated["validation_status"] == "passed"


def test_scenario_refund_unknown_never_promised():
    facts = {
        "actual_refund_status": "not_confirmed",
        "unknown_facts": [
            "actual_refund_status",
            "refund_payment_method",
            "refund_arrival_date",
        ],
    }
    assert validate_draft("您的退款将按原支付方式退回，请耐心等待。", facts)["valid"] is False
    template = generate_reply_draft(DraftContext("A1003", "accepted_simulated", "UNKNOWN", []))
    assert validate_draft(template["content"], facts)["valid"] is True


def test_scenario_model_out_of_bounds_falls_back(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    enable_llm(app.state.settings)
    install(
        monkeypatch,
        plan={
            "tone": "neutral",
            "selected_fact_ids": ["refund_success"],
            "opening_style": "neutral",
            "closing_style": "none",
        },
    )
    generated = generate(client, headers, request["id"], mode="llm").json()
    assert generated["generation_mode"] == "template"
    assert generated["fallback_used"] is True
    assert generated["fallback_reason"].startswith("引用了未经授权的事实")
    assert generated["validation_status"] == "passed"
    confirmed = client.post(
        f"/api/v1/reply-drafts/{generated['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": generated["facts_snapshot_hash"]},
    )
    assert confirmed.status_code == 200


def test_scenario_llm_requested_but_server_disabled(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    calls = install(
        monkeypatch,
        plan={
            "tone": "neutral",
            "selected_fact_ids": ["order_number"],
            "opening_style": "neutral",
            "closing_style": "none",
        },
    )
    generated = generate(client, headers, request["id"], mode="llm").json()
    assert calls == []
    assert generated["generation_mode"] == "template"
    assert generated["fallback_used"] is True
    assert generated["fallback_reason"] == "server_disabled"
    assert generated["validation_status"] == "passed"