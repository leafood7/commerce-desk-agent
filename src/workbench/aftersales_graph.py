"""Bounded LangGraph orchestration. Tools never receive control from retrieved text."""

import re
from datetime import UTC
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from business.shipping import eligibility
from workbench.models import now
from workbench.tool_plans import validate_read_plan


class AfterSalesState(TypedDict, total=False):
    query: str
    order_number: str | None
    shipping_kind: str | None
    reason: str | None
    intent: str
    planner_mode: str
    read_plan: dict | None
    tool_plan_mode: str
    order: dict
    evidence: list
    retrieval: dict
    request: dict
    proposal: dict
    confirmed: bool
    answer: str
    terminal: str
    next_step: str
    turns: int
    corrections: list
    warnings: list
    audit_status: str


def classify(query):
    if any(word in query for word in ("政策", "规则", "条件", "权益", "多少", "几分钟")):
        return "knowledge"
    if any(word in query for word in ("退款", "退货", "赔偿", "补偿", "改地址", "取消订单")):
        return "unsupported"
    if any(word in query for word in ("催单", "催发货", "催一下", "加急", "催一催")):
        return "shipping"
    if any(word in query for word in ("订单", "物流", "签收", "到货")):
        return "lookup"
    return "unsupported"


def signature(order):
    return {
        key: order.get(key)
        for key in ("number", "payment_status", "shipping_status", "promised_ship_at")
    }


def build_graph(saver, tools, validate, planner=None, *, profile=None):
    def route(state):
        number = state.get("order_number")
        if not number:
            found = re.findall(
                r"(?<![A-Za-z0-9_-])([A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)(?![A-Za-z0-9_-])",
                state["query"],
            )
            if len(set(found)) == 1:
                number = found[0].upper()
        plan = (
            planner(state["query"])
            if planner
            else {"intent": classify(state["query"]), "mode": "rules"}
        )
        return {
            "intent": plan["intent"],
            "planner_mode": plan["mode"],
            "read_plan": plan.get("read_plan"),
            "order_number": number,
            "turns": 0,
            "corrections": [],
            "warnings": [],
            "confirmed": False,
        }

    def gather(state):
        fields = (
            ["order_number"]
            if state["intent"] == "lookup"
            else ["order_number", "shipping_kind", "reason"]
        )
        missing = [name for name in fields if not state.get(name)]
        if not missing:
            return {"next_step": "read_plan"}
        if state.get("turns", 0) >= 20:
            return {"answer": "补充次数已达本次上限，请新建会话。", "next_step": "finish"}
        response = interrupt(
            {
                "status": "awaiting_input",
                "answer": "请补充明确的订单号、申请类型或原因。",
                "missing": missing,
                "known": {key: state.get(key) for key in fields},
            }
        )
        changes = {key: response[key] for key in fields if response.get(key) is not None}
        return {**changes, "turns": state.get("turns", 0) + 1, "next_step": "gather"}

    def observe(state):
        result = tools("get_order_status", {"order_number": state["order_number"]})
        return {"order": result["order"], "audit_status": result.get("audit_status", "recorded")}

    def lookup_result(state):
        order = state["order"]
        return {
            "answer": f"订单 {order['number']}：{order['payment_status']}，{order['shipping_status']}。"
            + (
                f"预计信息：{order['estimated_delivery']}。"
                if order.get("estimated_delivery")
                else ""
            )
            + (
                f"实际签收时间：{order['signed_at']}。"
                if order.get("signed_at")
                else "实际签收时间未提供。"
            )
        }

    def retrieve(state):
        query = (
            state["query"]
            if state["intent"] == "knowledge"
            else "普通催单与加急申请的条件、承诺发货时间及工具能力边界"
        )
        result = tools(
            "search_knowledge_base",
            {
                "query": query,
                "policy_scope": "shipping" if state["intent"] == "shipping" else None,
                "strategy": "hybrid_rerank",
                "top_k": (profile or {}).get("top_k", 3),
            },
        )
        matches = validate(result["matches"])
        return {"evidence": matches, "retrieval": result.get("trace", {})}

    def read_plan(state):
        defaults = {
            "shipping": ["get_order_status", "search_knowledge_base"],
            "lookup": ["get_order_status"],
            "knowledge": ["search_knowledge_base"],
        }
        proposed = state.get("read_plan")
        mode = "fixed"
        names = defaults[state["intent"]]
        if proposed:
            try:
                names = validate_read_plan(proposed, state["intent"], state)
                mode = "model_validated"
            except ValueError:
                mode = "rejected_fallback"
        local = dict(state)
        result = {"tool_plan_mode": mode}
        for name in names:
            delta = observe(local) if name == "get_order_status" else retrieve(local)
            local.update(delta)
            result.update(delta)
        return result

    def knowledge_result(state):
        return {
            "answer": "以下是当前已发布的政策原文，请结合适用条件核对；相关片段不等于已能回答全部问题。"
            if state.get("evidence")
            else "当前已发布资料没有可用依据，无法确认该政策。"
        }

    def propose(state):
        error = eligibility(state["order"], state["shipping_kind"], now().replace(tzinfo=UTC))
        if error:
            return {"answer": error + " 本次未提交申请。", "next_step": "finish"}
        evidence = validate(state.get("evidence", []))
        return {
            "proposal": {
                "order_number": state["order"]["number"],
                "shipping_kind": state["shipping_kind"],
                "reason": state["reason"],
                "order_facts": signature(state["order"]),
                "decision_basis": "server_business_rules",
                "effect": "登记普通催单，不通知外部仓库。"
                if state["shipping_kind"] == "normal"
                else "登记待审批加急申请，审批前不创建工单，不通知外部仓库。",
            },
            "evidence": evidence,
            "confirmed": False,
            "next_step": "confirm",
        }

    def confirmation(state):
        if state.get("turns", 0) >= 20:
            return {"answer": "本次交互次数已达上限，未提交申请。", "next_step": "finish"}
        response = interrupt(
            {
                "status": "awaiting_confirmation",
                "answer": "请核对申请内容。确认只授权登记本次申请，不代表外部仓库已收到通知。",
                "proposal": state["proposal"],
                "order": state["order"],
                "evidence": state.get("evidence", []),
                "warnings": []
                if state.get("evidence")
                else ["未检索到已发布政策；仅按系统业务规则校验，不推断其他权益。"],
            }
        )
        action = response["action"]
        if action == "cancel":
            return {
                "answer": "已取消本次确认，未提交申请。",
                "terminal": "cancelled",
                "next_step": "finish",
            }
        if action == "confirm":
            return {"confirmed": True, "turns": state.get("turns", 0) + 1, "next_step": "recheck"}
        changes = {
            key: response[key]
            for key in ("order_number", "shipping_kind", "reason")
            if response.get(key) is not None
        }
        return {
            **changes,
            "confirmed": False,
            "proposal": {},
            "evidence": [],
            "corrections": state.get("corrections", [])
            + [{"source": "explicit_user_correction", "fields": changes}],
            "turns": state.get("turns", 0) + 1,
            "next_step": "gather",
        }

    def recheck(state):
        if not state.get("confirmed"):
            raise ValueError("Confirmation is required")
        order = tools("get_order_status", {"order_number": state["order_number"]})["order"]
        error = eligibility(order, state["shipping_kind"], now().replace(tzinfo=UTC))
        if error:
            return {
                "order": order,
                "answer": error + " 状态已重新核对，本次未提交申请。",
                "next_step": "finish",
            }
        # Withdrawn/changed evidence cannot silently survive a human pause.
        validate(state.get("evidence", []))
        if signature(order) != state["proposal"]["order_facts"]:
            return {"order": order, "confirmed": False, "next_step": "propose"}
        return {"order": order, "next_step": "submit"}

    def submit(state):
        if not state.get("confirmed"):
            raise ValueError("Missing confirmed proposal")
        result = tools(
            "request_shipping",
            {
                "order_number": state["order_number"],
                "kind": state["shipping_kind"],
                "reason": state["reason"],
            },
        )  # Stable idempotency key is injected by the trusted executor.
        return {
            "request": result["request"],
            "audit_status": result.get("audit_status", "recorded"),
        }

    def wait_approval(state):
        interrupt(
            {
                "status": "awaiting_approval",
                "answer": "加急申请正在等待独立审批；尚未创建加急工单，也未通知外部仓库。",
                "request": state["request"],
            }
        )
        return {}

    def refresh(state):
        result = tools("get_shipping_status", {"request_id": state["request"]["id"]})
        return {
            "request": result["request"],
            "audit_status": result.get("audit_status", "recorded"),
        }

    def result(state):
        receipt = state["request"]
        return {"answer": receipt["result_message"], "terminal": "completed"}

    def unsupported(state):
        return {
            "answer": "当前可查询订单、检索政策、登记普通催单或申请加急。订单行级退货请进入“退货申请”核对商品与政策；该入口仅模拟受理。真实退款和赔偿尚未接入，不能代为执行，请联系人工。"
        }

    graph = StateGraph(AfterSalesState)
    for name, node in (
        ("route", route),
        ("gather", gather),
        ("read_plan", read_plan),
        ("lookup_result", lookup_result),
        ("knowledge_result", knowledge_result),
        ("propose", propose),
        ("confirmation", confirmation),
        ("recheck", recheck),
        ("submit", submit),
        ("wait_approval", wait_approval),
        ("refresh", refresh),
        ("result", result),
        ("unsupported", unsupported),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        lambda s: (
            "gather"
            if s["intent"] in {"shipping", "lookup"}
            else "read_plan"
            if s["intent"] == "knowledge"
            else "unsupported"
        ),
    )
    graph.add_conditional_edges(
        "gather", lambda s: END if s["next_step"] == "finish" else s["next_step"]
    )
    graph.add_conditional_edges(
        "read_plan",
        lambda s: (
            "lookup_result"
            if s["intent"] == "lookup"
            else "knowledge_result"
            if s["intent"] == "knowledge"
            else "propose"
        ),
    )
    graph.add_conditional_edges(
        "propose", lambda s: END if s["next_step"] == "finish" else "confirmation"
    )
    graph.add_conditional_edges(
        "confirmation", lambda s: END if s["next_step"] == "finish" else s["next_step"]
    )
    graph.add_conditional_edges(
        "recheck", lambda s: END if s["next_step"] == "finish" else s["next_step"]
    )
    graph.add_conditional_edges(
        "submit", lambda s: "wait_approval" if s["request"]["status"] == "pending" else "result"
    )
    graph.add_edge("wait_approval", "refresh")
    graph.add_conditional_edges(
        "refresh", lambda s: "wait_approval" if s["request"]["status"] == "pending" else "result"
    )
    for name in ("lookup_result", "knowledge_result", "result", "unsupported"):
        graph.add_edge(name, END)
    return graph.compile(checkpointer=saver)
