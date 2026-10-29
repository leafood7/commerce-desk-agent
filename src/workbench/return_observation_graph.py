"""Read/observe/decide loop. Existing confirmation/approval/write nodes stay deterministic."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from workbench.return_context import select_policy_context
from workbench.return_decisions import needs_model, validate_decision
from workbench.return_fact_checks import question_requirements, return_reasons
from workbench.return_intake import condition_candidate, description_digest
from workbench.return_observations import allowed_actions, decision_observation
from workbench.return_schemas import ReturnInput


def build_observation_graph(state_type, saver, tools, validate, planner, profile, core):
    bound = profile.get("return_retrieval_mode") == "clause_bound"
    scoped = profile.get("return_retrieval_mode") in {"policy_scoped", "clause_bound"}
    guided = profile.get("return_flow_version") == "guided_v3"
    fields = (
        "order_number",
        "reason",
        "return_item_id",
        "return_quantity",
        "return_condition",
        "return_policy_id",
        "return_description",
        "return_basis",
    )

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

    def gather(state):
        change = core["gather"](state)
        if change["next_step"] == "observe":
            change.update(
                next_step="decide",
                order_read=False,
                policy_searched=False,
                return_checked=False,
                supplemented=False,
                order={},
                preview={},
                evidence=[],
                retrieved_ids=[],
                retrieval={},
                quality_info={},
                policy_context=[],
                retrieved_evidence=[],
                context_bound=bound,
                context_purpose="policy_question"
                if guided and question_requirements(state.get("query"))["read_only"]
                else "application",
            )
            if guided:
                change["next_step"] = "prepare_condition"
        return change

    def prepare_condition(state):
        text = state.get("return_description") or ""
        digest = description_digest(text)
        if state.get("condition_ack") == digest:
            return {}
        proposal = condition_candidate(text)
        current = state.get("return_condition") or "unknown"
        if not text or (current in {"used", "unused"} and proposal["candidate"] == current):
            return {
                "condition_ack": digest,
                "condition_proposal": proposal,
                "condition_origin": {"kind": "structured_input", "value": current},
            }
        # Raw text can contradict a stale selection. Keep it unknown until the
        # user resolves it; never turn a parser/model candidate into eligibility.
        return {
            "return_condition": "unknown",
            "condition_proposal": proposal,
            "condition_origin": {"kind": "unconfirmed_description", "description_sha256": digest},
        }

    def decide(state):
        allowed = allowed_actions(state, scoped=scoped)
        preview = state.get("preview") or {}
        if (
            guided
            and allowed == ["stop"]
            and not preview.get("blocked")
            and bool(
                {"quality_verdict", "quality_defect_finding"} & set(preview.get("missing", []))
            )
            and not question_requirements(state.get("query"))["read_only"]
            and {s["id"] for s in preview["evidence"]["sources"]}
            <= set(state.get("retrieved_ids", []))
        ):
            return {"next_step": "wait_quality"}
        if (
            guided
            and state.get("order_read")
            and state.get("return_checked")
            and state.get("policy_searched")
            and question_requirements(state.get("query"))["read_only"]
        ):
            allowed = ["stop"]
        context = decision_observation(state, allowed)
        if guided:
            context["condition_source"] = {
                "kind": (state.get("condition_origin") or {}).get("kind"),
            }
            context["_cache_context"] = {
                "description_sha256": description_digest(state.get("return_description"))
            }
        result = {"action": allowed[0], "mode": "rules"}
        if guided and not needs_model(allowed):
            result["mode"] = "fixed_action"
        elif profile.get("return_decisions") == "model" and planner:
            result = planner(context)
        action = validate_decision({"action": result["action"]}, context)
        trace = {"action": action, "mode": result["mode"]}
        traces = [*state.get("decisions", []), trace][-32:]
        return {
            "next_step": action,
            "decisions": traces,
            "planner_mode": "bounded_return_model"
            if any(r["mode"] == "model" for r in traces)
            else "rules",
        }

    def read_order(state):
        return {
            "order": tools("get_order_status", {"order_number": state["order_number"]})["order"],
            "order_read": True,
        }

    def search_policy(state):
        args = {
            "query": state["query"],
            "policy_scope": "returns",
            "strategy": "hybrid_rerank",
            "top_k": profile.get("top_k", 3),
        }
        if scoped:
            sources = state["preview"]["evidence"]["sources"]
            args["version_id"] = sources[0]["version_id"]
        result = tools("search_knowledge_base", args)
        matches = validate(result.get("matches", []))
        return {
            "policy_searched": True,
            "evidence": matches,
            "retrieved_ids": [m["id"] for m in matches],
            "retrieval": result.get("trace", {}),
            "retrieved_evidence": matches,
            "policy_context": select_policy_context(
                matches,
                state["preview"]["evidence"]["clauses"],
                purpose=state.get("context_purpose", "application"),
            )["context"]
            if bound
            else [],
        }

    def check_return(state):
        preview = tools("evaluate_return", application(state))["preview"]
        sources = validate(preview["evidence"]["sources"])
        result = {"return_checked": True, "preview": preview, "evidence": sources}
        if guided and (
            "quality" in preview
            or any(
                r["field"] == "quality_verdict"
                for r in question_requirements(state.get("query"))["requested"]
            )
        ):
            result["quality_info"] = tools(
                "get_quality_inspection",
                {
                    "order_number": state["order_number"],
                    "item_id": state["return_item_id"],
                    "quantity": state["return_quantity"],
                },
            )["quality"]
        return result

    def wait_quality(state):
        response = interrupt(
            {
                "status": "awaiting_input",
                "pause_kind": "quality_wait",
                "answer": "适用政策要求核实质检结论。当前没有明确有效的演示质检结果，未创建退货申请；请由独立处理人登记后刷新核对。",
                "quality": state.get("quality_info"),
                "known": {k: state.get(k) for k in fields},
                "evidence": state["preview"]["evidence"]["sources"],
            }
        )
        if response.get("action") != "refresh":
            raise ValueError("Quality wait accepts refresh only")
        if state.get("turns", 0) >= 10:
            return {
                "next_step": "finish",
                "answer": "刷新次数已达上限，请人工核实；未提交退货申请。",
            }
        return {"next_step": "gather", "turns": state.get("turns", 0) + 1}

    def supplement(state):
        preview = state["preview"]
        args = {
            "query": "\n".join(c["quote"] for c in preview["evidence"]["clauses"])[:1200],
            "policy_scope": "returns",
            "strategy": "hybrid_rerank",
            "top_k": 5,
        }
        if scoped:
            args["version_id"] = preview["evidence"]["sources"][0]["version_id"]
        extra = validate(tools("search_knowledge_base", args).get("matches", []))
        retrieved = list(
            {m["id"]: m for m in [*state.get("retrieved_evidence", []), *extra]}.values()
        )
        return {
            "supplemented": True,
            "retrieved_ids": sorted(set(state.get("retrieved_ids", [])) | {e["id"] for e in extra}),
            "retrieved_evidence": retrieved,
            "policy_context": select_policy_context(
                retrieved,
                preview["evidence"]["clauses"],
                purpose=state.get("context_purpose", "application"),
            )["context"]
            if bound
            else [],
        }

    def ask_condition(state):
        # Model does not phrase the question or parse a guessed answer.
        proposal = state.get("condition_proposal") or {}
        candidate = proposal.get("candidate") if guided else None
        prompt = "请核实商品是否使用；未知状态不能直接认定满足退货条件。"
        if candidate:
            prompt = (
                "原话“"
                + proposal["source"]["quote"]
                + "”可能表示"
                + ("未使用" if candidate == "unused" else "已使用")
                + "，请明确确认或更正；尚未作为业务事实。"
            )
        response = interrupt(
            {
                "status": "awaiting_input",
                "answer": prompt,
                "missing": ["return_condition"],
                "known": {k: state.get(k) for k in fields},
                "evidence": state["preview"]["evidence"]["sources"],
                **(
                    {
                        "pause_kind": "condition_confirmation"
                        if candidate
                        else "condition_clarification",
                        "condition_proposal": proposal,
                    }
                    if guided
                    else {}
                ),
            }
        )
        if guided and response["action"] == "accept_condition":
            if candidate not in {"used", "unused"} or response.get(
                "condition_proposal_hash"
            ) != proposal.get("proposal_hash"):
                raise ValueError("Condition proposal changed")
            return {
                "return_condition": candidate,
                "condition_ack": description_digest(state.get("return_description")),
                "condition_origin": {
                    "kind": "confirmed_description",
                    "value": candidate,
                    "source": proposal["source"],
                    "description_sha256": proposal["description_sha256"],
                },
                "next_step": "gather",
                "confirmed": False,
            }
        if (
            guided
            and response.get("return_description") is not None
            and response["return_description"] != (state.get("return_description") or "")
            and response.get("return_condition") not in {"used", "unused"}
        ):
            return {
                **{k: response[k] for k in fields if response.get(k) is not None},
                "return_condition": "unknown",
                "condition_ack": None,
                "condition_proposal": {},
                "condition_origin": {},
                "next_step": "gather",
                "confirmed": False,
            }
        if response.get("return_condition") not in {"unused", "used"}:
            return {"next_step": "finish", "answer": "商品状态仍未知，未提交退货申请。"}
        return {
            **{k: response[k] for k in fields if response.get(k) is not None},
            "next_step": "gather",
            "confirmed": False,
            **(
                {
                    "condition_ack": description_digest(
                        response["return_description"]
                        if response.get("return_description") is not None
                        else state.get("return_description")
                    ),
                    "condition_origin": {
                        "kind": "explicit_clarification",
                        "value": response["return_condition"],
                    },
                }
                if guided
                else {}
            ),
        }

    def confirmation(state):
        if guided and question_requirements(state.get("query"))["read_only"]:
            raise ValueError("Read-only intent cannot enter application confirmation")
        change = core["confirmation"](state)
        if guided and change.get("next_step") == "gather":
            text = change.get("return_description", state.get("return_description")) or ""
            if text != (state.get("return_description") or ""):
                change.update(condition_ack=None, condition_proposal={}, condition_origin={})
            elif change.get("return_condition") in {"used", "unused"} and change.get(
                "return_condition"
            ) != state.get("return_condition"):
                change.update(
                    condition_ack=description_digest(text),
                    condition_proposal={},
                    condition_origin={
                        "kind": "explicit_correction",
                        "value": change["return_condition"],
                    },
                )
        return change

    def result_metadata(state):
        preview = state.get("preview") or {}
        required = {e["id"] for e in preview.get("evidence", {}).get("sources", [])}
        covered = required & set(state.get("retrieved_ids", []))
        return {
            "retrieval": {
                **state.get("retrieval", {}),
                "mode": profile.get("return_retrieval_mode", "baseline"),
                "required_source_ids": sorted(required),
                "covered_source_ids": sorted(covered),
                "complete": required <= covered,
                "supplemental_searches": int(state.get("supplemented", False)),
            },
            "evidence": state.get("retrieved_evidence", [])
            if bound and state.get("context_purpose") == "policy_question"
            else preview.get("evidence", {}).get("sources", []),
            **({"policy_context": state.get("policy_context", [])} if bound else {}),
        }

    def review(state):
        return result_metadata(state)

    def stop(state):
        preview = state["preview"]
        meta = result_metadata(state)
        if guided and question_requirements(state.get("query"))["read_only"]:
            answer = "已按只读需求核对订单与政策，未进入申请确认，未提交退货申请。" + (
                "当前检索未覆盖全部必要政策条款。"
                if not meta["retrieval"]["complete"]
                else "政策原文可供核对，不能替代缺失的实际业务字段。"
            )
            if bound:
                answer = "已按只读需求核对，未进入申请确认，未提交退货申请。" + (
                    "已检索到政策原文，请核对所问问题的适用范围和条件；原文不代表实际订单事实已确认。"
                    if meta["evidence"]
                    else "当前检索没有可引用的政策原文；实际业务字段单独列示，未知项仍待核实。"
                )
        elif not meta["retrieval"]["complete"]:
            answer = "一次补检后仍未覆盖该商品的必要退货条款；未提交申请，请人工核对来源。"
        elif preview["blocked"]:
            answer = (
                "当前演示规则存在限制："
                + (return_reasons(preview["blocked"]) if guided else "、".join(preview["blocked"]))
                + "。未提交退货申请；不据此判断其他退货权利。"
            )
        else:
            answer = (
                "需核实业务字段或适用政策："
                + (return_reasons(preview["missing"]) if guided else "、".join(preview["missing"]))
                + "。预计送达不等于实际签收，未提交申请。"
            )
        return {**meta, "answer": answer}

    graph = StateGraph(state_type)
    nodes = {
        "gather": gather,
        "decide": decide,
        "read_order": read_order,
        "search_policy": search_policy,
        "check_return": check_return,
        "supplement": supplement,
        "ask_condition": ask_condition,
        "review": review,
        "stop": stop,
        **{
            k: core[k]
            for k in (
                "confirmation",
                "submit",
                "wait_approval",
                "refresh",
                "confirm_external",
                "external",
                "result",
            )
        },
    }
    if guided:
        nodes["prepare_condition"] = prepare_condition
        nodes["wait_quality"] = wait_quality
        nodes["confirmation"] = confirmation
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "gather")
    graph.add_conditional_edges(
        "gather", lambda s: END if s["next_step"] == "finish" else s["next_step"]
    )
    if guided:
        graph.add_edge("prepare_condition", "decide")
        graph.add_conditional_edges(
            "wait_quality", lambda s: END if s["next_step"] == "finish" else "gather"
        )
    graph.add_conditional_edges("decide", lambda s: s["next_step"])
    for name in ("read_order", "search_policy", "check_return", "supplement"):
        graph.add_edge(name, "decide")
    graph.add_conditional_edges(
        "ask_condition", lambda s: END if s["next_step"] == "finish" else "gather"
    )
    graph.add_edge("review", "confirmation")
    graph.add_edge("stop", END)
    graph.add_conditional_edges("confirmation", lambda s: s["next_step"])
    graph.add_edge("submit", "wait_approval")
    graph.add_edge("wait_approval", "refresh")
    graph.add_conditional_edges("refresh", lambda s: s["next_step"])
    graph.add_edge("confirm_external", "external")
    graph.add_edge("external", "result")
    graph.add_edge("result", END)
    return graph.compile(checkpointer=saver)
