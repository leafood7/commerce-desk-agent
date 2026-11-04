"""Optional controlled LLM composition over administrator-approved fact phrases.

The model only selects allowed fact blocks and tone; the backend composes the
final text from fixed phrases. Invalid output, timeout or error falls back to
the existing rule template and the fallback is recorded.
"""

import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from workbench.reply_validation import validate_draft, validate_reply_plan

PROMPT_VERSION = "reply-compose-v1"
OPENINGS = {
    "friendly": "您好，感谢您的耐心等待。",
    "neutral": "您好。",
    "formal": "尊敬的客户，您好。",
}
CLOSINGS = {
    "neutral": "如有其他问题，请随时联系我们。",
    "grateful": "感谢您的理解与支持。",
    "none": "",
}


class ReplyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tone: Literal["friendly", "neutral", "formal"] = "neutral"
    selected_fact_ids: list[str] = Field(min_length=1, max_length=8)
    opening_style: Literal["friendly", "neutral", "formal"] = "neutral"
    closing_style: Literal["neutral", "grateful", "none"] = "neutral"


def _messages(context):
    payload = {
        "customer_intent": context.get("customer_intent"),
        "return_status": context.get("return_status"),
        "refund_status": context.get("refund_status"),
        "unknown_facts": context.get("unknown_facts", []),
        "allowed_claims": context.get("allowed_claims", {}),
        "prohibited_claims": context.get("prohibited_claims", []),
    }
    system = (
        "你是电商售后客服回复助手。根据系统提供的已核实事实组织客服回复。\n"
        "严格遵守：\n"
        "1. 不得补充输入中不存在的订单事实。\n"
        "2. 不得推测退款是否完成。\n"
        "3. 不得推测退款方式或到账时间。\n"
        "4. 不得自行承诺退款、赔偿或办理时限。\n"
        "5. 不得将待核验凭证描述为已核实凭证。\n"
        "6. 只能选择 allowed_claims 中存在的事实 ID。\n"
        "7. 不确定的信息必须保持不确定。\n"
        "8. 不得调用业务工具或执行任何业务操作。\n"
        "只返回符合指定 Schema 的结构化结果，不要输出正文。以 json 对象返回。\n"
        "Schema: "
        + json.dumps(ReplyPlan.model_json_schema(), ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _call(settings, context):
    url = settings.reply_llm_url or settings.agent_router_url
    request = {
        "model": settings.agent_router_model,
        "temperature": 0,
        "max_tokens": 256,
        "messages": _messages(context),
    }
    if (settings.agent_router_model or "").startswith("deepseek-"):
        request["thinking"] = {"type": "disabled"}
    response = httpx.post(
        url,
        headers={"Authorization": "Bearer " + settings.agent_router_key.get_secret_value()},
        json=request,
        timeout=12,
        follow_redirects=False,
        trust_env=False,
    )
    response.raise_for_status()
    if len(response.content) > 65536:
        raise ValueError("Model response too large")
    payload = response.json()
    content = payload["choices"][0]["message"].get("content") or ""
    if not isinstance(content, str):
        raise ValueError("Model content is not text")
    plan = ReplyPlan.model_validate(json.loads(content))
    usage = payload.get("usage") or {}
    return plan, {
        key: usage.get(key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if usage.get(key) is not None
    }


def _compose(plan, context):
    source = validate_reply_plan(plan, context)
    if not source["valid"]:
        issue = source["issues"][0]
        raise ValueError(issue["reason"] + ": " + issue["claim"])
    allowed = context.get("allowed_claims") or {}
    body = " ".join(allowed[fact] for fact in plan.selected_fact_ids)
    parts = [OPENINGS[plan.opening_style], body, CLOSINGS[plan.closing_style]]
    return " ".join(part for part in parts if part)


def _fallback_reason(exc):
    if isinstance(exc, ValidationError):
        return "schema_invalid"
    message = str(exc).strip()
    return (message or type(exc).__name__)[:200]


def generate_reply(context, settings, mode=None):
    from workbench.reply_drafts import DraftContext, generate_reply_draft

    template = generate_reply_draft(
        DraftContext(
            order_number=context["order_number"],
            return_status=context["return_status"],
            refund_status=context.get("refund_status"),
            evidence_refs=context.get("evidence_refs", []),
            fulfillment_status=context.get("fulfillment_status"),
        )
    )
    generation = {
        "mode": "template",
        "model": None,
        "prompt_version": None,
        "fallback_used": False,
        "fallback_reason": None,
        "usage": None,
    }
    if mode == "llm" and not settings.reply_llm_enabled:
        generation["fallback_used"] = True
        generation["fallback_reason"] = "server_disabled"
        return {**template, "generation": generation}
    if mode == "template" or not settings.reply_llm_enabled:
        return {**template, "generation": generation}
    usage = None
    try:
        plan, usage = _call(settings, context)
        content = _compose(plan, context)
        validation = validate_draft(content, context)
        if not validation["valid"]:
            raise ValueError("Composed text failed validation")
        return {
            "content": content,
            "status": "draft",
            "evidence_refs": context.get("evidence_refs", []),
            "requires_confirmation": True,
            "generation": {
                "mode": "llm",
                "model": settings.agent_router_model,
                "prompt_version": PROMPT_VERSION,
                "fallback_used": False,
                "fallback_reason": None,
                "usage": usage,
            },
        }
    except Exception as exc:
        return {
            **template,
            "generation": {
                "mode": "template",
                "model": settings.agent_router_model,
                "prompt_version": PROMPT_VERSION,
                "fallback_used": True,
                "fallback_reason": _fallback_reason(exc),
                "usage": usage,
            },
        }