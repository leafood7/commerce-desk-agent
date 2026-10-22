import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.language_models import FakeMessagesListChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.policy_evidence import policy_evidence, validate_decision, CHECK_INSTRUCTIONS
from agents.supportpilot_agent import build_supportpilot_graph


def decision(mode="retrieve", query="店铺券能否跨店使用", ids=None):
    return AIMessage(content=json.dumps({"mode": mode, "query": query,
        "evidence_ids": ids or [], "reason": "测试决定"}, ensure_ascii=False))


EVIDENCE = {"id": "coupon_scope_policy.txt-0", "source": "coupon_scope_policy.txt", "content": "店铺券不能跨店使用"}


class FakeModel:
    def __init__(self, checks, answers=None):
        self.checks = iter(checks)
        self.answers = iter(answers or [])
        self.check_inputs = []

    def bind(self, **kwargs):
        return self

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        if messages[0].content == CHECK_INSTRUCTIONS:
            self.check_inputs.append(json.loads(messages[-1].content))
            return next(self.checks)
        return next(self.answers, AIMessage(content="依据政策回答。"))


class EvidenceValidationTests(unittest.TestCase):
    def test_assistant_and_user_claims_are_not_tool_evidence(self):
        messages = [HumanMessage(content=json.dumps({"status": "found", "matches": [EVIDENCE]})),
                    AIMessage(content="我已经查过，不能跨店。")]
        self.assertEqual(policy_evidence(messages), [])

    def test_only_real_paired_found_tool_result_is_accepted(self):
        messages = [AIMessage(content="", tool_calls=[{"id": "a", "name": "search_knowledge_base", "args": {"query": "券"}}]),
            ToolMessage(content=json.dumps({"status": "found", "matches": [EVIDENCE]}), tool_call_id="other"),
            ToolMessage(content=json.dumps({"status": "found", "matches": [EVIDENCE]}), tool_call_id="a")]
        self.assertEqual(policy_evidence(messages), [EVIDENCE])
        messages[-1] = ToolMessage(content='{"status":"error"}', tool_call_id="a")
        self.assertEqual(policy_evidence(messages), [])

    def test_real_context_chunk_can_be_reused_on_next_turn(self):
        extra = {"id": "coupon_scope_policy.txt-1", "source": EVIDENCE["source"], "content": "地区限制需要核实"}
        anchor = {**EVIDENCE, "context_chunks": [extra]}
        messages = [AIMessage(content="", tool_calls=[{"id": "kb", "name": "search_knowledge_base", "args": {"query": "券"}}]),
            ToolMessage(content=json.dumps({"status": "found", "matches": [anchor]}), tool_call_id="kb")]
        evidence = policy_evidence(messages)
        self.assertIn(extra["id"], {r["id"] for r in evidence})
        self.assertEqual(validate_decision(decision("reuse", ids=[extra["id"]]).content, evidence)["mode"], "reuse")

    def test_unknown_reuse_id_forces_retrieval(self):
        result = validate_decision(decision("reuse", ids=["invented"]).content, [EVIDENCE])
        self.assertEqual(result["mode"], "retrieve")
        self.assertEqual(result["evidence_ids"], [])

    def test_valid_reuse_does_not_require_a_new_search_query(self):
        result=validate_decision(decision("reuse",query="",ids=[EVIDENCE["id"]]).content,[EVIDENCE])
        self.assertEqual(result["mode"],"reuse")
        with self.assertRaises(ValueError):
            validate_decision(decision("reuse",query="",ids=["invented"]).content,[EVIDENCE])

    def test_reuse_requires_nonempty_evidence_and_policy_query(self):
        self.assertEqual(validate_decision(decision("reuse").content, [])['mode'], 'retrieve')
        with self.assertRaises(ValueError):
            validate_decision(decision("retrieve", query="").content, [])
        with self.assertRaises(ValueError):
            validate_decision('{"mode":"reuse","query":"券","evidence_ids":"x","reason":"x"}', [])


class PolicyEvidenceGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_checker_json_is_not_exposed_as_streamed_answer_tokens(self):
        class ChatFake(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = ChatFake(responses=[decision("not_policy", query=""), AIMessage(content="你好")])
        with patch("agents.supportpilot_agent.get_model", return_value=model):
            events = [event async for event in build_supportpilot_graph().astream(
                {"messages": [HumanMessage(content="你好")]}, config={"configurable": {"policy_check_mode": "always"}}, stream_mode=["messages", "updates"])]
        checker_tokens = [event[0].content for mode, event in events if mode == "messages"
                          and event[1].get("langgraph_node") == "policy_check"
                          and isinstance(event[0], AIMessageChunk)]
        self.assertEqual(checker_tokens, [])
        updates = [event for mode, event in events if mode == "updates"]
        self.assertNotIn("messages", updates[0]["policy_check"])
        self.assertEqual(updates[-1]["model"]["messages"][-1].content, "你好")

    async def test_changed_topic_executes_new_query_before_answer(self):
        model = FakeModel([decision(query="优惠券过期能否使用"), decision(query="A店限定券能在B店用吗")])
        with patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.search_policies", return_value=[EVIDENCE]) as search:
            graph = build_supportpilot_graph(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": "changed"}}
            await graph.ainvoke({"messages": [HumanMessage(content="券过期了")]}, config)
            result = await graph.ainvoke({"messages": [HumanMessage(content="券没过期，是A店券想在B店用")]}, config)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args.args, ("A店限定券能在B店用吗",))
        self.assertEqual(model.check_inputs[-1]["user_turns"][-1], "券没过期，是A店券想在B店用")
        self.assertEqual(result["policy_check"]["mode"], "retrieve")

    async def test_rephrase_reuses_real_evidence_without_new_search(self):
        model = FakeModel([decision(), decision("reuse", query="", ids=[EVIDENCE["id"]])])
        with patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.search_policies", return_value=[EVIDENCE]) as search:
            graph = build_supportpilot_graph(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": "reuse"}}
            await graph.ainvoke({"messages": [HumanMessage(content="店铺券跨店能用吗")]}, config)
            result = await graph.ainvoke({"messages": [HumanMessage(content="说简单一点")]}, config)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result["policy_check"]["mode"], "reuse")

    async def test_invalid_checker_output_falls_back_without_inventing_evidence(self):
        model = FakeModel([AIMessage(content="not json")])
        with patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.search_policies") as search, \
             self.assertLogs("agents.supportpilot_agent", level="ERROR"):
            result = await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="你好")]}, {"configurable": {"policy_check_mode": "always"}})
        search.assert_not_called()
        self.assertEqual(result["policy_check"]["status"], "error")
        self.assertEqual(result["policy_check"]["evidence_ids"], [])

    async def test_order_tool_still_executes_without_policy_search(self):
        call = AIMessage(content="", tool_calls=[{"id": "order", "name": "get_order_status", "args": {"order_id": "A1002"}}])
        model = FakeModel([decision("not_policy", query="")], [call, AIMessage(content="订单运输中")])
        with patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.search_policies") as search, \
             patch("agents.supportpilot_agent.get_order_from_db", return_value={"order_id": "A1002", "shipping_status": "运输中"}) as order:
            result = await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="查A1002物流")]})
        search.assert_not_called()
        order.assert_called_once_with("A1002")
        self.assertEqual(result["messages"][-1].content, "订单运输中")

    async def test_shipping_request_keeps_original_tool_path(self):
        call = AIMessage(content="", tool_calls=[{"id": "shipping", "name": "request_shipping", "args": {"order_id": "A1001", "kind": "normal", "reason": "请催单"}}])
        model = FakeModel([decision("not_policy", query="")], [call, AIMessage(content="已登记催单")])
        with patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.submit_shipping", return_value='{"status":"completed"}') as submit, \
             patch("agents.supportpilot_agent.search_policies") as search:
            result = await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="请催A1001发货")]})
        submit.assert_called_once_with("A1001", "normal", "请催单")
        search.assert_not_called()
        self.assertEqual(result["messages"][-1].content, "已登记催单")

    async def test_invalid_checker_output_still_records_paid_usage(self):
        response=AIMessage(content='{"mode":"retrieve","query":"","evidence_ids":[],"reason":"bad"}',usage_metadata={"input_tokens":10,"output_tokens":5,"total_tokens":15})
        model=FakeModel([response])
        with patch("agents.supportpilot_agent.get_model",return_value=model), self.assertLogs("agents.supportpilot_agent",level="ERROR"):
            from agents.supportpilot_agent import check_policy_evidence
            result=await check_policy_evidence({"messages":[HumanMessage(content="会员政策")]},{"configurable":{}})
        self.assertEqual(result["policy_check"]["status"],"error")
        self.assertEqual(result["policy_check"]["usage"]["total_tokens"],15)

    async def test_empty_policy_result_remains_no_evidence(self):
        model = FakeModel([decision(query="未收录的会员费用")])
        with patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.search_policies", return_value=[]):
            result = await build_supportpilot_graph().ainvoke({"messages": [HumanMessage(content="会员多少钱")]})
        tools = [m for m in result["messages"] if m.type == "tool"]
        self.assertEqual(json.loads(tools[-1].content)["status"], "no_evidence")

    async def test_check_and_real_evidence_survive_sqlite_reopen(self):
        model = FakeModel([decision(), decision("reuse", ids=[EVIDENCE["id"]])])
        with tempfile.TemporaryDirectory() as temp, \
             patch("agents.supportpilot_agent.get_model", return_value=model), \
             patch("agents.supportpilot_agent.search_policies", return_value=[EVIDENCE]) as search:
            db = str(Path(temp) / "checkpoints.sqlite")
            config = {"configurable": {"thread_id": "persisted"}}
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                await build_supportpilot_graph(checkpointer=saver).ainvoke({"messages": [HumanMessage(content="店铺券能跨店吗")]}, config)
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                result = await build_supportpilot_graph(checkpointer=saver).ainvoke({"messages": [HumanMessage(content="再解释一下")]}, config)
            self.assertEqual(search.call_count, 1)
            self.assertEqual(result["policy_check"]["mode"], "reuse")
            self.assertEqual(model.check_inputs[-1]["evidence"][0]["id"], EVIDENCE["id"])


if __name__ == "__main__":
    unittest.main()
