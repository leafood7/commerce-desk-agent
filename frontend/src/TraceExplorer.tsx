import { useEffect, useState } from "react";
import { api } from "./api";
const services:Record<string,string>={api:"接口请求",worker:"后台调度",agent:"Agent流程",langgraph:"流程节点",mcp_client:"MCP调用",mcp_server:"MCP服务",business:"业务服务",retrieval:"知识检索",vector_store:"向量存储"};
const states:Record<string,string>={completed:"已完成",failed:"失败",paused:"等待补充或确认",cancelled:"已取消"};
export function TraceExplorer({token}:{token:string}) {
 const [items,setItems]=useState<any[]>([]),[selected,setSelected]=useState<any>(null),[query,setQuery]=useState(""),[run,setRun]=useState(""),[error,setError]=useState(""),[busy,setBusy]=useState(false),[offset,setOffset]=useState(0),[more,setMore]=useState(false);
 const [caseId,setCaseId]=useState(""),[trail,setTrail]=useState<any>(null);
 async function loadTrail(){if(!caseId.trim())return;setBusy(true);setError("");try{setTrail(await api("/cases/"+encodeURIComponent(caseId.trim())+"/trail",token));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function list(next=0){setError("");setBusy(true);try{const data=await api("/traces?offset="+next+(run?"&run_id="+encodeURIComponent(run.trim()):""),token);setItems(data.items);setOffset(next);setMore(data.has_more);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function open(id:string,append=false){if(!id.trim())return;setBusy(true);setError("");try{const data=await api("/traces/"+encodeURIComponent(id.trim())+"?offset="+(append?selected.items.length:0),token);setSelected((old:any)=>append?{...data,items:[...old.items,...data.items]}:data);setQuery(id);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{void list();},[token]);
 const entries=selected?.items||[];
 function depth(row:any){let n=0,parent=row.parent_id;const seen=new Set<string>();while(parent&&n<8&&!seen.has(parent)){seen.add(parent);const found=entries.find((r:any)=>r.id===parent);if(!found)break;n++;parent=found.parent_id;}return n;}
 return <section><h2>执行追踪</h2><p>查看自己的请求经过哪些环节、在哪里失败。记录步骤与状态，不展示模型草稿或完整业务正文。</p>
 <form onSubmit={e=>{e.preventDefault();void open(query);}}><label>请求追踪编号<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="粘贴错误提示或请求中的追踪编号"/></label><button disabled={busy}>查看请求</button></form>
 <form onSubmit={e=>{e.preventDefault();void list(0);}}><label>流程编号（可选）<input value={run} onChange={e=>setRun(e.target.value)} placeholder="关联同一流程的启动、补充和恢复请求"/></label><button disabled={busy}>筛选请求</button></form>
 <form onSubmit={e=>{e.preventDefault();void loadTrail();}}><label>业务编号（退货申请、凭证、冲突、草稿或流程编号）<input value={caseId} onChange={e=>setCaseId(e.target.value)} placeholder="粘贴业务记录编号，一次查看完整链路"/></label><button disabled={busy}>查看案例链路</button></form>
 {error&&<p role="alert" className="error">{error}</p>}
 <ul>{items.map(row=><li key={row.id}><button className="quiet" onClick={()=>void open(row.id)}>{row.operation} · {row.id.slice(0,8)}</button><span className="muted"> {row.created_at}{row.run_id?" · 流程 "+row.run_id.slice(0,8):""}</span></li>)}</ul>
 {offset>0&&<button disabled={busy} onClick={()=>void list(Math.max(0,offset-20))}>上一页</button>}{more&&<button disabled={busy} onClick={()=>void list(offset+20)}>下一页</button>}
 {selected&&<article><h3>请求 {selected.id}</h3><p>{selected.notice}</p>{selected.run_id&&<p>流程编号：<button className="quiet" onClick={()=>{setRun(selected.run_id);}}>{selected.run_id}</button>（填入筛选后查询）</p>}<button disabled={busy} onClick={()=>void open(selected.id)}>刷新步骤</button>
 {!entries.length&&<p>尚无已保存步骤，处理可能仍在进行；请刷新核实。缺少记录不能据此判断业务未执行。</p>}
 <ol>{entries.map((row:any)=><li key={row.id} style={{marginLeft:depth(row)*16}}><strong>{services[row.service]||row.service} · {row.operation}</strong><p>{states[row.status]||row.status} · {row.elapsed_ms} ms{row.error_code?" · 错误："+row.error_code:""}</p>
 <details><summary>定位信息</summary><p>步骤：{row.id}<br/>上级：{row.parent_id||"请求入口"}<br/>开始：{row.started_at}</p><pre>{JSON.stringify(row.details,null,2)}</pre></details></li>)}</ol>
 {selected.has_more&&<button disabled={busy} onClick={()=>void open(selected.id,true)}>加载后续步骤</button>}</article>}
 {trail&&<article><h3>案例链路 {trail.resource_id}</h3>
  <p>请求 {trail.traces.length} 条 · 流程 {trail.runs.length} 个 · 工具 {trail.tools.length} 次 · 审计 {trail.audits.length} 条 · 步骤 {trail.spans.length} 个</p>
  {!trail.traces.length&&<p>尚未找到与该业务编号关联的请求记录。若操作刚完成，请稍后刷新。</p>}
  {trail.runs.length>0&&<details open><summary>流程与事件</summary><ol>{trail.runs.map((r:any)=><li key={r.id}><strong>{r.kind} · {states[r.status]||r.status}</strong><span className="muted"> · {r.id.slice(0,8)}</span><ol>{r.events.map((event:any)=><li key={event.id}>{event.type}{event.payload?.name?" · "+event.payload.name:""}{event.payload?.error_code?" · "+event.payload.error_code:""}</li>)}</ol></li>)}</ol></details>}
  {trail.spans.length>0&&<details open><summary>请求步骤</summary><ol>{trail.spans.map((row:any)=><li key={row.id}><strong>{services[row.service]||row.service} · {row.operation}</strong><span className="muted"> · {states[row.status]||row.status} · {row.elapsed_ms} ms{row.error_code?" · 错误："+row.error_code:""}</span></li>)}</ol></details>}
  {trail.tools.length>0&&<details><summary>工具调用</summary><ol>{trail.tools.map((row:any)=><li key={row.id}>{row.name} · {states[row.status]||row.status} · {row.elapsed_ms} ms{row.error_code?" · "+row.error_code:""}</li>)}</ol></details>}
  {trail.audits.length>0&&<details><summary>业务审计</summary><ol>{trail.audits.map((row:any)=><li key={row.id}>{row.action} · {row.resource_id.slice(0,8)} · {row.created_at}</li>)}</ol></details>}
  <details><summary>业务状态</summary><pre>{JSON.stringify(trail.business,null,2)}</pre></details>
 </article>}
 </section>;
}
