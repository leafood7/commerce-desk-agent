import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from business.policy_context import flatten_policy_matches
from tool_protocol.client import readonly_mcp_tools, server_connection, text_result
from agents.policy_evidence import CHECK_INSTRUCTIONS, policy_evidence
from agents.supportpilot_agent import build_supportpilot_graph, request_shipping
from agents.mcp_supportpilot import MCPSupportPilotAgent

ROOT = Path(__file__).resolve().parents[1]


def fixture_connection():
    return server_connection({"args": [str(ROOT / "tests/fixtures/mcp_readonly_server.py")]})


def decision(mode="retrieve", query="店铺券跨店", ids=None):
    return AIMessage(content=json.dumps({"mode": mode, "query": query, "evidence_ids": ids or [], "reason": "协议回归"}, ensure_ascii=False))


class FakeModel:
    def __init__(self, checks=(), answers=()):
        self.checks, self.answers = iter(checks), iter(answers)
        self.bound, self.check_inputs = [], []

    def bind(self, **kwargs):
        return self

    def bind_tools(self, tools):
        self.bound = tools
        return self

    async def ainvoke(self, messages, config=None):
        if messages[0].content == CHECK_INSTRUCTIONS:
            self.check_inputs.append(json.loads(messages[-1].content))
            return next(self.checks)
        return next(self.answers, AIMessage(content="依据实际工具证据回答。"))


class MCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_sdk_discovers_only_readonly_tools_and_validates_parameters(self):
        c = fixture_connection()
        params = StdioServerParameters(command=c['command'], args=c['args'], env=c['env'], cwd=c['cwd'])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            self.assertEqual({t.name for t in tools}, {'get_order_status', 'search_knowledge_base'})
            self.assertTrue(all(t.annotations.readOnlyHint for t in tools))
            order_schema = next(t.inputSchema for t in tools if t.name == 'get_order_status')
            self.assertEqual(order_schema['required'], ['order_id'])
            result = await session.call_tool('get_order_status', {'order_id': ' a1002 '})
            self.assertFalse(result.isError)
            self.assertEqual(json.loads(result.content[0].text)['order_id'], 'A1002')
            for name, args in [('get_order_status', {}), ('get_order_status', {'order_id': 1002}),
                               ('search_knowledge_base', {'query': '   '}), ('search_knowledge_base', {'query': 'x'*1201})]:
                with self.subTest(name=name, args=str(args)[:60]):
                    self.assertTrue((await session.call_tool(name, args)).isError)
            unknown = await session.call_tool('get_order_status', {'order_id': 'A9999'})
            self.assertIn('未找到', unknown.content[0].text)
            bad = await session.call_tool('get_order_status', {'order_id': 'A1002 OR 1=1'})
            self.assertIn('格式不正确', bad.content[0].text)

    async def test_large_unicode_policy_response_is_not_truncated(self):
        from datetime import timedelta
        c = fixture_connection()
        params = StdioServerParameters(command=c['command'], args=c['args'], env=c['env'], cwd=c['cwd'])
        async with stdio_client(params) as (read, write), ClientSession(read, write, read_timeout_seconds=timedelta(seconds=3)) as session:
            await session.initialize()
            result = await session.call_tool('search_knowledge_base', {'query': 'large'})
            self.assertFalse(result.isError)
            self.assertEqual(json.loads(result.content[0].text)['matches'][0]['content'], '中文政策限制' * 4000)

    async def test_adapter_preserves_status_source_context_and_concurrent_calls(self):
        async with readonly_mcp_tools(fixture_connection()) as tools:
            by_name = {t.name: t for t in tools}
            order, policy = await asyncio.gather(by_name['get_order_status'].ainvoke({'order_id': 'A1002'}),
                by_name['search_knowledge_base'].ainvoke({'query': '店铺券'}))
            self.assertEqual(json.loads(order)['product_name'], '协议测试商品')
            payload = json.loads(policy)
            self.assertEqual(payload['status'], 'found')
            evidence = flatten_policy_matches(payload['matches'])
            self.assertEqual({x['id'] for x in evidence}, {'coupon_scope_policy.txt-0', 'coupon_scope_policy.txt-1'})
            self.assertEqual(evidence[0]['rerank_score'], 3.5)
            self.assertTrue(all(x['source'] == 'coupon_scope_policy.txt' for x in evidence))
            self.assertEqual(json.loads(await by_name['search_knowledge_base'].ainvoke({'query': 'no_evidence'}))['status'], 'no_evidence')
            self.assertEqual(json.loads(await by_name['search_knowledge_base'].ainvoke({'query': 'error'}))['status'], 'error')
            with self.assertLogs('tool_protocol.client', level='ERROR'):
                invalid = await by_name['get_order_status'].ainvoke({'order_id': 1002})
            self.assertEqual(json.loads(invalid)['status'], 'error')
        with self.assertLogs('tool_protocol.client', level='ERROR'):
            failure = await by_name['search_knowledge_base'].ainvoke({'query': '店铺券'})
        self.assertEqual(json.loads(failure)['status'], 'error')
        self.assertEqual(json.loads(failure)['matches'], [])

    async def test_real_mcp_graph_reuses_evidence_and_retrieves_after_change(self):
        model = FakeModel([decision(), decision('reuse', ids=['coupon_scope_policy.txt-1']), decision(query='no_evidence')])
        async with readonly_mcp_tools(fixture_connection()) as tools, asyncio.timeout(30):
            with patch('agents.supportpilot_agent.get_model', return_value=model):
                graph = build_supportpilot_graph(tools=[*tools, request_shipping], checkpointer=InMemorySaver())
                config = {'configurable': {'thread_id': 'mcp-policy'}}
                first = await graph.ainvoke({'messages': [HumanMessage(content='店铺券能跨店吗')]}, config)
                second = await graph.ainvoke({'messages': [HumanMessage(content='解释一下地区限制')]}, config)
                third = await graph.ainvoke({'messages': [HumanMessage(content='换个没有依据的问题')]}, config)
        self.assertEqual({t.name for t in model.bound}, {'get_order_status', 'search_knowledge_base', 'request_shipping'})
        self.assertEqual({t.metadata['transport'] for t in model.bound if t.name != 'request_shipping'}, {'mcp'})
        self.assertEqual(len([m for m in second['messages'] if m.type == 'tool']), 1)
        self.assertIn('coupon_scope_policy.txt-1', {x['id'] for x in policy_evidence(first['messages'])})
        self.assertEqual(second['policy_check']['mode'], 'reuse')
        self.assertEqual(json.loads(third['messages'][-2].content)['status'], 'no_evidence')
        self.assertEqual(model.check_inputs[-1]['user_turns'][-1], '换个没有依据的问题')

    async def test_order_selection_uses_remote_tool_and_shipping_stays_local(self):
        calls = [AIMessage(content='', tool_calls=[{'id': 'o', 'name': 'get_order_status', 'args': {'order_id': 'A1002'}}]), AIMessage(content='已查询'),
                 AIMessage(content='', tool_calls=[{'id': 's', 'name': 'request_shipping', 'args': {'order_id': 'A1004', 'kind': 'expedite'}}]), AIMessage(content='等待审批')]
        model = FakeModel([decision('not_policy', query='')], calls)
        async with readonly_mcp_tools(fixture_connection()) as tools:
            with patch('agents.supportpilot_agent.get_model', return_value=model), patch('agents.supportpilot_agent.submit_shipping', return_value='{"status":"pending"}') as submit:
                graph = build_supportpilot_graph(tools=[*tools, request_shipping])
                order = await graph.ainvoke({'messages': [HumanMessage(content='查A1002物流')]})
                shipping = await graph.ainvoke({'messages': [HumanMessage(content='申请A1004加急发货')]})
        self.assertEqual(json.loads(order['messages'][-2].content)['product_name'], '协议测试商品')
        submit.assert_called_once()
        self.assertEqual(json.loads(shipping['messages'][-2].content)['status'], 'pending')

    async def test_lazy_agent_load_close_and_model_binding(self):
        agent = MCPSupportPilotAgent(fixture_connection())
        await agent.load()
        try:
            graph = agent.get_graph()
            await agent.load()
            self.assertIs(graph, agent.get_graph())
            model = FakeModel(answers=[AIMessage(content='', tool_calls=[{'id': 'o', 'name': 'get_order_status', 'args': {'order_id': 'A1002'}}]), AIMessage(content='查询完成')])
            with patch('agents.supportpilot_agent.get_model', return_value=model):
                result = await graph.ainvoke({'messages': [HumanMessage(content='查A1002物流')]})
            self.assertEqual(json.loads(result['messages'][-2].content)['order_id'], 'A1002')
        finally:
            await agent.aclose()
        self.assertFalse(agent._loaded)
        with self.assertRaises(RuntimeError):
            agent.get_graph()
        await agent.aclose()

    async def test_failed_startup_is_not_silently_replaced_by_local_tools(self):
        connection = fixture_connection()
        connection['args'] = ['-c', 'raise SystemExit(3)']
        agent = MCPSupportPilotAgent(connection)
        with patch('agents.supportpilot_agent.search_policies') as local_search:
            with self.assertRaises(Exception):
                await agent.load()
        local_search.assert_not_called()
        self.assertFalse(agent._loaded)
        await agent.aclose()

    async def test_fastapi_lifespan_closes_mcp_agent(self):
        from agents.agents import agents
        from service import service
        from langgraph.store.memory import InMemoryStore
        @asynccontextmanager
        async def database():
            yield InMemorySaver()
        @asynccontextmanager
        async def store():
            yield InMemoryStore()
        agent = MCPSupportPilotAgent(fixture_connection())
        with patch.object(agents['supportpilot'], 'graph_like', agent), patch.object(service, 'initialize_database', database), patch.object(service, 'initialize_store', store):
            async with service.lifespan(service.app):
                self.assertTrue(agent._loaded)
                self.assertIsNotNone(agent.get_graph().checkpointer)
            self.assertFalse(agent._loaded)

    def test_non_text_response_is_rejected_and_credentials_not_forwarded(self):
        with self.assertRaises(ValueError):
            text_result([{'type': 'image', 'data': 'not-a-tool-response'}])
        with patch.dict('os.environ', {'COMPATIBLE_API_KEY': 'secret-test', 'OPENAI_API_KEY': 'secret-test'}):
            env = server_connection()['env']
        self.assertNotIn('COMPATIBLE_API_KEY', env)
        self.assertNotIn('OPENAI_API_KEY', env)


class MCPConfigurationTests(unittest.TestCase):
    def test_timeout_configuration_is_positive_and_finite(self):
        for value in ['0', '-1', 'nan', 'inf']:
            with self.subTest(value=value), patch.dict('os.environ', {'SUPPORTPILOT_MCP_TIMEOUT': value}):
                with self.assertRaises(ValueError):
                    server_connection()

    def test_explicit_config_and_environment_match(self):
        with patch.dict('os.environ', {'SUPPORTPILOT_DB_PATH': 'isolated/test.sqlite3', 'SUPPORTPILOT_MCP_TIMEOUT': '12'}):
            connection = server_connection()
        self.assertEqual(connection['env']['SUPPORTPILOT_DB_PATH'], 'isolated/test.sqlite3')
        self.assertEqual(connection['session_kwargs']['read_timeout_seconds'].total_seconds(), 12)
