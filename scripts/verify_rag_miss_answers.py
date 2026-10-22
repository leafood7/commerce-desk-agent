"""三个已授权案例：固定证据生成对照和真实图验证，不调用模型裁判或业务写工具。"""
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from business.knowledge import build_chunks, search_policies
from agents.supportpilot_agent import TOOLS, build_supportpilot_graph
from core import get_model, settings
from evaluate_query_rewrite import extract_turn
from evaluate_rag_answers import ANSWER_PROMPT


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main():
    source = ROOT / 'work/rag-miss-fix-offline.json'
    offline = json.loads(source.read_text(encoding='utf-8'))
    for key, filename in [('context_sha256', 'src/business/policy_context.py'),
                          ('retriever_sha256', 'src/business/rag_retrieval.py')]:
        if sha(ROOT / filename) != offline['metadata'][key]:
            raise ValueError('源码已变化，需重新生成离线基线：' + filename)
    documents = {row['id']: row for row in build_chunks()}
    corpus_hash = hashlib.sha256(json.dumps(sorted((key, row['content']) for key, row in documents.items()), ensure_ascii=False).encode()).hexdigest()
    if corpus_hash != offline['metadata']['corpus_sha256']:
        raise ValueError('政策语料已变化')
    if os.getenv('SUPPORTPILOT_RETRIEVAL_STRATEGY') != 'hybrid_rerank':
        raise ValueError('验证固定hybrid_rerank')
    destination = ROOT / 'work/rag-miss-fix-answers-refined.json'
    if destination.exists():
        raise FileExistsError('已存在结果，拒绝覆盖；请审阅后显式移动旧结果再运行。')
    output = {'metadata': {
        'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
        'model_setting': str(settings.DEFAULT_MODEL), 'offline_report_sha256': sha(source),
        'offline_metadata': offline['metadata'],
        'agent_sha256': sha(ROOT / 'src/agents/supportpilot_agent.py'),
        'checker_sha256': sha(ROOT / 'src/agents/policy_evidence.py'),
        'guidance_sha256': sha(ROOT / 'src/agents/answer_policy.py'),
        'answer_prompt_sha256': hashlib.sha256(ANSWER_PROMPT.encode()).hexdigest(),
        'script_sha256': sha(Path(__file__)),
        'pool_size': int(os.getenv('SUPPORTPILOT_RAG_POOL_SIZE', '12')),
        'threshold': float(os.getenv('SUPPORTPILOT_RERANK_THRESHOLD', '0')),
        'method': '固定查询同提示、相同原文锚点、交替生成顺序、temperature=0；修复后真实图独立内存会话。无额外模型裁判，单次样本。',
        'limitations': '三条已知失败案例不是留出集。固定生成前后对照不等于真实图前后对照。真实图使用生产模型设置。耗时包含接口波动，非性能基准；usage来自接口，不换算价格。'},
        'controlled': [], 'dialogues': []}
    def save():
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    model = get_model(settings.DEFAULT_MODEL).bind(temperature=0, stream=False)
    for index, target in enumerate(offline['targets']):
        row = {'id': target['id'], 'query': target['query'], 'evidence_groups': target['evidence_groups']}
        for variant in (['before', 'after'] if index % 2 == 0 else ['after', 'before']):
            evidence = [documents[key] for key in target[variant + '_ids']]
            started = perf_counter()
            response = await model.ainvoke([SystemMessage(content=ANSWER_PROMPT), HumanMessage(content=json.dumps(
                {'question': target['query'], 'evidence': evidence}, ensure_ascii=False))])
            row[variant] = {'answer': response.content, 'evidence': evidence,
                'usage': response.usage_metadata or {}, 'generation_ms': (perf_counter() - started) * 1000,
                'model': response.response_metadata.get('model_name')}
            print('controlled ' + target['id'] + ' ' + variant, flush=True)
        output['controlled'].append(row)
        save()
    # 串行加载本地模型和索引；实际业务工具一律阻断。
    await asyncio.to_thread(search_policies, '商城政策')
    def blocked(**kwargs):
        return json.dumps({'status': 'blocked_evaluation', 'message': '评测不访问订单或提交业务。'}, ensure_ascii=False)
    safe_tools = [tool if tool.name == 'search_knowledge_base' else tool.model_copy(update={'func': blocked}) for tool in TOOLS]
    graph = build_supportpilot_graph(tools=safe_tools, checkpointer=InMemorySaver())
    cases = []
    for filename, selected in [('query_rewrite_cases.jsonl', {'rewrite-01', 'rewrite-16'}),
                                ('policy_refresh_holdout.jsonl', {'refresh-new-02'})]:
        for line in (ROOT / 'data/evaluation' / filename).read_text(encoding='utf-8-sig').splitlines():
            if line.strip():
                case = json.loads(line)
                if case['id'] in selected:
                    cases.append(case)
    if len(cases) != 3:
        raise ValueError('三个真实对话案例不完整')
    for case in cases:
        config = {'configurable': {'thread_id': uuid4().hex}, 'recursion_limit': 20}
        turns = []
        for question in case['turns']:
            started = perf_counter()
            result = await graph.ainvoke({'messages': [HumanMessage(content=question)]}, config)
            messages = result['messages']
            start = max(i for i, message in enumerate(messages) if message.type == 'human')
            current = messages[start:]
            trace = extract_turn(current)
            turns.append({'question': question, 'answer': messages[-1].content,
                'policy_check': result.get('policy_check', {}), **trace,
                'answer_model_calls': [{'usage': message.usage_metadata or {},
                    'model': message.response_metadata.get('model_name')} for message in current
                    if message.type == 'ai' and not message.additional_kwargs.get('policy_preflight')],
                'elapsed_ms': (perf_counter() - started) * 1000})
            print('dialogue ' + case['id'] + ' turn ' + str(len(turns)), flush=True)
        ids = {row['id'] for turn in turns for row in turn['matches']}
        last_ids = {row['id'] for row in turns[-1]['matches']}
        output['dialogues'].append({'case': case, 'turns': turns,
            'union_evidence_complete': all(bool(ids & set(group)) for group in case['evidence_groups']),
            'last_turn_evidence_complete': all(bool(last_ids & set(group)) for group in case['evidence_groups'])})
        save()
    output['metadata']['status'] = 'complete'
    output['metadata']['completed_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print('Saved ' + str(destination), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
