# ruff: noqa: F811
"""Opt-in real-model end-to-end run; skipped unless a key is explicitly provided."""

import json
import os
from pathlib import Path

import pytest
from pydantic import SecretStr
from test_reply_drafts import accepted_case  # noqa: F401
from test_returns import return_case  # noqa: F401


def test_real_model_reply_end_to_end(return_case):
    key = os.environ.get("WORKBENCH_REAL_REPLY_KEY")
    if not key:
        pytest.skip("real reply model key is not authorized for this run")
    request = accepted_case(return_case)
    client, app, headers, _, _ = return_case
    settings = app.state.settings
    settings.reply_llm_enabled = True
    settings.reply_llm_url = "https://api.deepseek.com/chat/completions"
    settings.agent_router_url = "https://api.deepseek.com/chat/completions"
    settings.agent_router_model = "deepseek-v4-pro"
    settings.agent_router_key = SecretStr(key)

    generated = client.post(
        "/api/v1/reply-drafts",
        headers=headers("support", "demo-a"),
        json={"return_request_id": request["id"], "mode": "llm"},
    )
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["generation_mode"] in {"llm", "template"}
    assert body["validation_status"] == "passed"
    assert "退款成功" not in body["content"]
    assert "已到账" not in body["content"]
    assert "按原支付方式退回" not in body["content"]
    assert "原路退回" not in body["content"]
    if body["generation_mode"] == "llm":
        assert body["model_name"] == "deepseek-v4-pro"
        assert body["prompt_version"] == "reply-compose-v1"
        assert body["fallback_used"] is False
        assert body["fallback_reason"] is None
    else:
        assert body["fallback_used"] is True
        assert body["fallback_reason"]

    confirmed = client.post(
        f"/api/v1/reply-drafts/{body['id']}/confirm",
        headers=headers("support", "demo-a"),
        json={"expected_facts_snapshot_hash": body["facts_snapshot_hash"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    usage = (body.get("generation") or {}).get("usage") or {}
    cost = round(
        (
            (usage.get("prompt_tokens") or 0) * 2
            + (usage.get("completion_tokens") or 0) * 8
        )
        / 1_000_000,
        6,
    )
    record = {
        "generation_mode": body["generation_mode"],
        "model_name": body["model_name"],
        "prompt_version": body["prompt_version"],
        "fallback_used": body["fallback_used"],
        "fallback_reason": body["fallback_reason"],
        "validation_status": body["validation_status"],
        "usage": usage,
        "cost_cny": cost,
        "content_prefix": body["content"][:160],
        "confirmed": True,
    }
    path = Path("work/pr8/real-reply-e2e.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PR8_REAL_REPLY " + json.dumps(record, ensure_ascii=False))