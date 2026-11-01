import ContextCard from "./ContextCard";
import { useEffect, useState, type FormEvent } from "react";
import { api, watchRun } from "./api";

type Run = {id:string;conversation_id:string;status:string;input:{query:string};output:any;error_code?:string};
const nodes:Record<string,string>={route:"识别任务",read_plan:"校验只读工具计划",gather:"核对信息",observe:"读取订单",retrieve:"检索政策",propose:"形成申请",confirmation:"核对确认",recheck:"重新检查条件",submit:"登记申请",wait_approval:"等待审批",refresh:"核实审批状态",result:"整理办理结果",lookup_result:"整理订单信息",knowledge_result:"整理政策依据",unsupported:"说明能力边界"};
const businessStates:Record<string,string>={pending:"待审批",completed:"已完成",rejected:"已拒绝",invalidated:"条件已失效"};
const tools:Record<string,string>={get_order_status:"查询订单",search_knowledge_base:"检索政策",request_shipping:"登记申请",get_shipping_status:"查询申请状态"};
const states:Record<string,string>={queued:"已排队",running:"处理中",awaiting_input:"等待补充",
  awaiting_confirmation:"等待确认",awaiting_approval:"等待审批",interrupted:"处理暂停",
  completed:"处理完成",cancelled:"已停止",failed:"处理失败"};
const steps:Record<string,string>={"run.queued":"流程已排队","run.started":"LangGraph 开始处理",
  "tool.retry":"只读查询短暂失败，有限重试中","tool.started":"正在调用工具","tool.finished":"工具已返回","answer.published":"结果已发布",
  "run.paused":"流程已暂停，等待下一步","run.finished":"本次处理结束","run.resumed":"恢复流程","tool.failed":"工具未完成","graph.step":"流程步骤完成"};

export function AfterSales({token,conversationId,initialRunId,onResetContext}:{token:string;conversationId?:string;initialRunId?:string;onResetContext?:(id:string|null)=>void}) {
  const [activeConversation,setActiveConversation]=useState<string|null>(conversationId||null);
  const [configuration,setConfiguration]=useState<any>(null);
  const [query,setQuery]=useState("请帮我催发货");
  const [number,setNumber]=useState("");
  const [kind,setKind]=useState("");
  const [reason,setReason]=useState("");
  const [run,setRun]=useState<Run|null>(null);
  const [recent,setRecent]=useState<Run[]>([]);
  const [events,setEvents]=useState<{id:number;type:string;payload:any}[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [usage,setUsage]=useState<any>(null);
  const [verified,setVerified]=useState<any>(null);
  const loadRecent=()=>api("/aftersales/runs",token).then(data=>setRecent(data.items));
  useEffect(()=>{loadRecent().catch(e=>setError(e.message));},[token]);
  function adopt(row:Run) {
    setVerified(null);setRun(row);setActiveConversation(row.conversation_id);setQuery(row.input.query||"");
    const proposal=row.output?.proposal || row.output?.known;
    setNumber(proposal?.order_number||"");setKind(proposal?.shipping_kind||"");setReason(proposal?.reason||"");
  }
  useEffect(()=>{
    let active=true;
    if(!run){setUsage(null);return;}
    api("/runs/"+run.id+"/usage",token).then(value=>{if(active)setUsage(value);})
      .catch(problem=>{if(active)setError(problem.message);});
    return ()=>{active=false;};
  },[token,run]);
  async function verifyBusiness(){
    if(!run)return;
    setBusy(true);setError("");
    try{setVerified(await api("/runs/"+run.id+"/business-status",token));}
    catch(problem){setError((problem as Error).message);}finally{setBusy(false);}
  }
  useEffect(()=>{
    let active=true;const controller=new AbortController();
    if(initialRunId)api("/runs/"+initialRunId,token).then(async row=>{
      if(!active)return;adopt(row);
      if(["queued","running"].includes(row.status)){
        setBusy(true);
        try{
          await watchRun(row.id,token,item=>{if(active)setEvents(previous=>previous.some(old=>old.id===item.id)?previous:[...previous,item]);},controller.signal);
          const final=await api("/runs/"+row.id,token);if(active)adopt(final);
        }finally{if(active)setBusy(false);}
      }
    }).catch(problem=>{if(active)setError(problem.message);});
    return ()=>{active=false;controller.abort();};
  },[token,initialRunId]);
  useEffect(()=>{
    let active=true;if(!run){setConfiguration(null);return;}
    api("/runs/"+run.id+"/configuration",token).then(value=>{if(active)setConfiguration(value);})
      .catch(problem=>{if(active)setError(problem.message);});
    return ()=>{active=false;};
  },[token,run?.id]);
  async function follow(id:string) {
    await watchRun(id,token,item=>setEvents(previous=>previous.some(row=>row.id===item.id)?previous:[...previous,item]));
    adopt(await api("/runs/"+id,token));
    await loadRecent();
  }
  async function start(event:FormEvent) {
    event.preventDefault();if(initialRunId)onResetContext?.(activeConversation);setBusy(true);setError("");setEvents([]);
    try {
      const created=await api("/runs",token,{kind:"aftersales",query,conversation_id:activeConversation,order_number:run?null:number||null,
        shipping_kind:run?null:kind||null,reason:run?null:reason||null},{"Idempotency-Key":crypto.randomUUID()});
      setRun(created);setActiveConversation(created.conversation_id);await follow(created.id);
    } catch(e){setError((e as Error).message);} finally{setBusy(false);}
  }
  async function resume(action:string) {
    if(!run)return;setBusy(true);setError("");
    try {
      await api("/runs/"+run.id+"/resume",token,{action,pause_id:run.output?.pause_id||null,
        ...(["supply","correct"].includes(action)?{
          ...(number?{order_number:number}:{}),...(kind?{shipping_kind:kind}:{}),...(reason?{reason}:{}),
        }:{})},{"Idempotency-Key":crypto.randomUUID()});
      await follow(run.id);
    } catch(e){setError((e as Error).message);adopt(await api("/runs/"+run.id,token));}
    finally{setBusy(false);}
  }
  async function stop() {
    if(!run)return;setBusy(true);
    try {adopt(await api("/runs/"+run.id+"/cancel",token,{}));await loadRecent();}
    catch(e){setError((e as Error).message);} finally{setBusy(false);}
  }
  const verifiedForRun=verified?.run_id===run?.id?verified:null;
  const request=verifiedForRun?.request||run?.output?.request;
  const pendingEdits=run?.status==="awaiting_confirmation" && (number!==run.output?.proposal?.order_number || kind!==run.output?.proposal?.shipping_kind || reason!==run.output?.proposal?.reason);
  const fields=<>
    <label>售后订单号<input value={number} onChange={e=>setNumber(e.target.value)} maxLength={64} placeholder="可先留空，流程会询问"/></label>
    <label>申请类型<select aria-label="申请类型" value={kind} onChange={e=>setKind(e.target.value)}>
      <option value="">尚未确定</option><option value="normal">普通催单</option><option value="expedite">加急申请（需审批）</option></select></label>
    <label>申请原因<input value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></label>
  </>;
  return <section className="knowledge"><h2>售后助手</h2>
    <p className="muted">可查询订单、查阅政策、登记普通催单或申请加急。办理前需要确认；加急由独立审批员决定。当前不会办理退款或通知外部仓库。</p>
    {activeConversation&&<p className="muted">当前会话 {activeConversation.slice(0,8)}；新任务不自动沿用旧条件。</p>}
    <button className="quiet" disabled={busy} onClick={()=>{onResetContext?.(null);setRun(null);setActiveConversation(null);setQuery("");setEvents([]);setNumber("");setKind("");setReason("");setVerified(null);}}>新建会话</button>
    {run&&<button className="quiet" disabled={busy} onClick={()=>{onResetContext?.(activeConversation);setRun(null);setQuery("");setEvents([]);setNumber("");setKind("");setReason("");setVerified(null);}}>在当前会话准备新任务</button>}
    <form onSubmit={start}><fieldset disabled={busy}>
      <label>售后需求<textarea value={query} onChange={e=>setQuery(e.target.value)} required maxLength={1200}/></label>
      {!run&&fields}<button disabled={busy}>开始新流程</button>
    </fieldset></form>
    {recent.length>0&&<div className="actions"><label>恢复已有流程<select disabled={busy} aria-label="恢复已有流程" value={run?.id||""}
      onChange={async e=>{if(e.target.value){const row=await api("/runs/"+e.target.value,token);onResetContext?.(row.conversation_id);setEvents([]);adopt(row);}else{onResetContext?.(activeConversation);setRun(null);setEvents([]);}}}>
      <option value="">新流程</option>{recent.map(row=><option key={row.id} value={row.id}>{row.input.query.slice(0,30)} · {states[row.status]||row.status} · {row.id.slice(0,8)}</option>)}
    </select></label></div>}
    {run&&<article className="document-card"><h3>当前流程：{states[run.status]||run.status}</h3>
      {configuration&&<small>运行配置：{configuration.source==="legacy_unrecorded"?"旧流程未记录版本":configuration.source==="factory"?"内置默认":"发布版本 v"+configuration.version_number}{configuration.config_hash?" · "+configuration.config_hash.slice(0,12):""}</small>}
      {run.output?.profile_guard&& !["snapshot","legacy_unrecorded"].includes(run.output.profile_guard)&&<p className="muted">服务端条件已变化，当前模型调用已受限；原配置快照仍保留。</p>}
      <p className="answer" role="status">{run.output?.answer}</p>
      <ContextCard context={run.output?.context}/>
      {run.output?.proposal&&<dl><dt>订单</dt><dd>{run.output.proposal.order_number}</dd>
        <dt>申请类型</dt><dd>{run.output.proposal.shipping_kind==="normal"?"普通催单":"加急申请"}</dd>
        <dt>原因</dt><dd>{run.output.proposal.reason}</dd><dt>将执行</dt><dd>{run.output.proposal.effect}</dd></dl>}
      {run.output?.order&&<p>实际订单：{run.output.order.number} · {run.output.order.payment_status} · {run.output.order.shipping_status}</p>}
      {request&&<dl><dt>申请编号</dt><dd>{request.id}</dd>
        <dt>真实申请状态</dt><dd>{businessStates[request.status]||request.status}</dd><dt>工单</dt><dd>{request.ticket_id||"尚未创建"}</dd>
        <dt>外部仓库通知</dt><dd>{request.external_warehouse_notified?"已通知":"尚未通知"}</dd></dl>}
      {verifiedForRun&&<p className="muted">{verifiedForRun.lookup_status==="found"?
        "以上申请状态为人工只读核实结果，不改变Agent流程状态。":
        "本次查询尚未发现已提交申请；这不表示进行中的提交已撤销。"}</p>}
      {usage&&<details><summary>本流程累计用量</summary>
        <p>工具预留尝试 {usage.tool_attempts}/{usage.max_tools}，恢复后继续累计。</p>
        <p>模型已报告token {usage.model_observed_tokens}，未结算预留 {usage.model_reserved_tokens}；上限 {usage.max_model_tokens}。</p>
        {!!usage.unknown_model_attempts&&<p>存在未核实的模型用量，预留额度未释放。</p>}
        {!!usage.legacy_estimate&&<p>旧流程用量按历史轨迹保守估算。</p>}
        {!usage.remaining_tools&&<p>累计工具额度已耗尽；仍可只读核实已提交申请，不会重新办理。</p>}
      </details>}
      {(run.output?.request||run.output?.write_in_flight)&&<button disabled={busy} onClick={verifyBusiness}>只读核实办理状态</button>}
      {run.output?.warnings?.map((text:string)=><p className="muted" key={text}>{text}</p>)}
      {run.output?.evidence?.map((item:any)=><details key={item.id}><summary>{item.title} · v{item.version}</summary><blockquote>{item.content}</blockquote></details>)}
      {["awaiting_input","awaiting_confirmation"].includes(run.status)&&<fieldset disabled={busy}>{fields}
        {run.status==="awaiting_input"?<button onClick={()=>resume("supply")}>提交补充信息</button>:<div className="actions">
          <button onClick={()=>resume("correct")}>更新信息并重新核对</button>
          <button disabled={pendingEdits} onClick={()=>resume("confirm")}>确认上述申请</button>
          {pendingEdits&&<p>信息已改动，请先更新并重新核对，再确认。</p>}
          <button className="quiet" onClick={()=>resume("cancel")}>取消本次申请</button></div>}
      </fieldset>}
      {run.status==="awaiting_approval"&&<button disabled={busy} onClick={()=>resume("refresh")}>刷新审批结果</button>}
      {run.status==="interrupted"&&<button disabled={busy||usage?.remaining_tools===0} onClick={()=>resume("retry")}>恢复并核实状态</button>}
      {["awaiting_input","awaiting_confirmation","awaiting_approval","interrupted"].includes(run.status)&&
        <button className="quiet" disabled={busy||run.output?.write_in_flight} onClick={stop}>停止跟进</button>}
      {!!events.length&&<ol className="flow-steps">{events.map(item=><li key={item.id}>{item.type==="graph.step"?(nodes[item.payload.node]||"流程步骤完成"):(steps[item.type]||item.type)}
        {item.type.startsWith("tool.")&&item.payload.name?" · "+(tools[item.payload.name]||item.payload.name):""}</li>)}</ol>}
    </article>}
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}

export function Approvals({token}:{token:string}) {
  const [items,setItems]=useState<any[]>([]);const [error,setError]=useState("");const [reason,setReason]=useState("");
  const [busy,setBusy]=useState(false);
  async function load(){setItems((await api("/shipping/requests?status=pending",token)).items);}
  useEffect(()=>{load().catch(e=>setError(e.message));},[token]);
  async function decide(id:string,action:string) {
    setBusy(true);setError("");
    try{await api("/shipping/requests/"+id+"/decision",token,{action,reason});await load();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  return <section className="knowledge"><h2>加急审批</h2><p>审批前会重新校验订单条件。批准只创建内部工单，不表示外部仓库已收到通知。</p>
    <label>审批说明<input value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></label>
    <button className="quiet" onClick={()=>load().catch(e=>setError(e.message))}>刷新待审批列表</button>
    {items.map(item=><article className="document-card" key={item.id}><h3>订单 {item.order_number}</h3>
      <p>{item.reason}</p><p className="muted">申请编号：{item.id}</p>
      <button disabled={busy} onClick={()=>decide(item.id,"approve")}>批准加急</button>
      <button className="quiet" disabled={busy} onClick={()=>decide(item.id,"reject")}>拒绝申请</button></article>)}
    {!items.length&&<p>当前没有待审批申请。</p>}{error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
