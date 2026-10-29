import {useEffect,useState} from "react";
import {api} from "./api";

const states:Record<string,string>={requested:"待处理",in_progress:"处理中",completed:"已登记结果",voided:"已撤销"};
const results:Record<string,string>={passed:"通过",failed:"未通过",inconclusive:"无法确定"};

export function QualityInspections({token,role,binding}:{token:string;role:string;binding:{order_number:string;item_id:string;quantity:number;reason:string}}){
  const [rows,setRows]=useState<any[]>([]),[selected,setSelected]=useState<any>(null);
  const [note,setNote]=useState(""),[verdict,setVerdict]=useState("inconclusive");
  const [finding,setFinding]=useState("");
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  async function reload(){setRows((await api("/quality/inspections",token)).items);}
  useEffect(()=>{void reload().catch(e=>setError(e.message));},[token]);
  async function act(task:()=>Promise<void>){setBusy(true);setError("");try{await task();await reload();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function open(id:string){setSelected(await api("/quality/inspections/"+id,token));setNote("");}
  async function transition(action:string){const row=await api(`/quality/inspections/${selected.id}/transition`,token,{action,expected_revision:selected.revision,note,...(action==="complete"?{verdict,...(finding?{finding}:{})}:{})});await open(row.id);}
  return <article><h3>演示质检工单</h3>
    <p>客服按当前订单商品和数量创建；审批角色登记演示结果。工单不是退货批准，不连接真实质检设备，不执行退款。新工单或撤销会使旧结论失效。</p>
    {role==="support"&&<button disabled={busy||!binding.item_id||!binding.reason.trim()} onClick={()=>act(async()=>{const row=await api("/quality/inspections",token,binding,{"Idempotency-Key":crypto.randomUUID()});await open(row.id);})}>为当前商品及数量创建演示质检</button>}
    <button disabled={busy} onClick={()=>act(reload)}>刷新质检工单</button>
    <label>选择质检工单<select aria-label="选择质检工单" value={selected?.id||""} disabled={busy} onChange={e=>{if(e.target.value)void act(()=>open(e.target.value));}}><option value="">请选择</option>{rows.map(row=><option key={row.id} value={row.id}>{row.id.slice(0,8)} · {states[row.status]} · 数量{row.quantity} · {row.reason}</option>)}</select></label>
    {selected&&<><p>工单 {selected.id} · 商品行 {selected.item_id} · 数量 {selected.quantity} · 修订 {selected.revision}</p><p role="status">演示质检状态：{states[selected.status]}；结论：{results[selected.verdict]||"尚无明确结果"}</p><p>{selected.result_note}</p>
      <p>缺陷判定：{({defect_confirmed:"明确存在缺陷（演示）",no_defect:"未发现缺陷（演示）",unclear:"无法确定"} as Record<string,string>)[selected.finding]||"尚未独立登记；质检未通过不等于确认缺陷"}</p>
      {role==="approver"&&selected.status==="in_progress"&&<label>独立缺陷判定<select aria-label="独立缺陷判定" disabled={busy} value={finding} onChange={e=>{setFinding(e.target.value);if(e.target.value)setVerdict(({defect_confirmed:"failed",no_defect:"passed",unclear:"inconclusive"} as Record<string,string>)[e.target.value]);}}><option value="">未登记（兼容旧质检）</option><option value="defect_confirmed">明确存在缺陷（演示）</option><option value="no_defect">未发现缺陷（演示）</option><option value="unclear">无法确定</option></select></label>}
      {(role==="approver"||role==="admin")&&<label>处理说明<textarea aria-label="质检处理说明" disabled={busy} value={note} maxLength={500} onChange={e=>setNote(e.target.value)}/></label>}
      {role==="approver"&&selected.status==="requested"&&<button disabled={busy||!note.trim()} onClick={()=>act(()=>transition("start"))}>开始演示质检</button>}
      {role==="approver"&&selected.status==="in_progress"&&<><label>演示质检结论<select aria-label="演示质检结论" disabled={busy} value={verdict} onChange={e=>setVerdict(e.target.value)}>{Object.entries(results).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><button disabled={busy||!note.trim()} onClick={()=>act(()=>transition("complete"))}>登记演示质检结果</button></>}
      {role==="admin"&&selected.status!=="voided"&&<button disabled={busy||!note.trim()} onClick={()=>act(()=>transition("void"))}>撤销此演示质检记录</button>}
      <details><summary>质检处理记录</summary><ol>{selected.events?.map((event:any)=><li key={event.revision}>修订{event.revision} · {({requested:"申请质检",start:"开始处理",complete:"登记结果",void:"撤销记录"} as Record<string,string>)[event.action]} · {event.details.note||"创建工单"}</li>)}</ol></details>
    </>}{error&&<p role="alert" className="error">{error}</p>}
  </article>;
}
