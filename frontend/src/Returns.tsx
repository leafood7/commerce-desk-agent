import { useEffect, useRef, useState } from "react";
import { api, watchRun } from "./api";
import { QualityInspections } from "./QualityInspections";

const statuses:Record<string,string>={draft:"草稿",pending_approval:"待审批",approved:"已批准待提交",rejected:"已拒绝",cancelled:"已取消",accepted_simulated:"模拟已受理"};
const conditions:Record<string,string>={unknown:"尚未核实",unused:"用户确认未使用",used:"已使用"};
const bases:Record<string,string>={no_reason:"无理由退货",quality_issue:"质量问题退货"};
const missingLabels:Record<string,string>={actual_signed_at:"实际签收时间（须由业务数据核实）",verified_signed_at:"需核实异常的签收时间",item_condition:"商品是否使用",applicable_policy_for_sku:"适用该商品的政策",activation_status:"实际激活状态",member_entitlement:"实际会员权益",quality_verdict:"实际质检结论"};

export function Returns({token,role,initialRunId}:{token:string;role:string;initialRunId?:string}){
  const [number,setNumber]=useState("A1003"),[order,setOrder]=useState<any>(null);
  const [policies,setPolicies]=useState<any[]>([]),[policy,setPolicy]=useState("");
  const [item,setItem]=useState(""),[quantity,setQuantity]=useState(1),[condition,setCondition]=useState("unknown"),[reason,setReason]=useState("");
  const [rows,setRows]=useState<any[]>([]),[selected,setSelected]=useState<any>(null),[preview,setPreview]=useState<any>(null);
  const [error,setError]=useState(""),[busy,setBusy]=useState(false);
  const [query,setQuery]=useState("申请退货，请核对适用商品、实际签收后的时间窗口和未使用条件。"),[description,setDescription]=useState("");
  const [sourceVersions,setSourceVersions]=useState<any[]>([]),[sourceVersion,setSourceVersion]=useState(""),[sources,setSources]=useState<any[]>([]);
  const [sourceOffset,setSourceOffset]=useState(0),[sourceMore,setSourceMore]=useState(false);
  const [ruleSkus,setRuleSkus]=useState(""),[ruleDays,setRuleDays]=useState(7),[ruleUnused,setRuleUnused]=useState(true),[verified,setVerified]=useState(false);
  const [requiredFacts,setRequiredFacts]=useState<string[]>([]);
  const [qualityRequirement,setQualityRequirement]=useState("recorded");
  const [basis,setBasis]=useState("no_reason"),[ruleBasis,setRuleBasis]=useState("no_reason");
  const [clauseRefs,setClauseRefs]=useState<Record<string,{chunk_id:string;quote:string}>>({});
  const [flow,setFlow]=useState<any>(null),[recent,setRecent]=useState<any[]>([]),[steps,setSteps]=useState<any[]>([]);
  const watcher=useRef<AbortController|null>(null);
  useEffect(()=>()=>watcher.current?.abort(),[]);
  async function reload(){const [p,r,f]=await Promise.all([api("/returns/policies",token),api("/returns/requests",token),api("/aftersales/runs?workflow=returns",token)]);setPolicies(p.items);setRows(r.items);setRecent(f.items);setPolicy(previous=>previous||p.items[0]?.id||"");}
  useEffect(()=>{void reload().catch(e=>setError(e.message));},[token]);
  async function act(task:()=>Promise<void>){setBusy(true);setError("");try{await task();await reload();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function loadSources(id:string,offset=0){const result=await api(`/returns/policy-sources/${id}?offset=${offset}&limit=20`,token);setSources(old=>offset?[...old,...result.items]:result.items);setSourceOffset(offset+result.items.length);setSourceMore(result.has_more);}
  async function lookup(){const data=await api("/orders/"+encodeURIComponent(number),token);setOrder(data);setItem(data.items[0]?.id||"");setSelected(null);setPreview(null);}
  function body(){return {order_number:number,item_id:item,policy_id:policy,quantity,reason,item_condition:condition,return_basis:basis};}
  async function open(row:any){setSelected(row);setPreview(null);if(["draft","pending_approval","approved"].includes(row.status))setPreview(await api(`/returns/requests/${row.id}/preview`,token));}
  async function action(name:string,extra:Record<string,unknown>={}){
    const row=await api(`/returns/requests/${selected.id}/${name}`,token,{expected_revision:selected.revision,...extra});await open(row);
  }
  async function follow(id:string){
    watcher.current?.abort();const controller=new AbortController();watcher.current=controller;
    await watchRun(id,token,event=>setSteps(old=>old.some(e=>e.id===event.id)?old:[...old,event]),controller.signal);
    const row=await api("/runs/"+id,token);setFlow(row);
    if(row.output?.proposal?.return_condition)setCondition(row.output.proposal.return_condition);
    if(row.output?.request)await open(row.output.request);
  }
  async function startFlow(){
    setSteps([]);setSelected(null);setPreview(null);
    const row=await api("/runs",token,{kind:"aftersales",query,
      order_number:number,return_item_id:item,return_policy_id:policy,return_quantity:quantity,return_condition:condition,return_basis:basis,reason,...(description?{return_description:description}:{})},
      {"Idempotency-Key":crypto.randomUUID()});setFlow(row);await follow(row.id);
  }
  async function resumeFlow(action:string){
    const fields=["supply","correct"].includes(action)?{order_number:number,return_item_id:item,return_policy_id:policy,return_quantity:quantity,return_condition:condition,return_basis:basis,reason,...(flow.output?.flow_version==="guided_v3"?{return_description:description}:{})}:action==="accept_condition"?{condition_proposal_hash:flow.output?.condition_proposal?.proposal_hash}:{};
    await api(`/runs/${flow.id}/resume`,token,{action,pause_id:flow.output?.pause_id,...fields},{"Idempotency-Key":crypto.randomUUID()});await follow(flow.id);
  }
  async function openFlow(id:string){
    const row=await api("/runs/"+id,token);if(!row.input.return_policy_id)throw new Error("这不是退货流程，请从售后助手打开。");
    setFlow(row);setSelected(null);setPreview(null);const p=row.output?.proposal||row.output?.known;
    setQuery(row.input.query||"");setDescription(p?.return_description??row.input.return_description??"");
    setBasis(p?.return_basis||row.output?.request?.return_basis||row.input.return_basis||"no_reason");
    const request=row.output?.request;
    const orderNumber=p?.order_number||request?.order_number||row.input.order_number||"";
    setNumber(orderNumber);setItem(p?.return_item_id||request?.item_id||row.input.return_item_id||"");setQuantity(p?.return_quantity||request?.quantity||row.input.return_quantity||1);
    setCondition(p?.return_condition||request?.item_condition||row.input.return_condition||"unknown");setReason(p?.reason||request?.reason||row.input.reason||"");setPolicy(p?.return_policy_id||request?.policy_id||row.input.return_policy_id);
    setOrder(orderNumber?await api("/orders/"+encodeURIComponent(orderNumber),token):null);
    if(row.output?.request)await open(row.output.request);if(["queued","running"].includes(row.status))await follow(id);
  }
  useEffect(()=>{if(initialRunId)void act(()=>openFlow(initialRunId));},[initialRunId,token]);
  const proposed=flow?.output?.proposal;
  const agentEdits=!!proposed?.return_item_id&&(number!==proposed.order_number||item!==proposed.return_item_id||quantity!==proposed.return_quantity||condition!==proposed.return_condition||basis!==(proposed.return_basis||"no_reason")||reason!==proposed.reason||description!==(proposed.return_description||"")||policy!==(proposed.return_policy_id||flow.input.return_policy_id));
  return <section className="conversation"><h2>退货申请</h2>
    <p className="notice">演示商家规则 · 外部受理仅模拟 · 不执行真实退款。申请、批准与受理分别记录。</p>
    {role==="support"&&<>
      <div className="query"><input aria-label="退货订单号" value={number} onChange={e=>{setNumber(e.target.value);setOrder(null);setItem("");setPreview(null);setSelected(null);}}/><button disabled={busy} onClick={()=>act(lookup)}>读取退货订单</button></div>
      {order&&<><p>实际签收：{order.signed_at||"未提供，不能使用预计送达代替"}</p>
        <label>订单商品<select aria-label="退货商品" value={item} onChange={e=>{setItem(e.target.value);setPreview(null);}}>{order.items.map((i:any)=><option key={i.id} value={i.id}>{i.product_name}（{i.sku}，数量 {i.quantity}）</option>)}</select></label>
        <label>商家退货规则<select aria-label="退货规则" value={policy} onChange={e=>{setPolicy(e.target.value);setPreview(null);}}><option value="">请选择适用规则</option>{policies.map(p=><option key={p.id} value={p.id}>{p.rules.skus.join("、")} · {bases[p.rules.return_basis||"no_reason"]} · {p.rules.window_days}天 · {p.rules.required_facts?.includes("quality_verdict")?(p.rules.quality_requirement==="defect_confirmed"?"需明确缺陷":p.rules.quality_requirement==="passed"?"需质检通过":"需明确质检结果"):"无需质检分支"} · 管理员已映射条款</option>)}</select></label>
        <label>申请数量<input aria-label="退货数量" type="number" min={1} value={quantity} onChange={e=>{setQuantity(Number(e.target.value));setPreview(null);}}/></label>
        <label>商品状态<select aria-label="商品状态" value={condition} onChange={e=>{setCondition(e.target.value);setPreview(null);}}>{Object.entries(conditions).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
        <label>退货原因<input aria-label="退货原因" maxLength={500} value={reason} onChange={e=>{setReason(e.target.value);setPreview(null);}}/></label>
        <label>退货分支<select aria-label="退货分支" value={basis} onChange={e=>{setBasis(e.target.value);setPreview(null);}}>{Object.entries(bases).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><small>分支必须与所选政策一致；系统不会从“坏了”或质检未通过自动推断缺陷。</small>
        <label>本次需求<textarea aria-label="退货核对需求" maxLength={1200} value={query} onChange={e=>setQuery(e.target.value)}/></label>
        <label>商品状态原话<textarea aria-label="商品状态原话" maxLength={500} value={description} onChange={e=>setDescription(e.target.value)}/></label><small>原话澄清需要新版流程配置；提取的候选须由你确认，不能证明激活或质检结果。修改需求后请启动新流程。</small>
        <button disabled={busy||!policy||!reason.trim()} onClick={()=>act(async()=>{
          const row=await api("/returns/requests",token,body(),{"Idempotency-Key":crypto.randomUUID()});await open(row);
        })}>保存退货草稿</button>
        <button disabled={busy||!policy||!reason.trim()} onClick={()=>act(startFlow)}>启动退货 Agent 核对</button>
        {selected?.status==="draft"&&<button disabled={busy||!reason.trim()} onClick={()=>act(async()=>{
          const row=await api(`/returns/requests/${selected.id}/correct`,token,{expected_revision:selected.revision,application:body()});await open(row);
        })}>用当前信息更正草稿</button>}
      </>}
    </>}
    {role==="support"&&<article><h3>退货 Agent 流程</h3><p>根据工具观察选择查询或补问；默认规则路径。管理员可在服务端允许时启用受限模型建议，资格、审批和写入始终由业务规则控制。</p>
      <label>恢复退货流程<select aria-label="恢复退货流程" value={flow?.id||""} disabled={busy} onChange={e=>{const id=e.target.value;if(id)void act(()=>openFlow(id));}}><option value="">选择已有流程</option>{recent.map(r=><option key={r.id} value={r.id}>{r.id.slice(0,8)} · {r.status}</option>)}</select></label>
      {flow&&<><p role="status">{flow.output?.answer||"正在执行"}</p><small>流程状态：{flow.status}</small>
        {flow.output?.condition_proposal?.source&&<blockquote>待确认原话：{flow.output.condition_proposal.source.quote}</blockquote>}
        {flow.status==="awaiting_input"&&flow.output?.pause_kind==="condition_confirmation"&&<button disabled={busy||description!==(flow.output?.known?.return_description||"")} onClick={()=>act(()=>resumeFlow("accept_condition"))}>确认这段原话所表达的商品状态</button>}
        {flow.output?.fact_assessment?.items?.map((f:any)=><p key={f.field}>{f.label} · {f.status==="available"?"实际字段已提供":"尚无实际字段可核实"}：{f.message}</p>)}
        {flow.status==="awaiting_input"&&flow.output?.pause_kind!=="quality_wait"&&<button disabled={busy} onClick={()=>act(()=>resumeFlow("supply"))}>补充退货条件</button>}
        {flow.status==="awaiting_input"&&flow.output?.pause_kind==="quality_wait"&&<button disabled={busy} onClick={()=>act(()=>resumeFlow("refresh"))}>刷新质检结果并重新核对</button>}
        {flow.status==="awaiting_confirmation"&&<>
          {flow.output?.proposal?.return_item_id&&<button disabled={busy} onClick={()=>act(()=>resumeFlow("correct"))}>按当前信息重新核对</button>}
          <button disabled={busy||agentEdits} onClick={()=>act(()=>resumeFlow("confirm"))}>{flow.output?.request?.status==="approved"?"确认模拟受理":"确认已核对的退货预览"}</button>
          <p>确认只针对已保存的预览；改动上方字段后请先重新核对。</p>
        </>}
        {flow.status==="awaiting_approval"&&<button disabled={busy} onClick={()=>act(()=>resumeFlow("refresh"))}>刷新退货审批结果</button>}
        {flow.status==="interrupted"&&<button disabled={busy} onClick={()=>act(()=>resumeFlow("retry"))}>恢复并核实退货流程</button>}
        {["awaiting_input","awaiting_confirmation","awaiting_approval","interrupted"].includes(flow.status)&&<button disabled={busy} onClick={()=>act(async()=>{watcher.current?.abort();setFlow(await api(`/runs/${flow.id}/cancel`,token,{}));})}>停止本次退货跟进</button>}
        {proposed?.return_item_id&&<dl><dt>退货分支</dt><dd>{bases[proposed.return_basis||"no_reason"]}</dd><dt>核对订单</dt><dd>{proposed.order_number}</dd><dt>申请数量</dt><dd>{proposed.return_quantity}</dd><dt>退货原因</dt><dd>{proposed.reason}</dd><dt>商品状态</dt><dd>{conditions[proposed.return_condition]||"未核实"}</dd><dt>商品金额参考值</dt><dd>{(proposed.goods_value_cents/100).toFixed(2)}，实际退款金额未确认</dd></dl>}
        {flow.output?.policy_context?.map((e:any)=><blockquote key={"bound-"+e.id}>{flow.output.context_purpose==="policy_question"?"只读查询检索原文：":"当前分支绑定原文："}{e.content}</blockquote>)}{flow.output?.evidence?.map((e:any)=><blockquote key={e.id}>{e.content}<small>{e.title} · 版本 {e.version}</small></blockquote>)}
        <details><summary>退货工具执行步骤</summary><ol>{steps.map(s=><li key={s.id}>{s.type}{s.payload?.name?" · "+s.payload.name:""}</li>)}</ol></details>
        {!!flow.output?.decisions?.length&&<details><summary>实际退货动作决策</summary><ol>{flow.output.decisions.map((d:any,i:number)=><li key={i}>{({read_order:"读取订单",search_policy:"检索政策",check_return:"核对退货条件",supplement:"补充检索",ask_condition:"询问商品状态",review:"交还程序生成预览",stop:"说明当前边界"} as Record<string,string>)[d.action]||d.action} · {d.mode==="model"?"模型建议已通过校验":d.mode==="rules"?"规则选择":d.mode==="fixed_action"?"唯一合法动作，不调用模型":"已回退规则"}</li>)}</ol></details>}
      </>}
    </article>}
    {role==="admin"&&<details><summary>登记人工核对的退货规则</summary><p>先在知识库发布退货政策。选择原文并核对适用商品、期限和商品状态；系统校验来源，规则含义由管理员负责确认。</p>
      <button disabled={busy} onClick={()=>act(async()=>{const result=await api("/knowledge/documents?limit=100",token);setSourceVersions(result.items.flatMap((d:any)=>d.versions.filter((v:any)=>v.published&&v.policy_scope==="returns").map((v:any)=>({...v,title:d.title}))));})}>加载已发布退货政策</button>
      <label>退货政策版本<select value={sourceVersion} disabled={busy} onChange={e=>{const id=e.target.value;setSourceVersion(id);setClauseRefs({});setVerified(false);setSources([]);setSourceOffset(0);setSourceMore(false);if(id)void act(()=>loadSources(id));}}><option value="">请选择</option>{sourceVersions.map(v=><option key={v.id} value={v.id}>{v.title} · 版本 {v.number}</option>)}</select></label>
      {sourceMore&&<button disabled={busy||!sourceVersion} onClick={()=>act(()=>loadSources(sourceVersion,sourceOffset))}>加载后续政策片段</button>}
      <label>适用商品编码（用逗号分隔）<input value={ruleSkus} onChange={e=>{setRuleSkus(e.target.value);setVerified(false);}}/></label>
      <label>实际签收后的申请天数<input type="number" min={1} max={365} value={ruleDays} onChange={e=>{setRuleDays(Number(e.target.value));setVerified(false);}}/></label>
      <label><input type="checkbox" checked={ruleUnused} onChange={e=>{setRuleUnused(e.target.checked);setVerified(false);}}/>规则要求商品未使用</label>
      <label>政策适用退货分支<select aria-label="政策适用退货分支" value={ruleBasis} onChange={e=>{const value=e.target.value;setRuleBasis(value);setVerified(false);if(value==="quality_issue"){setRuleUnused(false);setRequiredFacts(old=>Array.from(new Set([...old,"quality_verdict"])));setQualityRequirement("defect_confirmed");}else setQualityRequirement("recorded");}}>{Object.entries(bases).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      <p>激活、会员权益目前没有查询接口；质检读取独立处理人登记的演示工单。用户描述和政策片段不能替代实际记录。</p>
      {["activation_status","member_entitlement","quality_verdict"].map(field=><label key={field}><input type="checkbox" checked={requiredFacts.includes(field)} onChange={e=>{setRequiredFacts(old=>e.target.checked?[...old,field]:old.filter(f=>f!==field));setVerified(false);}}/>须核实{missingLabels[field]}</label>)}
      {requiredFacts.includes("quality_verdict")&&<label>原文对质检的要求<select aria-label="政策质检要求" value={qualityRequirement} onChange={e=>{setQualityRequirement(e.target.value);setVerified(false);}} disabled={ruleBasis==="quality_issue"}><option value="recorded">需要明确结果，不额外要求通过</option><option value="passed">原文明确要求质检通过</option><option value="defect_confirmed" disabled={ruleBasis!=="quality_issue"}>质量问题分支：明确确认缺陷</option></select></label>}
      {requiredFacts.includes("quality_verdict")&&<label>质检要求原文<select aria-label="质检要求原文" value={clauseRefs.quality_clause?.chunk_id||""} onChange={e=>{const source=sources.find(s=>s.id===e.target.value);setClauseRefs(old=>({...old,quality_clause:{chunk_id:source?.id||"",quote:source?.content||""}}));setVerified(false);}}><option value="">请选择原文</option>{sources.map((s:any)=><option key={s.id} value={s.id}>{s.section||s.title} · {s.content.slice(0,45)}</option>)}</select></label>}
      {[["scope_clause","适用商品条款"],["window_clause","申请期限条款"],["condition_clause","商品状态条款"]].map(([key,label])=><div key={key}><label>{label}<select value={clauseRefs[key]?.chunk_id||""} onChange={e=>{const source=sources.find(s=>s.id===e.target.value);setClauseRefs(old=>({...old,[key]:{chunk_id:source?.id||"",quote:source?.content||""}}));setVerified(false);}}><option value="">选择政策原文</option>{sources.map((s:any)=><option key={s.id} value={s.id}>{s.section||s.title} · {s.content.slice(0,45)}</option>)}</select></label><textarea aria-label={label+"原文"} rows={3} maxLength={2000} value={clauseRefs[key]?.quote||""} onChange={e=>{setClauseRefs(old=>({...old,[key]:{chunk_id:old[key]?.chunk_id||"",quote:e.target.value}}));setVerified(false);}}/></div>)}
      <label><input type="checkbox" checked={verified} onChange={e=>setVerified(e.target.checked)}/>我已核对商品、期限和状态配置与上述原文一致</label>
      <button disabled={busy||!verified||!sourceVersion||!ruleSkus.trim()||(requiredFacts.includes("quality_verdict")&&!clauseRefs.quality_clause?.chunk_id)} onClick={()=>act(async()=>{const {quality_clause,...baseClauses}=clauseRefs;await api("/returns/policies",token,{version_id:sourceVersion,skus:ruleSkus.split(/[,，]/).map(s=>s.trim()).filter(Boolean),window_days:ruleDays,requires_unused:ruleUnused,return_basis:ruleBasis,required_facts:requiredFacts,quality_requirement:qualityRequirement,...baseClauses,...(requiredFacts.includes("quality_verdict")?{quality_clause}:{}),administrator_verified:true});setVerified(false);})}>登记已核对规则</button>
    </details>}
    <QualityInspections token={token} role={role} binding={{order_number:number,item_id:item,quantity,reason}}/>
    <h3>申请记录</h3><button disabled={busy} onClick={()=>act(async()=>{})}>刷新退货记录</button>
    {rows.map(row=><p key={row.id}><button disabled={busy} onClick={()=>act(()=>open(row))}>{row.order_number} · {statuses[row.status]||row.status} · {row.id.slice(0,8)}</button></p>)}
    {selected&&<article><h3>当前申请：{statuses[selected.status]}</h3><p>版本 {selected.revision} · 数量 {selected.quantity} · {selected.reason}</p><p>商品金额参考值：{(selected.goods_value_cents/100).toFixed(2)}；实际退款金额：未确认。</p>
      {preview&&<><p>核对结果：{preview.eligibility==="eligible"?"满足当前演示规则，可提交审批":preview.eligibility==="needs_information"?"需补充信息":"当前条件不满足"}</p>
        {preview.missing?.map((m:string)=><p key={m}>待核实：{missingLabels[m]||m}</p>)}{preview.blocked?.map((b:string)=><p key={b}>限制：{b}</p>)}
        <details><summary>政策条款与来源</summary>{preview.evidence.clauses.map((c:any)=><blockquote key={c.purpose}>{c.quote}<small>来源 {c.chunk_id}</small></blockquote>)}</details>
        {role==="support"&&selected.status==="draft"&&<button disabled={busy||preview.eligibility!=="eligible"} onClick={()=>act(()=>action("confirm",{preview_hash:preview.preview_hash}))}>确认本版本并提交审批</button>}
      </>}
      {role==="approver"&&selected.status==="pending_approval"&&<><button disabled={busy} onClick={()=>act(()=>action("decision",{action:"approve",reason:"审批员已核对本版本与政策"}))}>批准退货申请</button><button disabled={busy} onClick={()=>act(()=>action("decision",{action:"reject",reason:"审批员拒绝本申请"}))}>拒绝退货申请</button></>}
      {role==="support"&&selected.status==="approved"&&<button disabled={busy} onClick={()=>act(()=>action("submit-simulated"))}>提交模拟退货受理</button>}
      {role==="support"&&["draft","pending_approval","approved"].includes(selected.status)&&<button disabled={busy} onClick={()=>act(()=>action("cancel"))}>取消退货申请</button>}
      {selected.receipt_id&&<p>模拟受理编号：{selected.receipt_id}；真实退款未执行。</p>}
      <ol>{selected.events.map((e:any,i:number)=><li key={i}>{e.action} · 版本 {e.revision}</li>)}</ol>
    </article>}
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
