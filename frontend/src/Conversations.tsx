import {useEffect,useRef,useState} from "react";
import {api} from "./api";

export function Conversations({token,onContinue,onResume}:{token:string;onContinue:(id:string)=>void;onResume:(id:string,conversation:string)=>void}){
  const [items,setItems]=useState<any[]>([]),[selected,setSelected]=useState(""),[messages,setMessages]=useState<any[]>([]),[runs,setRuns]=useState<any[]>([]);
  const [cursor,setCursor]=useState<string|null>(null),[offset,setOffset]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const selection=useRef("");selection.current=selected;
  useEffect(()=>{let active=true;api("/conversations?limit=20&offset="+offset,token).then(data=>{if(active)setItems(data.items);})
    .catch(e=>{if(active)setError(e.message);});return ()=>{active=false;};},[token,offset]);
  useEffect(()=>{let active=true;setMessages([]);setRuns([]);setCursor(null);if(!selected)return;
    Promise.all([api("/conversations/"+selected+"/messages?limit=50",token),api("/conversations/"+selected+"/runs",token)])
      .then(([journal,history])=>{if(active){setMessages(journal.items);setCursor(journal.next_before);setRuns(history.items);}})
      .catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[selected,token]);
  async function older(){
    if(!selected||!cursor)return;const id=selected;setBusy(true);setError("");
    try{const data=await api("/conversations/"+id+"/messages?limit=50&before="+encodeURIComponent(cursor),token);
      if(selection.current===id){setMessages(previous=>[...data.items,...previous].filter((row,index,all)=>all.findIndex(item=>item.id===row.id)===index));setCursor(data.next_before);}}
    catch(e){if(selection.current===id)setError((e as Error).message);}finally{setBusy(false);}
  }
  return <section className="knowledge"><h2>会话记录</h2>
    <p>只展示本账号已公开的消息。历史回复不会自动作为政策证据；新任务请明确当前订单与条件。</p>
    <label>选择会话<select value={selected} onChange={e=>{setSelected(e.target.value);setError("");}}>
      <option value="">请选择</option>{items.map(row=><option key={row.id} value={row.id}>{row.title} · {row.id.slice(0,8)}</option>)}</select></label>
    <div className="actions"><button className="quiet" disabled={!offset} onClick={()=>setOffset(value=>Math.max(0,value-20))}>上一页会话</button>
      <button className="quiet" disabled={items.length<20} onClick={()=>setOffset(value=>value+20)}>下一页会话</button></div>
    {selected&&<><button onClick={()=>onContinue(selected)}>在此会话开始新任务</button>
      {cursor&&<button disabled={busy} className="quiet" onClick={older}>加载更早消息</button>}
      <div className="conversation-journal">{messages.map(row=><article className="document-card" key={row.id}><strong>{row.role==="human"?"用户":"助手"}</strong><p>{row.content}</p></article>)}</div>
      {!!runs.length&&<details><summary>该会话的近期流程</summary>{runs.map(row=><p key={row.id}>{row.id.slice(0,8)} · {row.status}
        {row.kind==="aftersales"&&<button className="quiet" onClick={()=>onResume(row.id,selected)}>打开售后流程 {row.id.slice(0,8)}</button>}</p>)}</details>}
    </>}
    {!items.length&&<p>当前没有可显示的会话。</p>}{error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
