"""Fixed prompt-only paired checks plus old-case regression; never edits runtime code."""
import argparse,ast,asyncio,hashlib,json,sys
from pathlib import Path
from time import perf_counter
HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'context-ablation-2026-10-25'
ROOT=Path(r'D:\VScodeProgects\commerce-desk-agent')
sys.path.insert(0,str(OLD))
import run_ablation_v1 as base
def read(name):return json.loads((HERE/name).read_text(encoding='utf-8'))
def save(name,data):(HERE/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def guidance(p):
 tree=ast.parse(p.read_text(encoding='utf-8-sig'))
 return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=='ANSWER_GUIDANCE' for t in n.targets))

def freeze():
 lock=read('acceptance-lock.json')
 assert sha(HERE/'acceptance-cases.json')==lock['cases_sha256']
 changed=[]
 for path,h in lock['source_hashes'].items():
  if sha(ROOT/path)!=h:changed.append(path)
 assert changed==['src/agents/answer_policy.py'],changed
 after=ROOT/'src/agents/answer_policy.py'
 (HERE/'answer_policy.after.py').write_bytes(after.read_bytes())
 old=guidance(HERE/'answer_policy.before.py')
 new=guidance(HERE/'answer_policy.after.py')
 old_prompt=(OLD/'fixed-answer-prompt-v1.txt').read_text(encoding='utf-8')
 assert old_prompt.endswith(old)
 (HERE/'prompt.before.txt').write_text(old_prompt,encoding='utf-8')
 (HERE/'prompt.after.txt').write_text(old_prompt[:-len(old)]+new,encoding='utf-8')
 cases=read('acceptance-cases.json')
 retrieval=json.loads((OLD/'retrieval-v1.json').read_text(encoding='utf-8'))
 r15=next(r for r in retrieval['rows'] if r['id']=='ablation-015')
 dialogue=json.loads((OLD/'answers-dialogues-v1.json').read_text(encoding='utf-8'))
 r30=next(r for r in dialogue['rows'] if r['id']=='ablation-030')['arms']['on'][1]
 cases += [{'id':'known-015','group':'known_regression','question':r15['question'],'evidence':r15['on']['evidence'],
            'expected':'部分退货不承诺整券立即返，不说必不返或只有全退才返；发票按实际影响核实。'},
           {'id':'known-030','group':'known_regression','question':r30['question'],'evidence':r30['evidence'],
            'expected':'运费券商品退货不当然返还，不添加只有取消才处理的排他条件。'}]
 save('paired-cases.json',cases)
 destination=HERE/'execution-lock.json'
 if destination.exists():raise FileExistsError(destination)
 endpoint,name,_=base.model_config()
 save('execution-lock.json',{'locked_at':base.now(),'runner_sha256':sha(Path(__file__)),'cases_sha256':sha(HERE/'paired-cases.json'),
                             'acceptance_lock_sha256':sha(HERE/'acceptance-lock.json'),
                             'before_guidance_sha256':sha(HERE/'answer_policy.before.py'),'after_guidance_sha256':sha(HERE/'answer_policy.after.py'),
                             'before_prompt_sha256':sha(HERE/'prompt.before.txt'),'after_prompt_sha256':sha(HERE/'prompt.after.txt'),
                             'endpoint':endpoint,'model_setting':name,'temperature':0,'stream':False,'max_retries':0,
                             'plan':'14 paired fixed-evidence cases (12 directed acceptance,2 known); 26 old fixed cases after-only regression;4 paired-dialogue cases after-only regression. No model judge.',
                             'limits':'Paired fixed evidence checks only guidance effect, not whole-Agent reasoning. Old after-only comparisons are historical, not fresh randomized causal pairs.'})
 print('Execution frozen: only answer_policy.py changed; 28 paired + 26 regression answer calls, then 4 dialogue regressions.',flush=True)

def verify():
 lock=read('execution-lock.json')
 assert sha(Path(__file__))==lock['runner_sha256']
 for name,key in [('paired-cases.json','cases_sha256'),('prompt.before.txt','before_prompt_sha256'),('prompt.after.txt','after_prompt_sha256')]:
  assert sha(HERE/name)==lock[key]
 assert sha(ROOT/'src/agents/answer_policy.py')==lock['after_guidance_sha256']
 before=read('acceptance-lock.json')
 assert sha(HERE/'acceptance-lock.json')==lock['acceptance_lock_sha256']
 assert all(sha(ROOT/path)==h for path,h in before['source_hashes'].items() if path!='src/agents/answer_policy.py')
 base.verify()
 return lock

async def answers():
 verify();base.setup()
 from langchain_core.messages import HumanMessage,SystemMessage
 model=base.client()
 output={'metadata':{'started_at':base.now(),'lock_sha256':sha(HERE/'execution-lock.json'),'method':'Same evidence,model,temp and template; only shared guidance changes; alternating order; no hidden retry.'},'paired':[],'regression':[]}
 destination=HERE/'answers.json'
 if destination.exists():raise FileExistsError(destination)
 async def call(case,prompt):
  start=perf_counter()
  try:
   resp=await model.ainvoke([SystemMessage(content=prompt),HumanMessage(content=json.dumps({'question':case['question'],'evidence':case['evidence']},ensure_ascii=False))])
   return {'answer':resp.content,'usage':resp.usage_metadata or {},'model':resp.response_metadata.get('model_name'),
           'finish_reason':resp.response_metadata.get('finish_reason'),'generation_ms':(perf_counter()-start)*1000}
  except Exception as exc:
   return {'error_type':type(exc).__name__,'generation_ms':(perf_counter()-start)*1000,'usage_unknown':True}
 prompts={arm:(HERE/f'prompt.{arm}.txt').read_text(encoding='utf-8') for arm in ['before','after']}
 for i,c in enumerate(read('paired-cases.json')):
  row={'id':c['id'],'group':c['group'],'answers':{}}
  output['paired'].append(row)
  for arm in (['before','after'] if i%2==0 else ['after','before']):
   row['answers'][arm]=await call(c,prompts[arm]);save('answers.json',output)
   print(c['id'],arm,'done',flush=True)
 old=json.loads((OLD/'retrieval-v1.json').read_text(encoding='utf-8'))
 for r in old['rows']:
  c={'question':r['question'],'evidence':r['on']['evidence']}
  result=await call(c,prompts['after'])
  output['regression'].append({'id':r['id'],**result});save('answers.json',output)
  print(r['id'],'regression done',flush=True)
 output['metadata']['completed_at']=base.now();save('answers.json',output)

async def dialogues():
 verify()
 col=base.collection(False)
 from business.rag_retrieval import retrieve,reranker
 from business.policy_context import flatten_policy_matches
 from business.readonly_tools import policy_result
 from langchain_core.messages import HumanMessage
 from langchain_core.tools import tool
 from langgraph.checkpoint.memory import InMemorySaver
 import agents.supportpilot_agent as agent
 agent.ANSWER_GUIDANCE=guidance(HERE/'answer_policy.after.py')
 model=base.client();agent.get_model=lambda _:model
 reranker()
 cases=[json.loads(s) for s in (OLD/'scorecards-30-v1.jsonl').read_text(encoding='utf-8').splitlines()]
 dest=HERE/'dialogues.json'
 if dest.exists():raise FileExistsError(dest)
 output={'metadata':{'started_at':base.now(),'method':'Frozen original Agent with exact new shared guidance only; local retrieval, expansion on, no MCP. One run, historical comparison.',
                     'guidance_sha256':sha(HERE/'answer_policy.after.py')},'rows':[]}
 for case in [c for c in cases if len(c['turns'])>1]:
  calls=[];blocked=[]
  def search(query):
   r=retrieve(col,query,'hybrid_rerank',3,12,-2)
   calls.append({'query':query,'matches':r['matches']})
   return r['matches']
  @tool(description=agent.POLICY_DESCRIPTION)
  def search_knowledge_base(query:str)->str:return policy_result(query,search=search)
  @tool(description=agent.ORDER_DESCRIPTION)
  def get_order_status(order_id:str)->str:
   blocked.append({'name':'get_order_status','order_id':order_id});return '{"status":"blocked_evaluation"}'
  @tool(description=agent.request_shipping.description)
  def request_shipping(order_id:str,kind:str='normal',reason:str='用户要求催发货')->str:
   blocked.append({'name':'request_shipping','order_id':order_id});return '{"status":"blocked_evaluation"}'
  graph=agent.build_supportpilot_graph(tools=[get_order_status,search_knowledge_base,request_shipping],checkpointer=InMemorySaver())
  row={'id':case['id'],'turns':[]};output['rows'].append(row)
  for i,q in enumerate(case['turns']):
   start=perf_counter();offset=len(calls)
   try:
    state=await graph.ainvoke({'messages':[HumanMessage(content=q)]},{'configurable':{'thread_id':case['id'],'policy_check_mode':'auto'},'recursion_limit':12})
    msgs=state['messages'];last=max(j for j,m in enumerate(msgs) if m.type=='human');current=msgs[last:]
    check=state['policy_check']
    ev=agent.policy_evidence(msgs) if check['mode']=='reuse' else flatten_policy_matches([m for c in calls[offset:] for m in c['matches']])
    if check['mode']=='reuse':ev=[r for r in ev if r['id'] in check['evidence_ids']]
    row['turns'].append({'question':q,'answer':msgs[-1].content,'policy_check':check,'evidence':ev,
                         'evidence_grade':base.evidence_grade(ev,case['evidence_by_turn'][i]),'policy_calls':calls[offset:],'blocked_business_calls':list(blocked),
                         'answer_model_calls':[{'usage':m.usage_metadata or {},'model':m.response_metadata.get('model_name')} for m in current if m.type=='ai' and not m.additional_kwargs.get('policy_preflight')],
                         'trace':[m.model_dump(mode='json') for m in current],'elapsed_ms':(perf_counter()-start)*1000})
   except Exception as exc:
    row['turns'].append({'question':q,'error_type':type(exc).__name__,'elapsed_ms':(perf_counter()-start)*1000})
    save('dialogues.json',output);raise
   save('dialogues.json',output);print(case['id'],'turn',i+1,'done',flush=True)
 output['metadata']['completed_at']=base.now();save('dialogues.json',output)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','answers','dialogues']);a=p.parse_args()
 if a.mode=='freeze':freeze()
 elif a.mode=='answers':asyncio.run(answers())
 else:asyncio.run(dialogues())
