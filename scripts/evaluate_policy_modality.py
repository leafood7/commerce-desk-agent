"""固定证据下比较共享回答约束；默认只校验，--online 才调用模型。"""
import argparse
import ast
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / 'data/evaluation/policy_modality_cases.json'
BASELINE = ROOT / 'data/evaluation/baselines/policy_modality/answer_policy.py'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def literal_assignment(path, variable):
    """只读取字符串字面量，不执行冻结源码。"""
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == variable for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, str):
                raise ValueError(f'{variable}不是字符串')
            return value
    raise ValueError(f'未找到{variable}')


def policy_documents(directory):
    documents = {}
    for path in sorted(directory.glob('*.txt')):
        lines = [line.strip() for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        for index, text in enumerate(lines[1:]):
            documents[f'{path.name}-{index}'] = {
                'source': path.name, 'content': lines[0] + '\n\n' + text,
            }
    return documents


def load_cases(path=DEFAULT_CASES, knowledge=None):
    cases = json.loads(path.read_text(encoding='utf-8-sig'))
    documents = policy_documents(knowledge or ROOT / 'data/knowledge')
    seen = set()
    if not isinstance(cases, list) or not cases:
        raise ValueError('题集必须为非空数组')
    for case in cases:
        if not case.get('id') or case['id'] in seen:
            raise ValueError('题目ID为空或重复')
        seen.add(case['id'])
        if not case.get('question') or not case.get('expected') or not case.get('evidence'):
            raise ValueError('缺少问题、预期行为或固定证据')
        for evidence in case['evidence']:
            original = documents.get(evidence.get('id'))
            if not original or any(evidence.get(key) != original[key] for key in ['source', 'content']):
                raise ValueError('证据ID、来源或原文不匹配：' + str(evidence.get('id')))
    return cases


def paired_prompts(root=ROOT):
    template = literal_assignment(root / 'scripts/evaluate_rag_answers.py', 'ANSWER_PROMPT')
    before = literal_assignment(root / 'data/evaluation/baselines/policy_modality/answer_policy.py', 'ANSWER_GUIDANCE')
    after = literal_assignment(root / 'src/agents/answer_policy.py', 'ANSWER_GUIDANCE')
    return {'before': template + '\n' + before, 'after': template + '\n' + after}


def select_cases(cases, ids):
    if not ids:
        return cases
    selected = set(ids.split(','))
    if not selected <= {case['id'] for case in cases}:
        raise ValueError('指定了不存在的题目ID')
    return [case for case in cases if case['id'] in selected]


async def run_online(args, cases):
    if not args.output:
        raise ValueError('在线运行必须指定新的--output文件')
    destination = Path(args.output).resolve()
    if destination.exists():
        raise FileExistsError('拒绝覆盖已有结果')
    sys.path.insert(0, str(ROOT / 'src'))
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    from langchain_core.messages import HumanMessage, SystemMessage
    from core import get_model, settings
    model = get_model(settings.DEFAULT_MODEL).bind(temperature=0, stream=False)
    prompts = paired_prompts()
    output = {'metadata': {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'model_setting': str(settings.DEFAULT_MODEL),
        'dataset_sha256': sha(Path(args.cases)),
        'baseline_sha256': sha(BASELINE),
        'guidance_sha256': sha(ROOT / 'src/agents/answer_policy.py'),
        'prompt_hashes': {arm: hashlib.sha256(text.encode()).hexdigest() for arm, text in prompts.items()},
        'method': '固定相同证据与基础模板，只改变共享回答约束，交替顺序，temperature=0，无额外裁判调用。',
        'limitations': '已知合成回归题，不是独立盲测；单次运行，模型SDK可能按自身配置重试。',
    }, 'rows': []}
    destination.parent.mkdir(parents=True, exist_ok=True)

    def save():
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    for index, case in enumerate(cases):
        row = {'id': case['id'], 'answers': {}}
        output['rows'].append(row)
        user = HumanMessage(content=json.dumps({'question': case['question'], 'evidence': case['evidence']}, ensure_ascii=False))
        for arm in (['before', 'after'] if index % 2 == 0 else ['after', 'before']):
            started = perf_counter()
            try:
                response = await model.ainvoke([SystemMessage(content=prompts[arm]), user])
                row['answers'][arm] = {
                    'answer': response.content, 'usage': response.usage_metadata or {},
                    'model': response.response_metadata.get('model_name'),
                    'elapsed_ms': (perf_counter() - started) * 1000,
                }
            except Exception as exc:
                row['answers'][arm] = {'error_type': type(exc).__name__, 'usage_unknown': True,
                                        'elapsed_ms': (perf_counter() - started) * 1000}
            save()
            print(case['id'], arm, 'done', flush=True)
    output['metadata']['completed_at'] = datetime.now(timezone.utc).isoformat()
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default=str(DEFAULT_CASES))
    parser.add_argument('--ids', default='')
    parser.add_argument('--online', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    cases = select_cases(load_cases(Path(args.cases)), args.ids)
    if not args.online:
        print(json.dumps({'validated_cases': len(cases), 'model_calls': 0}, ensure_ascii=False))
        return
    asyncio.run(run_online(args, cases))


if __name__ == '__main__':
    main()
