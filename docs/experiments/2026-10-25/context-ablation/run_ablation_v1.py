"""Execute the frozen six-category protocol; no application edits, no judge API."""
import argparse
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
from time import perf_counter
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
ROOT=Path(r'D:\VScodeProgects\commerce-desk-agent')
SNAP=HERE/'frozen'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def save(p,obj): p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))


def verify():
    p=read(HERE/'protocol-30-v1.json')
    assert sha(HERE/p['cases_file'])==p['cases_sha256']
    assert sha(HERE/'freeze.json')==p['snapshot_manifest_sha256']
    for r in read(HERE/'freeze.json')['files']:
        if sha(SNAP/r['path'])!=r['sha256']:
            raise ValueError('Frozen file changed '+r['path'])
    return p,[json.loads(s) for s in (HERE/p['cases_file']).read_text(encoding='utf-8').splitlines()]


def setup():
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['ANONYMIZED_TELEMETRY']='False'
    os.environ['SUPPORTPILOT_RETRIEVAL_STRATEGY']='hybrid_rerank'
    os.environ['SUPPORTPILOT_RAG_POOL_SIZE']='12'
    os.environ['SUPPORTPILOT_RERANK_THRESHOLD']='-2'
    sys.path.insert(0,str(SNAP/'src'))
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    import business.order_repository as repo
    repo.PROJECT_ROOT=ROOT  # model cache path only; no repository query or write is invoked
    return repo


def model_config():
    from dotenv import dotenv_values
    from urllib.parse import urlparse
    env=dotenv_values(ROOT/'.env')
    endpoint=env.get('COMPATIBLE_BASE_URL') or 'https://api.deepseek.com/v1'
    if urlparse(endpoint).hostname!='api.deepseek.com':
        raise ValueError('Provider differs from expected api.deepseek.com; no request sent')
    name=env.get('COMPATIBLE_MODEL') or 'deepseek-chat'
    key=env.get('COMPATIBLE_API_KEY') or env.get('DEEPSEEK_API_KEY')
    if not key: raise ValueError('Missing model key')
    return endpoint,name,key


def preflight():
    protocol,data=verify()
    setup()
    from agents.answer_policy import ANSWER_GUIDANCE
    tree=ast.parse((SNAP/'scripts/evaluate_rag_answers.py').read_text(encoding='utf-8-sig'))
    base=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=='ANSWER_PROMPT' for t in n.targets))
    prompt=base+'\n'+ANSWER_GUIDANCE
    endpoint,name,_=model_config()
    destination=HERE/'execution-lock-v1.json'
    textpath=HERE/'fixed-answer-prompt-v1.txt'
    if destination.exists(): raise FileExistsError(destination)
    textpath.write_text(prompt,encoding='utf-8')
    save(destination,{'locked_at':now(),'protocol_sha256':sha(HERE/'protocol-30-v1.json'),
                      'runner_sha256':sha(Path(__file__)),'fixed_prompt_sha256':sha(textpath),
                      'endpoint':endpoint,'model_setting':name,'temperature':0,'stream':False,
                      'max_retries':0,'timeout_seconds':90,'repetitions':1,
                      'multi_turn_backend':'Frozen LangGraph with local policy tool; read/write business tools blocked.',
                      'counterfactual':'On/off share all source and parameters. Single-turn shares anchors; multi-turn derived paths may differ.',
                      'safety':'No real orders, no business writes, no model judge calls. Keys not persisted.',
                      'timing':'warm retrieval; fixed-evidence pair order alternated; cold load separately recorded.'})
    print(json.dumps({'status':'execution_locked','model':name,'endpoint':endpoint},ensure_ascii=True),flush=True)


def locked():
    p,c=verify()
    e=read(HERE/'execution-lock-v1.json')
    assert sha(Path(__file__))==e['runner_sha256']
    assert sha(HERE/'protocol-30-v1.json')==e['protocol_sha256']
    assert sha(HERE/'fixed-answer-prompt-v1.txt')==e['fixed_prompt_sha256']
    return p,c,e


def collection(copy_if_needed):
    setup()
    from business.knowledge import build_chunks,COLLECTION_NAME,embedding_function
    import chromadb
    target=HERE/'index-copy-v1'
    if not target.exists():
        if not copy_if_needed: raise FileNotFoundError(target)
        shutil.copytree(ROOT/'data/supportpilot/chroma',target)
    client=chromadb.PersistentClient(path=str(target))
    col=client.get_collection(COLLECTION_NAME,embedding_function=embedding_function())
    s=col.get(include=['documents','metadatas'])
    original={r['id']:(r['content'],r['source']) for r in build_chunks(SNAP/'data/knowledge')}
    actual={k:(text,meta['source']) for k,text,meta in zip(s['ids'],s['documents'],s['metadatas'])}
    if original!=actual: raise ValueError('Index/policy mismatch')
    return col


def evidence_grade(evidence,groups):
    ids={r['id'] for r in evidence}
    hits=[bool(ids & set(g)) for g in groups]
    return {'ids':sorted(ids),'complete':all(hits) if hits else None,
            'coverage':sum(hits)/len(hits) if hits else None,
            'chars':sum(len(r['content']) for r in evidence),'count':len(evidence)}


def offline():
    _,cases,_=locked()
    destination=HERE/'retrieval-v1.json'
    if destination.exists(): raise FileExistsError(destination)
    start=perf_counter()
    col=collection(True)
    from business.rag_retrieval import retrieve,reranker
    from business.policy_context import flatten_policy_matches
    reranker()
    retrieve(col,'电子发票申请资料','hybrid_rerank',3,12,-2)
    output={'metadata':{'started_at':now(),'warmup_ms':(perf_counter()-start)*1000,
                         'execution_lock_sha256':sha(HERE/'execution-lock-v1.json'),
                         'method':'26 single-turn cases; one shared retrieval per case, off=anchors, on=expanded. Unknown excluded from coverage denominator.'},'rows':[]}
    save(destination,output)
    for case in [r for r in cases if len(r['turns'])==1]:
        begin=perf_counter()
        report=retrieve(col,case['turns'][0],'hybrid_rerank',3,12,-2)
        elapsed=(perf_counter()-begin)*1000
        off=flatten_policy_matches(report['anchor_matches'])
        on=flatten_policy_matches(report['matches'])
        assert {r['id'] for r in off}<={r['id'] for r in on}
        assert [r['id'] for r in report['anchor_matches']]==[r['id'] for r in report['matches']]
        row={'id':case['id'],'category':case['category'],'question':case['turns'][0],
             'off':{'evidence':off,**evidence_grade(off,case['evidence_groups'])},
             'on':{'evidence':on,**evidence_grade(on,case['evidence_groups'])},
             'shared_retrieval_ms':elapsed,'timing_ms':report['timing_ms'],
             'stages':report['stages'],'ranked_candidates':report['candidates']}
        output['rows'].append(row)
        save(destination,output)
        print(case['id'],row['off']['complete'],row['on']['complete'],flush=True)
    summary={}
    for category in list(dict.fromkeys(r['category'] for r in output['rows']))+['all_answerable']:
        group=[r for r in output['rows'] if r['category']==category or category=='all_answerable' and r['off']['complete'] is not None]
        summary[category]={'n':len(group),'off_complete':sum(r['off']['complete'] is True for r in group) if category!='政策没有答案' else None,
                           'on_complete':sum(r['on']['complete'] is True for r in group) if category!='政策没有答案' else None,
                           'off_mean_chars':statistics.mean(r['off']['chars'] for r in group),
                           'on_mean_chars':statistics.mean(r['on']['chars'] for r in group),
                           'improved':sum(r['off']['complete'] is False and r['on']['complete'] is True for r in group),
                           'regressed':sum(r['off']['complete'] is True and r['on']['complete'] is False for r in group)}
    output['summary']=summary
    output['metadata']['completed_at']=now()
    save(destination,output)
    print(json.dumps(summary,ensure_ascii=True),flush=True)


def client():
    from langchain_openai import ChatOpenAI
    endpoint,name,key=model_config()
    lock=read(HERE/'execution-lock-v1.json')
    assert endpoint==lock['endpoint'] and name==lock['model_setting']
    return ChatOpenAI(model=name,base_url=endpoint,api_key=key,temperature=0,streaming=False,max_retries=0,timeout=90)


async def fixed_answers():
    locked()
    setup()
    source=HERE/'retrieval-v1.json'
    report=read(source)
    assert len(report['rows'])==26 and report['metadata'].get('completed_at')
    dest=HERE/'answers-fixed-v1.json'
    if dest.exists(): raise FileExistsError(dest)
    from langchain_core.messages import SystemMessage,HumanMessage
    model=client()
    prompt=(HERE/'fixed-answer-prompt-v1.txt').read_text(encoding='utf-8')
    output={'metadata':{'started_at':now(),'retrieval_sha256':sha(source),'execution_lock_sha256':sha(HERE/'execution-lock-v1.json'),
                         'method':'52 fixed-evidence answer calls, paired alternating order; no Agent/tool/checker/judge calls.'},'rows':[]}
    save(dest,output)
    for i,row in enumerate(report['rows']):
        result={'id':row['id'],'answers':{}}
        output['rows'].append(result)
        for arm in (['off','on'] if i%2==0 else ['on','off']):
            started=perf_counter()
            try:
                response=await model.ainvoke([SystemMessage(content=prompt),HumanMessage(content=json.dumps({'question':row['question'],'evidence':row[arm]['evidence']},ensure_ascii=False))])
                result['answers'][arm]={'answer':response.content,'usage':response.usage_metadata or {},
                                         'model':response.response_metadata.get('model_name'),'finish_reason':response.response_metadata.get('finish_reason'),
                                         'generation_ms':(perf_counter()-started)*1000}
            except Exception as exc:
                result['answers'][arm]={'error_type':type(exc).__name__,'generation_ms':(perf_counter()-started)*1000}
                save(dest,output)
                raise
            save(dest,output)
            print(row['id'],arm,'done',flush=True)
    output['metadata']['completed_at']=now()
    save(dest,output)


async def conversations():
    _,cases,_=locked()
    col=collection(False)
    from business.rag_retrieval import retrieve,reranker
    from business.policy_context import flatten_policy_matches
    from business.readonly_tools import policy_result
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    import agents.supportpilot_agent as agent
    reranker()
    model=client()
    agent.get_model=lambda _:model
    destination=HERE/'answers-dialogues-v1.json'
    if destination.exists(): raise FileExistsError(destination)
    output={'metadata':{'started_at':now(),'execution_lock_sha256':sha(HERE/'execution-lock-v1.json'),
                         'backend':'Frozen LangGraph + local policy retrieval, no MCP transport; all business tools blocked.',
                         'method':'4 dialogue pairs, 16 user turns. Independent conversations, paths may differ. Both use frozen prompts and temperature=0.'},'rows':[]}
    save(destination,output)
    for i,case in enumerate(r for r in cases if len(r['turns'])>1):
        result={'id':case['id'],'arms':{}}
        output['rows'].append(result)
        for arm in (['off','on'] if i%2==0 else ['on','off']):
            calls=[]
            blocked_calls=[]
            def search(query):
                start=perf_counter()
                report=retrieve(col,query,'hybrid_rerank',3,12,-2)
                matches=report['anchor_matches'] if arm=='off' else report['matches']
                calls.append({'query':query,'matches':matches,'evidence':flatten_policy_matches(matches),
                              'elapsed_ms':(perf_counter()-start)*1000,'stages':report['stages'],
                              'anchor_ids':[r['id'] for r in report['anchor_matches']]})
                return matches
            @tool(description=agent.POLICY_DESCRIPTION)
            def search_knowledge_base(query:str)->str:
                return policy_result(query,search=search)
            @tool(description=agent.ORDER_DESCRIPTION)
            def get_order_status(order_id:str)->str:
                blocked_calls.append({'name':'get_order_status','order_id':order_id})
                return json.dumps({'status':'blocked_evaluation','message':'评测不访问真实订单。'},ensure_ascii=False)
            @tool(description=agent.request_shipping.description)
            def request_shipping(order_id:str,kind:str='normal',reason:str='用户要求催发货')->str:
                blocked_calls.append({'name':'request_shipping','order_id':order_id,'kind':kind})
                return json.dumps({'status':'blocked_evaluation','message':'评测不执行业务写入。'},ensure_ascii=False)
            graph=agent.build_supportpilot_graph(tools=[get_order_status,search_knowledge_base,request_shipping],checkpointer=InMemorySaver())
            config={'configurable':{'thread_id':case['id']+'-'+arm,'policy_check_mode':'auto'},'recursion_limit':12}
            turns=[]
            result['arms'][arm]=turns
            for j,question in enumerate(case['turns']):
                start=perf_counter()
                call_start=len(calls)
                try:
                    state=await graph.ainvoke({'messages':[HumanMessage(content=question)]},config)
                    messages=state['messages']
                    last=max(k for k,m in enumerate(messages) if m.type=='human')
                    current=messages[last:]
                    evidence=agent.policy_evidence(messages)
                    check=state.get('policy_check',{})
                    valid=check.get('evidence_ids',[]) if check.get('mode')=='reuse' else []
                    if check.get('mode')=='reuse':
                        evidence=[r for r in evidence if r['id'] in valid]
                    else:
                        evidence=flatten_policy_matches([m for c in calls[call_start:] for m in c['matches']])
                    answer=messages[-1].content
                    turns.append({'question':question,'answer':answer,'policy_check':check,'evidence':evidence,
                                  'evidence_grade':evidence_grade(evidence,case['evidence_by_turn'][j]),
                                  'policy_calls':calls[call_start:],'blocked_business_calls':list(blocked_calls),
                                  'answer_model_calls':[{'usage':getattr(m,'usage_metadata',None) or {},'model':m.response_metadata.get('model_name')}
                                                        for m in current if m.type=='ai' and not m.additional_kwargs.get('policy_preflight')],
                                  'trace':[m.model_dump(mode='json') for m in current],
                                  'elapsed_ms':(perf_counter()-start)*1000})
                except Exception as exc:
                    turns.append({'question':question,'error_type':type(exc).__name__,'elapsed_ms':(perf_counter()-start)*1000})
                    save(destination,output)
                    raise
                save(destination,output)
                print(case['id'],arm,'turn',j+1,'done',flush=True)
    output['metadata']['completed_at']=now()
    save(destination,output)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['preflight','offline','fixed','dialogues'])
    args=parser.parse_args()
    if args.mode=='preflight': preflight()
    elif args.mode=='offline': offline()
    elif args.mode=='fixed': asyncio.run(fixed_answers())
    else: asyncio.run(conversations())
