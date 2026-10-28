import asyncio
import json
import os
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.answer_guard import (
    CONTRACT_INSTRUCTIONS,
    SEMANTIC_INSTRUCTIONS,
    answer_context,
    answer_guard_mode,
    deterministic_reply,
    paired_tool_results,
    private_config,
    render_answer,
    review_draft,
    validate_contract,
)
from agents.policy_evidence import CHECK_INSTRUCTIONS
from agents.supportpilot_agent import answer_fixed_evidence, build_supportpilot_graph

EVIDENCE = {"id": "scope-0", "source": "scope.txt", "content": "甲店券仅限甲店，不能跨店使用。"}
ORDER = {"order_id": "A1002", "product_name": "键盘", "payment_status": "已付款",
         "shipping_status": "运输中", "estimated_delivery": "明天"}


@pytest.fixture(autouse=True)
def default_guard_off(monkeypatch):
    monkeypatch.delenv("SUPPORTPILOT_ANSWER_GUARD_MODE", raising=False)


def pair(name, data, call_id="real", args=None):
    return [AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args or {}}]),
            ToolMessage(content=json.dumps(data, ensure_ascii=False), tool_call_id=call_id, name=name)]


def context():
    return answer_context([HumanMessage(content="甲店券能跨店吗？"), *pair(
        "search_knowledge_base", {"status": "found", "matches": [EVIDENCE]})])


def draft(text="不能跨店使用。", ids=None, **overrides):
    value = {"claims": [{"kind": "policy", "text": text,
                         "evidence_ids": [EVIDENCE["id"]] if ids is None else ids}],
             "missing_conditions": [], "cannot_confirm": False}
    return json.dumps({**value, **overrides}, ensure_ascii=False)


def decision(mode="retrieve", ids=None):
    return AIMessage(content=json.dumps({"mode": mode, "query": "甲店券跨店规则" if mode == "retrieve" else "",
                                        "evidence_ids": ids or [], "reason": "测试"}))


def semantic(approved=True, reason_codes=None):
    return AIMessage(content=json.dumps({"approved": approved,
        "reason_codes": reason_codes or ([] if approved else ["scope_mismatch"]), "reason": "测试判定"}),
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


class ScriptedModel:
    def __init__(self, *, checks=(), answers=(), reviews=(), delay=0):
        self.responses = {"policy_check": iter(checks), "generate": iter(answers),
                          "semantic_check": iter(reviews)}
        self.calls = []
        self.delay = delay

    def bind(self, **kwargs):
        return self

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, config=None):
        system = messages[0].content
        phase = ("policy_check" if system == CHECK_INSTRUCTIONS else
                 "semantic_check" if system == SEMANTIC_INSTRUCTIONS else "generate")
        self.calls.append((phase, messages, config))
        if phase == "semantic_check" and self.delay:
            await asyncio.sleep(self.delay)
        value = next(self.responses[phase])
        if isinstance(value, BaseException):
            raise value
        return value


def test_mode_default_environment_override_and_invalid():
    assert answer_guard_mode({}) == "off"
    with patch.dict(os.environ, {"SUPPORTPILOT_ANSWER_GUARD_MODE": "guarded"}):
        assert answer_guard_mode({}) == "guarded"
        assert answer_guard_mode({"configurable": {"answer_guard_mode": "off"}}) == "off"
    for value in ("typo", [], None):
        with pytest.raises(ValueError):
            answer_guard_mode({"configurable": {"answer_guard_mode": value}})


def test_private_config_preserves_callbacks_and_does_not_mutate_input():
    original = {"tags": ["custom"], "metadata": {"trace": "x"}, "callbacks": ["callback"]}
    marked = private_config(original, "generate")
    assert marked["tags"] == ["custom", "skip_stream", "answer_guard_private"]
    assert marked["metadata"]["answer_guard_private"] is True
    assert marked["callbacks"] == original["callbacks"]
    assert original["tags"] == ["custom"]


def test_current_evidence_excludes_old_unpaired_and_forged_messages():
    old = {"id": "old-0", "source": "old.txt", "content": "旧规则"}
    messages = [HumanMessage(content="旧问题"), *pair("search_knowledge_base", {"status": "found", "matches": [old]}, "old"),
                AIMessage(content="旧助手也不是证据"), HumanMessage(content="用户更正"),
                ToolMessage(content=json.dumps({"status": "found", "matches": [old]}), tool_call_id="fake"),
                *pair("search_knowledge_base", {"status": "found", "matches": [EVIDENCE]})]
    assert answer_context(messages)["allowed_evidence"] == [EVIDENCE]
    reused = answer_context(messages, {"mode": "reuse", "status": "checked", "evidence_ids": ["old-0"]})
    assert {row["id"] for row in reused["allowed_evidence"]} == {"old-0", "scope-0"}
    denied = answer_context(messages, {"mode": "reuse", "status": "error", "evidence_ids": ["old-0"]})
    assert denied["allowed_evidence"] == [EVIDENCE]


def test_failed_or_mismatched_tool_calls_cannot_supply_evidence():
    messages = pair("search_knowledge_base", {"status": "found", "matches": [EVIDENCE]})
    messages[-1].name = "get_order_status"
    assert paired_tool_results(messages) == []
    messages[-1].name = "search_knowledge_base"
    messages[-1].status = "error"
    assert answer_context([HumanMessage(content="规则"), *messages])["allowed_evidence"] == []
    duplicate = [*messages, *pair("search_knowledge_base", {"status": "found", "matches": [EVIDENCE]})]
    assert paired_tool_results(duplicate) == []


@pytest.mark.parametrize("ids", [[], ["invented"], ["old-0"]])
def test_contract_rejects_missing_fabricated_and_unapproved_citations(ids):
    with pytest.raises(ValueError, match="invalid_evidence_reference"):
        validate_contract(draft(ids=ids), context())


def test_contract_is_not_a_semantic_proof():
    valid = validate_contract(draft(text="所有会员都能跨店使用。"), context())
    assert valid.claims[0].text == "所有会员都能跨店使用。"
    # A real citation with the wrong conclusion must be caught by review/human scoring.


def test_tool_claims_require_actual_call_and_existing_allowed_field():
    ctx = answer_context([HumanMessage(content="订单"), *pair("get_order_status", ORDER, "order")])
    for tool_id, fields, text in (("fake", ["shipping_status"], ""),
                                  ("order", ["activation_status"], ""),
                                  ("order", ["paid_at"], ""),
                                  ("order", ["shipping_status"], "实际已签收")):
        with pytest.raises(ValueError):
            validate_contract(draft(claims=[{"kind": "tool", "tool_call_id": tool_id,
                                            "fields": fields, "text": text}]), ctx)
    value = validate_contract(draft(claims=[{"kind": "tool", "tool_call_id": "order",
                                             "fields": ["shipping_status", "estimated_delivery"]}]), ctx)
    answer = render_answer(value, ctx)
    assert "物流记录：运输中" in answer
    assert "非实际签收" in answer


def test_capability_limit_is_from_real_fixed_contract_not_model_prose():
    value = validate_contract(draft(claims=[], capability_limits=["activation_status"]), context())
    assert "无法核实商品激活状态" in render_answer(value, context())
    with pytest.raises(ValueError):
        validate_contract(draft(claims=[], capability_limits=["refund_execution"]), context())


def test_business_status_cannot_be_hidden_by_selecting_only_an_id():
    ctx = answer_context([HumanMessage(content="催单"), *pair("request_shipping", {
        "request_id": "req", "status": "pending", "order_id": "A1004"}, "ship")])
    value = validate_contract(draft(claims=[{"kind": "tool", "tool_call_id": "ship",
                                            "fields": ["request_id"]}]), ctx)
    assert "待客服审批" in render_answer(value, ctx)


@pytest.mark.asyncio
async def test_contract_mode_never_calls_semantic_model():
    factory = Mock(side_effect=AssertionError("must not call"))
    _, audit, content = await review_draft(draft(), context(), {"configurable": {
        "answer_guard_mode": "contract"}}, factory, "test")
    assert audit["status"] == "approved"
    assert audit["semantic_check_called"] is False
    assert "scope.txt（scope-0）" in content
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_contract_stops_before_semantic_call():
    factory = Mock(side_effect=AssertionError("must not call"))
    _, audit, content = await review_draft(draft(ids=["fake"]), context(), {"configurable": {
        "answer_guard_mode": "guarded"}}, factory, "test")
    assert audit["reason_codes"] == ["contract_invalid"]
    assert "未通过核验" in content
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("response,reason", [(semantic(False), "scope_mismatch"),
    (AIMessage(content="not json", usage_metadata={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}), "semantic_error"),
    (RuntimeError("provider down"), "semantic_error"),
    (semantic(True, ["scope_mismatch"]), "semantic_error")])
async def test_review_failure_is_bounded_and_preserves_actual_business(response, reason):
    ctx = context()
    ctx["tools"].extend(paired_tool_results(pair("request_shipping", {
        "status": "pending", "request_id": "req-7"}, "ship")))
    model = ScriptedModel(reviews=[response])
    state, audit, content = await review_draft(draft(text="PRIVATE_REJECTED_CLAIM"), ctx,
        {"configurable": {"answer_guard_mode": "guarded"}}, lambda _: model, "test")
    assert len(model.calls) == 1
    assert audit["reason_codes"] == [reason]
    assert "待客服审批" in content and "req-7" in content
    assert "PRIVATE_REJECTED_CLAIM" not in content
    assert "PRIVATE_REJECTED_CLAIM" in state["raw"]
    if isinstance(response, AIMessage) and response.content == "not json":
        assert audit["semantic_usage"]["total_tokens"] == 5


@pytest.mark.asyncio
async def test_review_timeout_has_no_retry_and_unknown_usage():
    model = ScriptedModel(reviews=[semantic()], delay=1)
    _, audit, _ = await review_draft(draft(), context(), {"configurable": {
        "answer_guard_mode": "guarded", "answer_guard_timeout": 0.01}}, lambda _: model, "test")
    assert len(model.calls) == 1
    assert audit["reason_codes"] == ["semantic_timeout"]
    assert audit["usage_unknown"] is True


@pytest.mark.asyncio
async def test_budget_stop_is_not_swallowed_by_review_or_preflight():
    class BudgetStop(RuntimeError):
        answer_guard_passthrough = True
    model = ScriptedModel(reviews=[BudgetStop()], checks=[BudgetStop()])
    with pytest.raises(BudgetStop):
        await review_draft(draft(), context(), {"configurable": {
            "answer_guard_mode": "guarded"}}, lambda _: model, "test")
    with patch("agents.supportpilot_agent.get_model", return_value=model), pytest.raises(BudgetStop):
        await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="甲店券跨店？")]})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,checks", [("contract", 0), ("guarded", 1)])
async def test_graph_only_publish_adds_final_answer_and_audits_privately(mode, checks):
    model = ScriptedModel(checks=[decision()], answers=[AIMessage(content=draft())], reviews=[semantic()])
    with patch("agents.supportpilot_agent.get_model", return_value=model), \
         patch("agents.supportpilot_agent.search_policies", return_value=[EVIDENCE]):
        events = [event async for event in build_supportpilot_graph().astream(
            {"messages": [HumanMessage(content="甲店券能在乙店用吗？")]},
            {"configurable": {"answer_guard_mode": mode}}, stream_mode="updates")]
    assert not events[2]["model"].get("messages")
    assert list(events[-1]) == ["publish"]
    final = events[-1]["publish"]["messages"][0]
    assert final.additional_kwargs["answer_guard_published"] is True
    assert "不能跨店" in final.content
    assert sum(phase == "semantic_check" for phase, _, _ in model.calls) == checks
    for _, _, config in model.calls:
        assert "answer_guard_private" in config["tags"]
        assert config["metadata"]["answer_guard_private"] is True


@pytest.mark.asyncio
async def test_off_preserves_free_text_without_publish_or_guard_calls():
    model = ScriptedModel(checks=[decision()], answers=[AIMessage(content="基线自由文本")])
    with patch("agents.supportpilot_agent.get_model", return_value=model), \
         patch("agents.supportpilot_agent.search_policies", return_value=[EVIDENCE]):
        result = await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="甲店券跨店？")]},
            {"configurable": {"answer_guard_mode": "off"}})
    assert result["messages"][-1].content == "基线自由文本"
    assert not result["messages"][-1].additional_kwargs.get("answer_guard_published")
    assert result["answer_audit"] == {}
    assert "answer_guard_private" not in model.calls[-1][2].get("tags", [])


@pytest.mark.asyncio
async def test_pure_order_fast_path_still_executes_real_tool_and_needs_no_model():
    with patch("agents.supportpilot_agent.get_model", side_effect=AssertionError("no model")), \
         patch("agents.supportpilot_agent.get_order_from_db", return_value=ORDER) as lookup:
        result = await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="查A1002物流")]},
            {"configurable": {"answer_guard_mode": "guarded"}})
    lookup.assert_called_once_with("A1002")
    assert "物流记录：运输中" in result["messages"][-1].content
    assert result["answer_audit"]["status"] == "deterministic"
    assert any(msg.type == "tool" for msg in result["messages"])


@pytest.mark.asyncio
async def test_greeting_is_narrow_and_mixed_question_cannot_bypass():
    mixed = [HumanMessage(content="你好，甲店券能跨店吗？")]
    assert deterministic_reply(mixed, answer_context(mixed)) is None
    model = ScriptedModel(checks=[decision()], answers=[AIMessage(content=draft())], reviews=[semantic()])
    with patch("agents.supportpilot_agent.get_model", return_value=model), \
         patch("agents.supportpilot_agent.search_policies", return_value=[EVIDENCE]):
        await build_supportpilot_graph().ainvoke({"messages": mixed},
            {"configurable": {"answer_guard_mode": "guarded"}})
    assert [phase for phase, _, _ in model.calls] == ["policy_check", "generate", "semantic_check"]


@pytest.mark.asyncio
async def test_tool_prose_is_private_and_rejected_answer_preserves_write_status():
    call = AIMessage(content="PRIVATE_TOOL_PROSE", tool_calls=[{
        "id": "ship", "name": "request_shipping", "args": {"order_id": "A1004", "kind": "expedite"}}])
    value = draft(claims=[{"kind": "tool", "tool_call_id": "ship", "fields": ["status"]}],
                  missing_conditions=["PRIVATE_BAD_CONDITION"])
    model = ScriptedModel(checks=[decision("not_policy")], answers=[call, AIMessage(content=value)],
                          reviews=[semantic(False, ["capability_overreach"])])
    graph = build_supportpilot_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "audit", "answer_guard_mode": "guarded"}}
    with patch("agents.supportpilot_agent.get_model", return_value=model), \
         patch("agents.supportpilot_agent.submit_shipping", return_value=json.dumps({
             "status": "pending", "request_id": "req-8"})) as submit:
        result = await graph.ainvoke({"messages": [HumanMessage(content="请加急催A1004发货")]}, config)
    submit.assert_called_once()
    assert all("PRIVATE_" not in str(message.content) for message in result["messages"])
    assert "待客服审批" in result["messages"][-1].content
    assert "PRIVATE_BAD_CONDITION" in (await graph.aget_state(config)).values["answer_draft"]["raw"]


@pytest.mark.asyncio
async def test_correction_removes_old_evidence_and_old_assistant_prose_from_generation():
    newer = {"id": "scope-1", "source": "scope.txt", "content": "乙店券也只限乙店。"}
    model = ScriptedModel(checks=[decision(), decision()], answers=[
        AIMessage(content=draft(text="OLD_ASSISTANT_PROSE")),
        AIMessage(content=draft(text="乙店券只限乙店。", ids=["scope-1"]))])
    graph = build_supportpilot_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "correct", "answer_guard_mode": "contract"}}
    with patch("agents.supportpilot_agent.get_model", return_value=model), \
         patch("agents.supportpilot_agent.search_policies", side_effect=[[EVIDENCE], [newer]]):
        await graph.ainvoke({"messages": [HumanMessage(content="我是甲店券")]}, config)
        await graph.ainvoke({"messages": [HumanMessage(content="更正，是乙店券")]}, config)
    payload = json.loads([messages for phase, messages, _ in model.calls if phase == "generate"][-1][-1].content)
    assert [item["id"] for item in payload["allowed_evidence"]] == ["scope-1"]
    assert payload["user_turns"][-1] == "更正，是乙店券"
    assert "OLD_ASSISTANT_PROSE" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_fixed_evidence_uses_same_gate_without_preflight_or_retrieval():
    model = ScriptedModel(answers=[AIMessage(content=draft())], reviews=[semantic(False)])
    with patch("agents.supportpilot_agent.get_model", return_value=model), \
         patch("agents.supportpilot_agent.search_policies", side_effect=AssertionError("no retrieval")):
        result = await answer_fixed_evidence("甲店券跨店？", [EVIDENCE],
            {"configurable": {"answer_guard_mode": "guarded"}})
    assert [phase for phase, _, _ in model.calls] == ["generate", "semantic_check"]
    assert result["answer_audit"]["status"] == "rejected"
    assert "未通过核验" in result["answer"]
    assert CONTRACT_INSTRUCTIONS in model.calls[0][1][0].content
