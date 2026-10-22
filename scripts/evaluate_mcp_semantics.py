"""真实模型+生产MCP图回归；仅合成对话/临时订单，显式--online才外发。"""
import argparse, asyncio, hashlib, json, os, shutil, sqlite3, sys, tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ.setdefault('HF_HUB_OFFLINE','1')
os.environ.setdefault('TRANSFORMERS_OFFLINE','1')

def validate_row(row):
 """仅检查可验证的轨迹/引用约束，不将这些检查当语义正确率。"""
 import re
 issues=[];known={}
 for index,t in enumerate(row['turns'],1):
  known.update({m['id']:m for m in t.get('matches',[])})
  if t.get('tool_errors'): issues.append(f'turn{index}: tool error')
  if any(c['name']=='request_shipping' for c in t.get('other_tools',[])): issues.append(f'turn{index}: unexpected write tool')
  if t.get('policy_check',{}).get('status')=='error': issues.append(f'turn{index}: checker error')
  if t.get('policy_check',{}).get('mode')=='reuse':
   ids=t['policy_check'].get('evidence_ids',[])
   if not ids or not set(ids)<=known.keys(): issues.append(f'turn{index}: invalid reuse lineage')
  starts=[e for e in t.get('tool_trace',[]) if e['event']=='start' and e.get('metadata',{}).get('server')=='commerce-desk-readonly']
  expected=len(t.get('queries',[]))+sum(c['name']=='get_order_status' for c in t.get('other_tools',[]))
  if len(starts)!=expected: issues.append(f'turn{index}: MCP trace count mismatch')
  completed={e['run_id'] for e in t.get('tool_trace',[]) if e['event']=='end'}
  if any(e['run_id'] not in completed for e in starts): issues.append(f'turn{index}: MCP call not completed')
  sources={m['source'] for m in known.values()}
  citations=set(re.findall(r'[A-Za-z][A-Za-z0-9_]*[.]txt',str(t.get('answer',''))))
  if not citations<=sources: issues.append(f'turn{index}: citation without evidence')
  if any(r.get('status')=='error' for r in t.get('tool_results',[])): issues.append(f'turn{index}: tool message error')
 return issues

async def run(args):
 from langchain_core.messages import HumanMessage
 from langchain_core.tools import tool
 from langchain_core.callbacks import BaseCallbackHandler
 from langgraph.checkpoint.memory import InMemorySaver
 from unittest.mock import patch
 from agents.mcp_supportpilot import MCPSupportPilotAgent
 from tool_protocol.client import server_connection
 from business.order_repository import initialize_demo_orders
 from core import settings
 from evaluate_query_rewrite import extract_turn
 from evaluate_answer_quality import variant_cost
 import agents.supportpilot_agent as live
 class Trace(BaseCallbackHandler):
  def on_tool_start(self, serialized, input_str, *, run_id, metadata=None, **kwargs):
   self.events.append({'event':'start','run_id':str(run_id),'tool':serialized.get('name'), 'input':input_str,'metadata':metadata or {}})
  def on_tool_end(self, output, *, run_id, **kwargs):
   self.events.append({'event':'end','run_id':str(run_id),'status':getattr(output,'status','success')})
  def on_tool_error(self, error, *, run_id, **kwargs):
   self.events.append({'event':'error','run_id':str(run_id),'error_type':type(error).__name__})
 @tool
 def blocked_shipping(order_id: str, kind: str='normal', reason: str='评测') -> str:
  """评测禁止提交业务。"""
  return json.dumps({'status':'blocked_evaluation'},ensure_ascii=False)
 blocked_shipping.name='request_shipping'
 cases=[json.loads(x) for x in (ROOT/'data/evaluation/answer_quality_cases.jsonl').read_text(encoding='utf-8-sig').splitlines() if x.strip()]
 if args.ids: cases=[c for c in cases if c['id'] in args.ids.split(',')]
 destination=ROOT/args.output
 destination.parent.mkdir(parents=True,exist_ok=True)
 if destination.exists(): raise FileExistsError('保留旧结果，请使用不同输出文件')
 sha=lambda p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
 report={'metadata':{'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'endpoint':settings.COMPATIBLE_BASE_URL,'model':settings.COMPATIBLE_MODEL,'variant':args.variant,'controlled_query':args.force_query,'transport':'production stdio MCP','real_llm':True,'synthetic_orders':True,'business_writes_blocked':True,'source_hashes':{p:sha(p) for p in ['src/agents/answer_policy.py','src/agents/policy_evidence.py','src/agents/supportpilot_agent.py','src/tool_protocol/server.py','src/business/rag_retrieval.py','data/evaluation/answer_quality_cases.jsonl','scripts/evaluate_mcp_semantics.py']},'limitations':'单次小样本；程序检查不是语义金标准，需逐条复核；不含网页点击、HTTP链路或生产并发。'},'rows':[]}
 def save(): destination.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
 save()
 with tempfile.TemporaryDirectory(prefix='mcp-semantic-',dir=ROOT/'work') as directory:
  db=Path(directory)/'orders.sqlite3';initialize_demo_orders(db)
  with closing(sqlite3.connect(db)) as conn, conn:
   conn.execute("UPDATE orders SET product_name='评测机械键盘', shipping_status='运输中', payment_status='已付款', estimated_delivery='2026-10-25' WHERE order_id='A1002'")
  connection=server_connection();connection['env']['SUPPORTPILOT_DB_PATH']=str(db)
  source=Path(connection['env'].get('SUPPORTPILOT_CHROMA_PATH','data/supportpilot/chroma'))
  source=source if source.is_absolute() else ROOT/source
  index=Path(directory)/'index';shutil.copytree(source,index);connection['env']['SUPPORTPILOT_CHROMA_PATH']=str(index)
  agent=MCPSupportPilotAgent(connection)
  try:
   started=perf_counter()
   with patch('agents.mcp_supportpilot.request_shipping',blocked_shipping): await agent.load()
   if args.force_query:
    original_validate=live.validate_decision
    def controlled(content,evidence):
     decision=original_validate(content,evidence)
     return {**decision,'mode':'retrieve','query':args.force_query,'evidence_ids':[]}
    live.validate_decision=controlled
   report['metadata']['startup_ms']=(perf_counter()-started)*1000
   graph=agent.get_graph();graph.checkpointer=InMemorySaver()
   for case in cases:
    trace=Trace();trace.events=[]
    config={'configurable':{'thread_id':uuid4().hex,'policy_check_mode':'auto'},'callbacks':[trace],'recursion_limit':16}
    row={'case':case,'turns':[]};report['rows'].append(row);length=0
    for question in case['turns']:
     started=perf_counter();offset=len(trace.events)
     result=await asyncio.wait_for(graph.ainvoke({'messages':[HumanMessage(content=question)]},config),timeout=150)
     messages=result['messages'][length:];length=len(result['messages'])
     parsed=extract_turn(messages)
     turn={'question':question,'answer':result['messages'][-1].content,'policy_check':result.get('policy_check',{}),**parsed,'answer_calls':[{'usage':m.usage_metadata or {},'model':m.response_metadata.get('model_name')} for m in messages if m.type=='ai' and not m.additional_kwargs.get('policy_preflight')],'elapsed_ms':(perf_counter()-started)*1000,'tool_trace':trace.events[offset:], 'tool_results':[{'name':m.name,'content':m.content,'status':getattr(m,'status',None)} for m in messages if m.type=='tool']}
     row['turns'].append(turn);save()
     print(case['id']+' turn '+str(len(row['turns']))+' completed',flush=True)
    row['cost']=variant_cost(row);row['mechanical_issues']=validate_row(row);save()
   report['metadata']['status']='complete'
  except BaseException as exc:
   report['metadata']['status']='failed';report['failure']={'type':type(exc).__name__,'message':str(exc),'failed_attempt_usage_unknown':True};save();raise
  finally: await agent.aclose()
 report['cost']={k:sum(r['cost'][k] for r in report['rows']) for k in report['rows'][0]['cost']}
 save();print(json.dumps(report['cost'],ensure_ascii=False))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--online',action='store_true');parser.add_argument('--force-query');parser.add_argument('--ids');parser.add_argument('--variant',default='after');parser.add_argument('--output',required=True);args=parser.parse_args()
 if not args.online: parser.error('仅--online启用真实模型外发；数据限合成题、演示政策和临时订单。')
 asyncio.run(run(args))
