"""原生SDK独立验证生产MCP Server，无Agent/LLM；本地基线在独立短进程执行。"""
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from statistics import median
from time import perf_counter
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client, get_default_environment
from business.order_repository import initialize_demo_orders, get_order_from_db
from business.readonly_tools import order_result
from business.policy_context import flatten_policy_matches
from tool_protocol.client import server_connection


def params_for(c):
    return StdioServerParameters(**{k: c[k] for k in ['command', 'args', 'env', 'cwd']})


def canonical(payload):
    return {'status': payload['status'], 'evidence': sorted((x['id'], x['source'], x['content'])
        for x in flatten_policy_matches(payload.get('matches', [])))}


async def main(args):
    (ROOT / 'work').mkdir(exist_ok=True)
    report = {'transport': 'stdio', 'raw_sdk_client': True, 'external_llm_calls': 0, 'isolated_demo_database': True,
              'isolated_index_copies': True, 'local_baseline_separate_process': True,
              'windows_selector_loop': args.selector_loop, 'checks': {}, 'timing_ms': {}, 'status': 'running'}
    def save_failure(error):
        report['status'], report['error'] = 'failed', repr(error)
        (ROOT / 'work/mcp-last-failure.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='mcp-validation-', dir=ROOT / 'work') as directory:
        database = Path(directory) / 'orders.sqlite3'
        initialize_demo_orders(database)
        connection = server_connection()
        connection['env']['SUPPORTPILOT_DB_PATH'] = str(database)
        connection['env'].update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
        source = Path(connection['env'].get('SUPPORTPILOT_CHROMA_PATH', 'data/supportpilot/chroma'))
        source = source if source.is_absolute() else ROOT / source
        local_index, remote_index = Path(directory) / 'index-local', Path(directory) / 'index-mcp'
        shutil.copytree(source, local_index)
        shutil.copytree(source, remote_index)
        local_env = {**get_default_environment(), **connection['env'], 'SUPPORTPILOT_CHROMA_PATH': str(local_index)}
        baseline_file = Path(directory) / 'baseline.json'
        report['phase'] = 'local_baseline_subprocess'
        print('MCP verification: isolated local baseline', file=sys.stderr, flush=True)
        await asyncio.to_thread(subprocess.run, [sys.executable, str(ROOT / 'scripts/mcp_local_baseline.py'), '--output', str(baseline_file)],
            cwd=ROOT, env=local_env, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        baseline = json.loads(baseline_file.read_text(encoding='utf-8'))
        report['timing_ms']['local_policy_cold'] = baseline['cold_ms']
        report['timing_ms']['local_policy_warm'] = {'samples': baseline['warm_ms'], 'median': median(baseline['warm_ms'])}
        connection['env']['SUPPORTPILOT_CHROMA_PATH'] = str(remote_index)
        started = perf_counter()
        async with stdio_client(params_for(connection)) as (read, write), ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
            try:
                report['phase'] = 'initialize'
                await session.initialize()
                tools = (await session.list_tools()).tools
                report['timing_ms']['startup_and_discovery'] = (perf_counter() - started) * 1000
                report['tools'] = [t.model_dump(mode='json', exclude_none=True) for t in tools]
                assert {t.name for t in tools} == {'get_order_status', 'search_knowledge_base'} and all(t.annotations.readOnlyHint for t in tools)
                report['checks']['only_two_readonly_tools'] = True
                direct = order_result('A1002', lookup=lambda key: get_order_from_db(key, database))
                response = await session.call_tool('get_order_status', {'order_id': ' a1002 '})
                assert not response.isError and json.loads(response.content[0].text) == json.loads(direct)
                report['checks']['order_payload_equal'] = True
                assert '未找到' in (await session.call_tool('get_order_status', {'order_id': 'A9999'})).content[0].text
                report['checks']['unknown_order'] = True
                for name, arguments in [('get_order_status', {}), ('get_order_status', {'order_id': 1002}), ('search_knowledge_base', {'query': ' '})]:
                    assert (await session.call_tool(name, arguments)).isError
                report['checks']['invalid_arguments_rejected'] = True
                query = '金卡会员未标注延长退货，是否适用15天？'
                report['phase'] = 'policy_mcp_cold'
                print('MCP verification: remote policy cold', file=sys.stderr, flush=True)
                started = perf_counter()
                response = await session.call_tool('search_knowledge_base', {'query': query})
                report['timing_ms']['mcp_policy_cold'] = (perf_counter() - started) * 1000
                payload = json.loads(response.content[0].text)
                assert not response.isError and canonical(payload) == canonical(baseline['payload']), 'local/MCP policy mismatch'
                ids = {x['id'] for x in flatten_policy_matches(payload['matches'])}
                assert {'membership_policy.txt-1', 'membership_policy.txt-2'} <= ids
                report['checks']['policy_ids_source_content_and_context_equal'] = True
                report['checks']['gold_required_evidence_present'] = True
                report['policy_evidence_ids'] = sorted(ids)
                local_order, rpc_order, rpc_policy = [], [], []
                print('MCP verification: warm calls', file=sys.stderr, flush=True)
                report['phase'] = 'warm_calls'
                for _ in range(3):
                    started = perf_counter()
                    order_result('A1002', lookup=lambda key: get_order_from_db(key, database))
                    local_order.append((perf_counter()-started)*1000)
                    started = perf_counter()
                    await session.call_tool('get_order_status', {'order_id': 'A1002'})
                    rpc_order.append((perf_counter()-started)*1000)
                    started = perf_counter()
                    await session.call_tool('search_knowledge_base', {'query': query})
                    rpc_policy.append((perf_counter()-started)*1000)
                for key, values in [('local_order_warm', local_order), ('mcp_order_warm', rpc_order), ('mcp_policy_warm', rpc_policy)]:
                    report['timing_ms'][key] = {'samples': values, 'median': median(values)}
            except Exception as error:
                save_failure(error)
                print('MCP original error: ' + repr(error), file=sys.stderr, flush=True)
                raise
        missing = server_connection()
        missing['env'].update({'SUPPORTPILOT_DB_PATH': str(database), 'SUPPORTPILOT_CHROMA_PATH': str(Path(directory) / 'missing-index')})
        async with stdio_client(params_for(missing)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool('search_knowledge_base', {'query': '会员退货'})
            assert not result.isError and json.loads(result.content[0].text)['status'] == 'error'
            report['checks']['missing_index_is_domain_error_not_found'] = True
    assert 'agents' not in sys.modules and 'torch' not in sys.modules
    report['checks']['no_agent_or_torch_import_in_client'] = True
    report['status'] = 'complete'
    report['limitations'] = '本机3个热样本，非并发压测/SLA；对照使用同源隔离索引副本，本地基线进程先退出再启动MCP；不含外部LLM或网页点击测试。'
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'checks': report['checks'], 'timing_ms': report['timing_ms']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='work/mcp-independent-validation.json')
    parser.add_argument('--selector-loop', action='store_true')
    args = parser.parse_args()
    if args.selector_loop and sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args))
