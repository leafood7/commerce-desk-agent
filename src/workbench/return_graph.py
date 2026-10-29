"""Persistent return workflow driven by tool observations, not model eligibility.

This first vertical slice deliberately uses deterministic decisions. A later bounded
model decision adapter can propose read/ask actions without owning write authority.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from workbench.mcp_gateway import ToolFailure
from workbench.return_schemas import ReturnInput


class ReturnState(TypedDict, total=False):
    query: str
    order_number: str | None
    reason: str | None
    return_policy_id: str
    return_item_id: str | None
    return_quantity: int | None
    return_condition: str | None
    order: dict
    evidence: list
    request: dict
    preview: dict
    answer: str
    terminal: str
    next_step: str
    turns: int
    confirmed: bool
    corrections: list
    retrieved_ids: list
    retrieval: dict
    order_read: bool
    policy_searched: bool
    return_checked: bool
    supplemented: bool
    decisions: list
    planner_mode: str
    return_description: str | None
    condition_proposal: dict
    condition_ack: str | None
    condition_origin: dict
    quality_info: dict
    return_basis: str | None
    policy_context: list
    retrieved_evidence: list
    context_bound: bool
    context_purpose: str


FIELDS = ("order_number", "reason", "return_item_id", "return_quantity", "return_condition")


def application(state):
    return ReturnInput(
        order_number=state["order_number"],
        item_id=state["return_item_id"],
        policy_id=state["return_policy_id"],
        quantity=state["return_quantity"],
        reason=state["reason"],
        item_condition=state.get("return_condition") or "unknown",
        return_basis=state.get("return_basis") or "no_reason",
    ).model_dump(mode="json")


def build_return_graph(saver, tools, validate, planner=None, *, profile=None):
    def gather(state):
        missing = [field for field in FIELDS[:-1] if not state.get(field)]
        if not missing:
            return {"next_step": "observe"}
        response = interrupt(
            {
                "status": "awaiting_input",
                "answer": "请明确退货订单、商品行、数量和原因；不从猜测补全。",
                "missing": missing,
                "known": {k: state.get(k) for k in FIELDS},
                "return_description": state.get("return_description"),
            }
        )
        if state.get("turns", 0) >= 10:
            return {"next_step": "finish", "answer": "补充次数已达上限，请人工核实。"}
        return {
            **{k: response[k] for k in FIELDS if response.get(k) is not None},
            **(
                {"return_basis": response["return_basis"]}
                if response.get("return_basis") is not None
                else {}
            ),
            **(
                {"return_policy_id": response["return_policy_id"]}
                if response.get("return_policy_id")
                else {}
            ),
            **(
                {"return_description": response["return_description"]}
                if response.get("return_description") is not None
                else {}
            ),
            "turns": state.get("turns", 0) + 1,
            "next_step": "gather",
        }

    def observe(state):
        result = tools("get_order_status", {"order_number": state["order_number"]})
        return {"order": result["order"]}

    def retrieve(state):
        result = tools(
            "search_knowledge_base",
            {
                "query": state["query"],
                "policy_scope": "returns",
                "strategy": "hybrid_rerank",
                "top_k": (profile or {}).get("top_k", 3),
            },
        )
        evidence = validate(result.get("matches", []))
        return {
            "retrieved_ids": [e["id"] for e in evidence],
            "evidence": evidence,
            "retrieval": result.get("trace", {}),
        }

    def evaluate(state):
        preview = tools("evaluate_return", application(state))["preview"]
        # Validate canonical source bodies again. Mapping is human-reviewed, not
        # inferred by the model or supplied as a self-certified citation.
        evidence = validate(preview["evidence"]["sources"])
        required = {e["id"] for e in evidence}
        covered = required & set(state.get("retrieved_ids", []))
        if covered != required:
            query = "\n".join(c["quote"] for c in preview["evidence"]["clauses"])[:1200]
            extra = tools(
                "search_knowledge_base",
                {
                    "query": query,
                    "policy_scope": "returns",
                    "strategy": "hybrid_rerank",
                    "top_k": 5,
                },
            )
            extra_evidence = validate(extra.get("matches", []))
            covered |= required & {e["id"] for e in extra_evidence}
        retrieval = {
            **state.get("retrieval", {}),
            "required_source_ids": sorted(required),
            "covered_source_ids": sorted(covered),
            "complete": covered == required,
            "supplemental_searches": int(
                required != required & set(state.get("retrieved_ids", []))
            ),
        }
        if covered != required:
            return {
                "preview": preview,
                "evidence": evidence,
                "retrieval": retrieval,
                "next_step": "finish",
                "answer": "一次补检后仍未覆盖该商品的必要退货条款；未提交申请，请人工核对来源。",
            }
        if preview["blocked"]:
            return {
                "preview": preview,
                "evidence": evidence,
                "retrieval": retrieval,
                "next_step": "finish",
                "answer": "当前演示规则存在限制："
                + "、".join(preview["blocked"])
                + "。未提交退货申请；不据此判断其他退货权利。",
            }
        missing = preview["missing"]
        if missing:
            if missing == ["item_condition"]:
                response = interrupt(
                    {
                        "status": "awaiting_input",
                        "answer": "请核实商品是否使用；未知状态不能直接认定满足退货条件。",
                        "missing": ["return_condition"],
                        "known": {k: state.get(k) for k in FIELDS},
                        "evidence": evidence,
                    }
                )
                if response.get("return_condition") not in {"unused", "used"}:
                    return {
                        "next_step": "finish",
                        "answer": "商品状态仍未知，未提交退货申请。",
                        "evidence": evidence,
                    }
                return {
                    **{k: response[k] for k in FIELDS if response.get(k) is not None},
                    "next_step": "gather",
                    "confirmed": False,
                }
            return {
                "preview": preview,
                "evidence": evidence,
                "retrieval": retrieval,
                "next_step": "finish",
                "answer": "需核实业务字段或适用政策："
                + "、".join(missing)
                + "。不能以预计送达代替实际签收，未提交申请。",
            }
        return {
            "preview": preview,
            "evidence": evidence,
            "retrieval": retrieval,
            "next_step": "confirmation",
        }

    def confirmation(state):
        response = interrupt(
            {
                "status": "awaiting_confirmation",
                "answer": "请确认本版本退货申请，确认后仅提交独立审批，不执行退款。",
                "proposal": {
                    **{k: state.get(k) for k in FIELDS},
                    "return_policy_id": state["return_policy_id"],
                    "return_description": state.get("return_description"),
                    "return_basis": state.get("return_basis") or "no_reason",
                    "goods_value_cents": state["preview"]["goods_value_cents"],
                    "refund_amount_cents": None,
                    "external_mode": "simulation",
                },
                "evidence": state["evidence"],
            }
        )
        if response["action"] == "correct":
            return {
                **{k: response[k] for k in FIELDS if response.get(k) is not None},
                **(
                    {"return_basis": response["return_basis"]}
                    if response.get("return_basis") is not None
                    else {}
                ),
                **(
                    {"return_policy_id": response["return_policy_id"]}
                    if response.get("return_policy_id")
                    else {}
                ),
                **(
                    {"return_description": response["return_description"]}
                    if response.get("return_description") is not None
                    else {}
                ),
                "confirmed": False,
                "next_step": "gather",
                "corrections": [
                    *state.get("corrections", []),
                    {k: response[k] for k in FIELDS if response.get(k) is not None},
                ],
            }
        if response["action"] != "confirm":
            raise ValueError("Explicit confirmation required")
        return {"confirmed": True, "next_step": "submit"}

    def submit(state):
        fresh = tools("evaluate_return", application(state))["preview"]
        if fresh["preview_hash"] != state["preview"]["preview_hash"]:
            raise ToolFailure("return_preview_changed")
        row = tools("create_return_draft", application(state))["request"]
        row = tools(
            "confirm_return_application",
            {
                "request_id": row["id"],
                "expected_revision": row["revision"],
                "preview_hash": fresh["preview_hash"],
            },
        )["request"]
        return {"request": row}

    def wait_approval(state):
        interrupt(
            {
                "status": "awaiting_approval",
                "answer": "退货申请已提交独立审批，尚未受理或退款。审批后刷新核实。",
                "request": state["request"],
                "evidence": state.get("evidence", []),
            }
        )
        return {}

    def refresh(state):
        row = tools("get_return_status", {"request_id": state["request"]["id"]})["request"]
        step = (
            "wait_approval"
            if row["status"] == "pending_approval"
            else "confirm_external"
            if row["status"] == "approved"
            else "result"
        )
        return {"request": row, "next_step": step}

    def confirm_external(state):
        response = interrupt(
            {
                "status": "awaiting_confirmation",
                "answer": "本版本已获批准。是否提交本地模拟受理？不会执行真实退款。",
                "request": state["request"],
                "proposal": {"external_mode": "simulation"},
            }
        )
        if response["action"] != "confirm":
            raise ValueError("External simulation requires confirmation")
        return {}

    def external(state):
        row = tools(
            "submit_simulated_return",
            {
                "request_id": state["request"]["id"],
                "expected_revision": state["request"]["revision"],
            },
        )["request"]
        return {"request": row}

    def result(state):
        row = state["request"]
        return {
            "answer": "退货申请状态：" + row["status"] + "。外部受理为本地模拟，真实退款未执行。"
        }

    if (profile or {}).get("return_flow_version") in {"observed_v2", "guided_v3"}:
        from workbench.return_observation_graph import build_observation_graph

        return build_observation_graph(
            ReturnState,
            saver,
            tools,
            validate,
            planner,
            profile,
            {
                "gather": gather,
                "confirmation": confirmation,
                "submit": submit,
                "wait_approval": wait_approval,
                "refresh": refresh,
                "confirm_external": confirm_external,
                "external": external,
                "result": result,
            },
        )
    graph = StateGraph(ReturnState)
    for name, node in (
        ("gather", gather),
        ("observe", observe),
        ("retrieve", retrieve),
        ("evaluate", evaluate),
        ("confirmation", confirmation),
        ("submit", submit),
        ("wait_approval", wait_approval),
        ("refresh", refresh),
        ("confirm_external", confirm_external),
        ("external", external),
        ("result", result),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "gather")
    graph.add_conditional_edges(
        "gather", lambda s: END if s["next_step"] == "finish" else s["next_step"]
    )
    graph.add_edge("observe", "retrieve")
    graph.add_edge("retrieve", "evaluate")
    graph.add_conditional_edges(
        "evaluate", lambda s: END if s["next_step"] == "finish" else s["next_step"]
    )
    graph.add_conditional_edges("confirmation", lambda s: s["next_step"])
    graph.add_edge("submit", "wait_approval")
    graph.add_edge("wait_approval", "refresh")
    graph.add_conditional_edges("refresh", lambda s: s["next_step"])
    graph.add_edge("confirm_external", "external")
    graph.add_edge("external", "result")
    graph.add_edge("result", END)
    return graph.compile(checkpointer=saver)
