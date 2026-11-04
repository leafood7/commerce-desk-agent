# ruff: noqa: F811
"""PR-02: controlled model composition over allowed fact phrases, template fallback."""

import json

import httpx
from pydantic import SecretStr
from test_reply_drafts import accepted_case, generate  # noqa: F401
from test_returns import return_case  # noqa: F401

from workbench.config import WorkbenchSettings
from workbench.reply_generation import generate_reply


def enable_llm(settings):
    settings.reply_llm_enabled = True
    settings.reply_llm_url = "https://example.test/chat"
    settings.agent_router_url = "https://example.test/chat"
    settings.agent_router_model = "deepseek-v4-pro"
    settings.agent_router_key = SecretStr("test-key")


def install(monkeypatch, plan=None, error=None):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if error is not None:
            raise error
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(plan, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("workbench.reply_generation.httpx.post", fake_post)
    return calls


def test_llm_plan_composes_only_allowed_facts(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    enable_llm(app.state.settings)
    calls = install(
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
    body = generate(client, headers, request["id"], mode="llm").json()
    assert calls
    assert body["generation"]["mode"] == "llm"
    assert body["generation"]["fallback_used"] is False
    assert body["generation"]["model"] == "deepseek-v4-pro"
    assert body["generation"]["prompt_version"] == "reply-compose-v1"
    assert body["generation"]["usage"]["total_tokens"] == 15
    assert body["generation_mode"] == "llm"
    assert body["model_name"] == "deepseek-v4-pro"
    assert body["prompt_version"] == "reply-compose-v1"
    assert body["fallback_used"] is False
    assert body["validation_status"] == "passed"
    assert "订单 A1003" in body["content"]
    assert "系统模拟受理" in body["content"]
    assert "尚未确认实际退款结果" in body["content"]
    assert "退款成功" not in body["content"]
    assert body["validation"]["valid"] is True
    assert body["requires_confirmation"] is True


def test_unauthorized_fact_id_falls_back_to_template(return_case, monkeypatch):
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
    body = generate(client, headers, request["id"], mode="llm").json()
    assert body["generation"]["mode"] == "template"
    assert body["generation"]["fallback_used"] is True
    assert body["generation"]["fallback_reason"] == "引用了未经授权的事实: refund_success"
    assert "系统模拟受理" in body["content"]


def test_timeout_falls_back_to_template(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    enable_llm(app.state.settings)
    install(monkeypatch, error=httpx.TimeoutException("timeout"))
    body = generate(client, headers, request["id"], mode="llm").json()
    assert body["generation"]["mode"] == "template"
    assert body["generation"]["fallback_used"] is True
    assert body["generation"]["fallback_reason"] == "timeout"


def test_invalid_schema_falls_back_to_template(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    enable_llm(app.state.settings)
    install(monkeypatch, plan={"content": "自由正文"})
    body = generate(client, headers, request["id"], mode="llm").json()
    assert body["generation"]["mode"] == "template"
    assert body["generation"]["fallback_used"] is True
    assert body["generation"]["fallback_reason"] == "schema_invalid"


def test_switch_off_never_calls_model(return_case, monkeypatch):
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    app.state.settings.reply_llm_enabled = False
    calls = install(
        monkeypatch,
        plan={
            "tone": "neutral",
            "selected_fact_ids": ["order_number"],
            "opening_style": "neutral",
            "closing_style": "none",
        },
    )
    body = generate(client, headers, request["id"], mode="template").json()
    assert calls == []
    assert body["generation"]["mode"] == "template"
    assert body["generation"]["fallback_used"] is False


def test_composed_text_still_blocked_by_validation(monkeypatch):
    settings = WorkbenchSettings(
        agent_router_mode="model",
        agent_router_url="https://example.test/chat",
        agent_router_model="deepseek-v4-pro",
        agent_router_key=SecretStr("test-key"),
        reply_llm_enabled=True,
        reply_llm_url="https://example.test/chat",
    )
    context = {
        "order_number": "A1003",
        "return_status": "accepted_simulated",
        "refund_status": "UNKNOWN",
        "evidence_refs": [],
        "allowed_claims": {"bad": "您的退款将按原支付方式退回。"},
        "unknown_facts": [],
        "prohibited_claims": [],
    }
    install(
        monkeypatch,
        plan={
            "tone": "neutral",
            "selected_fact_ids": ["bad"],
            "opening_style": "neutral",
            "closing_style": "none",
        },
    )
    result = generate_reply(context, settings)
    assert result["generation"]["mode"] == "template"
    assert result["generation"]["fallback_used"] is True
    assert result["generation"]["fallback_reason"] == "Composed text failed validation"