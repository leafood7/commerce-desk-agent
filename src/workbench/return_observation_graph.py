"""Read/observe/decide loop. Existing confirmation/approval/write nodes stay deterministic."""

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from workbench.mcp_gateway import ToolFailure
from workbench.return_context import select_policy_context
from workbench.return_decisions import needs_model, validate_decision
from workbench.return_fact_checks import question_requirements, return_reasons
from workbench.return_intake import condition_candidate, description_digest
from workbench.return_observations import allowed_actions, decision_observation
from workbench.return_schemas import ReturnInput


def build_observation_graph(state_type, saver, tools, validate, planner, profile, core):
    reading = profile.get("return_source_reading", False)
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
        requirements = question_requirements(
            state.get("query"),
            selected_fields=[state["return_fact"]] if state.get("return_fact") else (),
        )
        explicit = state.get("return_task")
        if profile.get("return_task_clarification"):
            if not explicit and requirements["task"]["purpose"] == "unspecified":
                response = interrupt(
                    {
                        "status": "awaiting_input",
                        "pause_kind": "task_clarification",
                        "answer": "你希望先了解规则、查询实际字段、核对退货资格，还是申请退货？选择目的不会提交申请。",
                        "missing": ["return_task"],
                        "choices": ["policy", "facts", "eligibility", "application"],
                    }
                )
                if response.get("action") != "choose_task" or response.get("return_task") not in {
                    "policy",
                    "facts",
                    "eligibility",
                    "application",
                }:
                    raise ValueError("An explicit task choice is required")
                return {
                    "return_task": response["return_task"],
                    "next_step": "gather",
                    "confirmed": False,
                }
            if explicit:
                requirements = {
                    **requirements,
                    "read_only": explicit in {"policy", "facts"},
                    "task": {
                        "purpose": explicit,
                        "origin": "explicit_user_selection",
                        "does_not_authorize_write": True,
                    },
                }
        elif explicit:
            raise ValueError("Task choice is not enabled for this workflow")
        if explicit == "facts" and not requirements["requested"]:
            from workbench.return_fact_checks import LABELS

            response = interrupt(
                {
                    "status": "awaiting_input",
                    "pause_kind": "fact_clarification",
                    "answer": "你要核对哪项实际记录？政策文字不会代替实际记录。",
                    "missing": ["return_fact"],
                    "choices": list(LABELS),
                }
            )
            if response.get("action") != "choose_fact" or response.get("return_fact") not in LABELS:
                raise ValueError("An explicit actual field is required")
            return {"return_fact": response["return_fact"], "next_step": "gather"}
        information = guided and requirements["read_only"]
        if information:
            # Information does not require application quantity/reason/item condition.
            change = {"next_step": "observe"}
        else:
            change = core["gather"](state)
        if change["next_step"] == "observe":
            change.update(
                policy_reuse_candidates=state.get("retrieved_evidence", []) if reading else [],
                reference_check=profile.get("return_reference_check", False),
                task_frame=requirements["task"],
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
                quality_read=False,
                source_targets=[],
                source_attempted_targets=state.get("source_attempted_targets", []),
                source_readings=[],
                source_stop_reason=None,
                policy_context=[],
                retrieved_evidence=[],
                context_bound=bound,
                context_purpose="policy_question"
                if guided and requirements["read_only"]
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
        task = state.get("task_frame") or {}
        if guided and task.get("purpose") == "facts":
            requested = question_requirements(
                state.get("query"),
                selected_fields=[state["return_fact"]] if state.get("return_fact") else (),
            )["requested"]
            needs_order = any(r["field"] == "signed_at" for r in requested)
            if needs_order and not state.get("order_read"):
                return {"next_step": "read_order"}
            if any(r["field"] == "quality_verdict" for r in requested) and not state.get(
                "quality_read"
            ):
                return {"next_step": "read_quality"}
            return {"next_step": "stop"}
        if guided and state.get("context_purpose") == "policy_question":
            if not state.get("return_checked"):
                return {"next_step": "read_policy"}
            if not state.get("policy_searched"):
                return {"next_step": "search_policy"}
        allowed = allowed_actions(state, scoped=scoped)
        preview = state.get("preview") or {}
        if (
            guided
            and allowed == ["stop"]
            and not preview.get("blocked")
            and bool(
                {"quality_verdict", "quality_defect_finding"} & set(preview.get("missing", []))
            )
            and state.get("context_purpose") != "policy_question"
            and {s["id"] for s in preview["evidence"]["sources"]}
            <= set(state.get("retrieved_ids", []))
        ):
            return {"next_step": "wait_quality"}
        if (
            guided
            and state.get("order_read")
            and state.get("return_checked")
            and state.get("policy_searched")
            and state.get("context_purpose") == "policy_question"
        ):
            allowed = ["stop"]
        if reading:
            from workbench.source_reading import available_targets, inquiry_actions

            allowed = inquiry_actions(state, allowed)
        context = decision_observation(state, allowed)
        if reading:
            context["read_targets"] = [
                {
                    "id": r["id"],
                    "section": r["section"],
                    "reason": r["reason"],
                    "offset": r["request"]["offset"],
                }
                for r in available_targets(state)
            ]
            context["source_budget"] = {
                "reads_remaining": max(0, 2 - state.get("source_read_count", 0)),
                "supplement_remaining": max(0, 1 - state.get("source_search_count", 0)),
            }
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
        if result["action"] == "read_source" and "target_id" not in result:
            result["target_id"] = context["read_targets"][0]["id"]
        action = validate_decision(
            {k: result[k] for k in ("action", "target_id") if k in result}, context
        )
        trace = {"action": action, "mode": result["mode"]}
        traces = [*state.get("decisions", []), trace][-32:]
        return {
            "next_step": action,
            "selected_source_target": result.get("target_id"),
            "decisions": traces,
            "planner_mode": "bounded_return_model"
            if any(r["mode"] == "model" for r in traces)
            else "rules",
        }

    def read_quality(state):
        needed = ("order_number", "return_item_id", "return_quantity")
        values = {key: state.get(key) for key in needed}
        missing = [key for key in needed if not values[key]]
        if missing:
            response = interrupt(
                {
                    "status": "awaiting_input",
                    "answer": "查询实际质检需订单、商品行和数量。",
                    "missing": missing,
                }
            )
            values.update({key: response[key] for key in needed if response.get(key) is not None})
            if any(not values[key] for key in needed):
                raise ValueError("Quality lookup fields are required")
        result = tools(
            "get_quality_inspection",
            {
                "order_number": values["order_number"],
                "item_id": values["return_item_id"],
                "quantity": values["return_quantity"],
            },
        )
        return {**values, "quality_info": result["quality"], "quality_read": True}

    def read_policy(state):
        result = tools("get_return_policy", {"policy_id": state["return_policy_id"]})
        evidence = result["evidence"]
        sources = validate(evidence["sources"])
        return {
            "order_read": True,
            "return_checked": True,
            "preview": {"evidence": {**evidence, "sources": sources}, "missing": [], "blocked": []},
        }

    def read_order(state):
        if not state.get("order_number"):
            response = interrupt(
                {
                    "status": "awaiting_input",
                    "answer": "请提供需查询的订单号。",
                    "missing": ["order_number"],
                }
            )
            if not response.get("order_number"):
                raise ValueError("Order number is required")
            number = response["order_number"]
        else:
            number = state["order_number"]
        return {
            "order_number": number,
            "order": tools("get_order_status", {"order_number": number})["order"],
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
        binding = {
            "policy_id": state.get("return_policy_id"),
            "return_basis": state.get("return_basis") or "no_reason",
            "version_ids": sorted(
                {
                    r["version_id"]
                    for r in (state.get("preview") or {}).get("evidence", {}).get("sources", [])
                }
            ),
        }
        reused = []
        if reading and state.get("policy_reuse_binding") == binding:
            # Revalidate original tool evidence against current publication, never assistant text.
            from fastapi import HTTPException

            try:
                reused = validate(state.get("policy_reuse_candidates", []))
            except (HTTPException, ValueError):
                reused = []
            reused = [r for r in reused if r["version_id"] in binding["version_ids"]]
            matches = list({r["id"]: r for r in [*reused, *matches]}.values())
        result["trace"] = {
            **result.get("trace", {}),
            "revalidated_policy_ids": [r["id"] for r in reused],
            "policy_reuse_excludes_order_and_quality": True,
        }
        from workbench.source_reading import search_targets

        return {
            "source_targets": search_targets(matches) if reading else [],
            "policy_reuse_binding": binding,
            "policy_reuse_candidates": [],
            "policy_searched": True,
            "evidence": matches,
            "retrieved_ids": sorted(
                {m["id"] for m in matches}
                | {
                    chunk
                    for m in matches
                    if m.get("source_kind") == "source_read"
                    for chunk in m.get("covered_chunk_ids", [])
                }
            ),
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

    def read_source(state):
        if not reading:
            raise ToolFailure("source_reading_disabled")
        from workbench.knowledge_schemas import ReadInput
        from workbench.source_reading import available_targets, next_targets, reading_evidence

        selected = next(
            (r for r in available_targets(state) if r["id"] == state.get("selected_source_target")),
            None,
        )
        if selected is None:
            raise ValueError("Read target is no longer permitted")
        body = ReadInput.model_validate(selected["request"])
        record = {
            "target_id": selected["id"],
            "reason": selected["reason"],
            "section": selected["section"],
            "offset": body.offset,
        }
        attempted = [*state.get("source_attempted_targets", []), selected["id"]]
        count = state.get("source_read_count", 0) + 1
        try:
            result = tools("read_policy_source", body.model_dump(mode="json"))
            artifact = validate([reading_evidence(result, body)])[0]
        except ToolFailure as exc:
            if exc.code in {"unauthorized", "forbidden", "cancelled"}:
                raise
            return {
                "source_read_count": count,
                "source_attempted_targets": attempted,
                "source_history": [
                    *state.get("source_history", []),
                    {**record, "status": "failed"},
                ],
                "source_targets": [],
                "source_stop_reason": "read_failed",
            }
        retrieved = [*state.get("retrieved_evidence", []), artifact]
        return {
            "source_attempted_targets": attempted,
            "source_read_count": count,
            "source_read_chars": state.get("source_read_chars", 0) + len(artifact["content"]),
            "source_history": [
                *state.get("source_history", []),
                {
                    **record,
                    "status": "read",
                    "chars": len(artifact["content"]),
                    "complete": result["complete"],
                    "next_offset": result["next_offset"],
                },
            ],
            "source_readings": [*state.get("source_readings", []), artifact],
            "source_targets": next_targets(result, body, state.get("source_targets", [])),
            "retrieved_ids": sorted(
                set(state.get("retrieved_ids", [])) | set(artifact["covered_chunk_ids"])
            ),
            "retrieved_evidence": retrieved,
            "policy_context": select_policy_context(
                retrieved,
                state["preview"]["evidence"]["clauses"],
                purpose=state.get("context_purpose", "application"),
            )["context"],
        }

    def supplement(state):
        preview = state["preview"]
        clauses = preview["evidence"]["clauses"]
        if reading:
            from workbench.source_reading import required_ids

            missing = required_ids(state) - set(state.get("retrieved_ids", []))
            clauses = [c for c in clauses if c["chunk_id"] in missing] or clauses
        args = {
            "query": "\n".join(c["quote"] for c in clauses)[:1200],
            "policy_scope": "returns",
            "strategy": "hybrid_rerank",
            "top_k": 5,
        }
        if scoped:
            args["version_id"] = preview["evidence"]["sources"][0]["version_id"]
        if reading:
            args["__source_supplement"] = True
        try:
            extra = validate(tools("search_knowledge_base", args).get("matches", []))
        except ToolFailure as exc:
            if not reading or exc.code in {"unauthorized", "forbidden", "cancelled"}:
                raise
            return {
                "supplemented": True,
                "source_search_count": 1,
                "source_stop_reason": "supplement_failed",
            }
        retrieved = list(
            {m["id"]: m for m in [*state.get("retrieved_evidence", []), *extra]}.values()
        )
        from workbench.source_reading import search_targets

        return {
            "source_search_count": state.get("source_search_count", 0) + 1,
            "source_targets": list(
                {
                    r["id"]: r for r in [*state.get("source_targets", []), *search_targets(extra)]
                }.values()
            )[:8]
            if reading
            else [],
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
        if (state.get("task_frame") or {}).get("purpose") == "eligibility":
            return {
                **result_metadata(state),
                "next_step": "stop",
                "answer": "当前核对符合本演示规则；尚未申请，未提交审批或执行退款。",
            }
        if guided and state.get("context_purpose") == "policy_question":
            raise ValueError("Read-only intent cannot enter application confirmation")
        change = core["confirmation"](state)
        if response_task := (state.get("return_task")):
            change.setdefault("return_task", response_task)
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

    def complete_for_question(state):
        from workbench.source_reading import reference_gaps, required_ids

        return required_ids(state) <= set(state.get("retrieved_ids", [])) and (
            not state.get("reference_check") or not any(reference_gaps(state).values())
        )

    def result_metadata(state):
        preview = state.get("preview") or {}
        required = {e["id"] for e in preview.get("evidence", {}).get("sources", [])}
        covered = required & set(state.get("retrieved_ids", []))
        from workbench.source_reading import reference_gaps

        gaps = reference_gaps(state) if state.get("reference_check") else {}
        return {
            "retrieval": {
                **state.get("retrieval", {}),
                "mode": profile.get("return_retrieval_mode", "baseline"),
                "required_source_ids": sorted(required),
                "covered_source_ids": sorted(covered),
                "complete": required <= covered and not any(gaps.values()),
                "reference_gaps": gaps,
                "supplemental_searches": state.get("source_search_count", 0)
                if reading
                else int(state.get("supplemented", False)),
                **(
                    {
                        "source_reading": {
                            "reads": state.get("source_read_count", 0),
                            "chars": state.get("source_read_chars", 0),
                            "history": state.get("source_history", []),
                            "remaining_targets": len(state.get("source_targets", [])),
                            "stop_reason": state.get("source_stop_reason")
                            or (
                                "read_budget_exhausted"
                                if state.get("source_read_count", 0) >= 2
                                else "required_sources_covered"
                                if complete_for_question(state)
                                else "no_permitted_target"
                            ),
                            "question_sources_covered": complete_for_question(state),
                        }
                    }
                    if reading
                    else {}
                ),
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
        if (state.get("task_frame") or {}).get("purpose") == "facts" and not question_requirements(
            state.get("query")
        )["requested"]:
            return {
                **meta,
                "answer": "请在查询需求中写明要核对的实际字段，例如实际签收或质检结论；未提交申请。",
            }
        if (
            (state.get("task_frame") or {}).get("purpose") == "eligibility"
            and not preview.get("missing")
            and not preview.get("blocked")
            and meta["retrieval"]["complete"]
        ):
            return {**meta, "answer": "当前核对符合本演示规则；尚未申请，未提交审批或执行退款。"}
        if guided and state.get("context_purpose") == "policy_question":
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
        if any(meta["retrieval"].get("reference_gaps", {}).values()):
            answer += (
                "关联条款尚未读全、引用未解析，或尚未纳入管理员的资格规则；暂不能确认完整适用结论。"
            )
        return {**meta, "answer": answer}

    graph = StateGraph(state_type)
    nodes = {
        "gather": gather,
        "decide": decide,
        "read_order": read_order,
        "read_policy": read_policy,
        "read_quality": read_quality,
        "search_policy": search_policy,
        "check_return": check_return,
        "supplement": supplement,
        "read_source": read_source,
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
        from workbench.tracing import trace_node

        graph.add_node(name, trace_node(node, name))
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
    for name in (
        "read_order",
        "read_policy",
        "read_quality",
        "search_policy",
        "check_return",
        "supplement",
        "read_source",
    ):
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
