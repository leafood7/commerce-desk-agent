"""合成题配对评测。默认仅检查冻结文件；外部调用必须显式传--online。"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ.setdefault('HF_HUB_OFFLINE','1')
os.environ.setdefault('TRANSFORMERS_OFFLINE','1')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline_path(filename):
    local=ROOT/filename
    return local if local.exists() else ROOT/"data/evaluation/baselines/answer_quality"/Path(filename).name


def read_cases():
    manifest=json.loads(baseline_path('work/answer-quality-baseline/manifest.json').read_text(encoding='utf-8'))
    for filename, expected in manifest.items():
        if sha(baseline_path(filename))!=expected:
            raise ValueError('冻结文件变化：'+filename)
    cases=[json.loads(line) for line in (ROOT/'data/evaluation/answer_quality_cases.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if len(cases)!=18 or len({c['id'] for c in cases})!=18:
        raise ValueError('题集不完整或ID重复')
    return manifest,cases


def variant_cost(variant):
    turns=variant["turns"]
    checks=[t.get("policy_check",{}) for t in turns]
    actual_checks=[d for d in checks if d.get("checker_called",bool(d) and d.get("status")!="skipped")]
    calls=[c for t in turns for c in t.get("answer_calls",[])]
    usages=[d.get("usage",{}) for d in actual_checks]+[c.get("usage",{}) for c in calls]
    return {"turns":len(turns),"checker_calls":len(actual_checks),"answer_calls":len(calls),
        "searches":sum(len(t.get("queries",[])) for t in turns),
        "tool_errors":sum(len(t.get("tool_errors",[])) for t in turns),
        "blocked_business_calls":sum(c.get("name")=="request_shipping" for t in turns for c in t.get("other_tools",[])),
        "missing_usage_calls":sum(not u for u in usages),
        "input_tokens_reported":sum(u.get("input_tokens",0) for u in usages),
        "output_tokens_reported":sum(u.get("output_tokens",0) for u in usages),
        "total_tokens_reported":sum(u.get("total_tokens",0) for u in usages),
        "elapsed_ms":sum(t["elapsed_ms"] for t in turns)}


def cost_summary(report):
    def combine(variants):
        costs=[variant_cost(v) for v in variants]
        return {key:sum(row[key] for row in costs) for key in costs[0]} if costs else {}
    summary={"variants":{name:{"cases":sum(name in row["variants"] for row in report["rows"]),
        **combine([row["variants"][name] for row in report["rows"] if name in row["variants"]])}
        for name in ["before","after","after_always"]}}
    paired=[row for row in report["rows"] if "after_always" in row["variants"] and "after" in row["variants"]]
    summary["same_prompt_cost_subset"]={"case_ids":[row["case"]["id"] for row in paired],
        "auto":combine([row["variants"]["after"] for row in paired]),
        "always":combine([row["variants"]["after_always"] for row in paired])}
    clean=[row for row in paired if all(
        not variant_cost(row["variants"][name])["missing_usage_calls"]
        and not any(t.get("policy_check",{}).get("status")=="error" or t.get("tool_errors") for t in row["variants"][name]["turns"])
        for name in ["after","after_always"])]
    summary["clean_same_prompt_cost_subset"]={"case_ids":[row["case"]["id"] for row in clean],
        "excluded_ids":[row["case"]["id"] for row in paired if row not in clean],
        "auto":combine([row["variants"]["after"] for row in clean]),
        "always":combine([row["variants"]["after_always"] for row in clean])}
    return summary


async def run(args,manifest,cases):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from agents import supportpilot_agent as live
    from business.knowledge import search_policies,build_chunks
    from core import get_model,settings
    from evaluate_query_rewrite import extract_turn
    from evaluate_rag_answers import ANSWER_PROMPT
    def frozen(name,path):
        module=types.ModuleType(name)
        module.__file__=str(ROOT/("scripts" if path.name.startswith("evaluate_") else "src/agents")/path.name)
        exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
        return module
    old=frozen('answer_quality_before',baseline_path('work/answer-quality-baseline/supportpilot_agent.py'))
    old_answers=frozen('answer_prompt_before',baseline_path('work/answer-quality-baseline/evaluate_rag_answers.py'))
    fixture={'order_id':'A1002','product_name':'评测机械键盘','payment_status':'已付款','shipping_status':'运输中',
        'estimated_delivery':'2026-10-25','hours_since_payment':24,'paid_at':'2026-10-21T00:00:00+00:00','promised_ship_at':'2026-10-23T00:00:00+00:00'}
    original=live.get_order_from_db
    old.get_order_from_db=lambda key: dict(fixture) if key=='A1002' else None
    live.get_order_from_db=old.get_order_from_db
    def blocked(**kwargs):
        return json.dumps({'status':'blocked_evaluation','message':'评测不提交业务。'},ensure_ascii=False)
    def safe(module):
        return [t.model_copy(update={'func':blocked}) if t.name=='request_shipping' else t for t in module.TOOLS]
    graphs={name:module.build_supportpilot_graph(tools=safe(module),checkpointer=InMemorySaver())
        for name,module in [('before',old),('after',live),('after_always',live)]}
    meta={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'model_setting':str(settings.DEFAULT_MODEL),
        'baseline_manifest':manifest,'agent_sha256':sha(ROOT/'src/agents/supportpilot_agent.py'),
        'checker_sha256':sha(ROOT/'src/agents/policy_evidence.py'),'guidance_sha256':sha(ROOT/'src/agents/answer_policy.py'),
        'corpus_sha256':hashlib.sha256(json.dumps(build_chunks(),ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
        'retriever_sha256':sha(ROOT/'src/business/rag_retrieval.py'),
        'strategy':os.getenv('SUPPORTPILOT_RETRIEVAL_STRATEGY'),'pool_size':os.getenv('SUPPORTPILOT_RAG_POOL_SIZE'),
        'threshold':os.getenv('SUPPORTPILOT_RERANK_THRESHOLD'),
        'method':'18题修改前冻结，3已知+15新增；旧/新图独立内存会话、逐题交替顺序、串行运行；新版auto/always单因素成本对照。订单只用合成fixture，业务写入阻断，无额外裁判调用。',
        'limitations':'内部小样本、单次采样、非外部盲测；耗时受缓存网络影响。本助手复核不等于独立人工金标准。',
        'phase':'final_regression' if args.final_only else 'paired',
        'final_only':args.final_only}
    destination=ROOT/args.output
    output={'metadata':meta,'rows':[],'controlled':[]}
    if destination.exists():
        if not args.resume:
            raise FileExistsError('已有结果；须显式--resume且哈希一致。')
        previous=json.loads(destination.read_text(encoding='utf-8'))
        for key in ['model_setting','baseline_manifest','agent_sha256','checker_sha256','guidance_sha256','corpus_sha256','retriever_sha256','strategy','pool_size','threshold','final_only']:
            if previous['metadata'][key]!=meta[key]:
                raise ValueError('续跑配置变化：'+key)
        output=previous
    def save():
        destination.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    await asyncio.to_thread(search_policies,'商城政策')
    async def dialogue(case,variant):
        config={'configurable':{'thread_id':uuid4().hex,'policy_check_mode':'auto' if variant=='after' else 'always'},'recursion_limit':20}
        length,turns=0,[]
        for question in case['turns']:
            started=perf_counter()
            result=await graphs[variant].ainvoke({'messages':[HumanMessage(content=question)]},config)
            messages=result['messages'][length:]
            turns.append({'question':question,'answer':result['messages'][-1].content,'policy_check':result.get('policy_check',{}),
                **extract_turn(messages),'answer_calls':[{'usage':m.usage_metadata or {},'model':m.response_metadata.get('model_name')}
                    for m in messages if m.type=='ai' and not m.additional_kwargs.get('policy_preflight')],
                'elapsed_ms':(perf_counter()-started)*1000})
            length=len(result['messages'])
        return {'turns':turns}
    try:
        for index,case in enumerate(cases):
            row=next((r for r in output['rows'] if r['case']['id']==case['id']),None)
            if row is None:
                row={'case':case,'variants':{}}
                output['rows'].append(row)
            order=['after'] if args.final_only else (['before','after'] if index%2==0 else ['after','before'])
            if case['group'] in {'greeting','order','repeat'}:
                order.append('after_always')
            for variant in order:
                if variant not in row['variants']:
                    try:
                        row['variants'][variant]=await dialogue(case,variant)
                    except Exception as error:
                        output.setdefault('attempt_failures',[]).append({'case_id':case['id'],'variant':variant,
                            'error_type':type(error).__name__,'usage_unknown':True})
                        save()
                        raise
                    save()
                    print(case['id']+' '+variant,flush=True)
        offline=json.loads((ROOT/'work/rag-miss-fix-offline.json').read_text(encoding='utf-8'))
        documents={r['id']:r for r in build_chunks()}
        model=get_model(settings.DEFAULT_MODEL).bind(temperature=0,stream=False)
        for index,case in enumerate(offline['targets']):
            row=next((r for r in output['controlled'] if r['id']==case['id']),None)
            if row is None:
                row={'id':case['id'],'question':case['query'],'evidence':[documents[key] for key in case['after_ids']],'variants':{}}
                output['controlled'].append(row)
            for variant in (['after'] if args.final_only else (['before','after'] if index%2==0 else ['after','before'])):
                if variant in row['variants']:
                    continue
                prompt=old_answers.ANSWER_PROMPT if variant=='before' else ANSWER_PROMPT
                started=perf_counter()
                response=await model.ainvoke([SystemMessage(content=prompt),HumanMessage(content=json.dumps({'question':row['question'],'evidence':row['evidence']},ensure_ascii=False))])
                row['variants'][variant]={'answer':response.content,'usage':response.usage_metadata or {},'elapsed_ms':(perf_counter()-started)*1000,
                    'model':response.response_metadata.get('model_name'),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
                save()
                print('controlled '+case['id']+' '+variant,flush=True)
        output['cost_summary']=cost_summary(output)
        output['metadata']['status']='complete'
        output['metadata']['completed_at']=datetime.now(timezone.utc).isoformat()
        save()
    finally:
        live.get_order_from_db=original


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--online',action='store_true',help='发送合成题和相关政策到.env模型接口并产生用量；须获得用户授权。')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--final-only',action='store_true',help='最终约束只跑新版与新版always，不重复旧版。')
    parser.add_argument('--output',default='work/answer-quality-evaluation.json')
    args=parser.parse_args()
    manifest,cases=read_cases()
    if args.online:
        asyncio.run(run(args,manifest,cases))
    else:
        print('Offline validation OK: 18 cases; no model calls. Use --online only after approval.')
