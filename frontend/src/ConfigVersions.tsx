import {useEffect,useState,type FormEvent} from "react";
import {api} from "./api";

export function ConfigVersions({token,role}:{token:string;role:string}){
  const [current,setCurrent]=useState<any>(null),[versions,setVersions]=useState<any[]>([]);
  const [alias,setAlias]=useState("rules"),[tokens,setTokens]=useState(256),[top,setTop]=useState(3),[tone,setTone]=useState("plain");
  const [returnDecisions,setReturnDecisions]=useState("rules"),[returnRetrieval,setReturnRetrieval]=useState("baseline");
  const [returnFlow,setReturnFlow]=useState("guided_v3");
  const [selected,setSelected]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  async function load(preferred=""){
    const value=await api("/runtime/config",token);setCurrent(value);
    if(role==="admin"){
      const rows=await api("/runtime/config/versions?limit=50",token);
      setVersions(rows.items);setSelected(preferred||value.current.id||rows.items[0]?.id||"");
    }
  }
  useEffect(()=>{let active=true;
    api("/runtime/config",token).then(value=>{if(active){setCurrent(value);setAlias(value.allowed_model_aliases.includes(value.current.values.model_alias)?value.current.values.model_alias:"rules");
      setTokens(value.current.values.max_output_tokens);setTop(value.current.values.top_k);setTone(value.current.values.tone);
      setReturnDecisions(value.current.values.return_decisions||"rules");setReturnRetrieval(value.current.values.return_retrieval_mode||"baseline");}})
      .catch(e=>{if(active)setError(e.message);});
    if(role==="admin")api("/runtime/config/versions?limit=50",token).then(rows=>{if(active){setVersions(rows.items);setSelected(rows.items[0]?.id||"");}})
      .catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[token,role]);
  async function draft(event:FormEvent){
    event.preventDefault();setBusy(true);setError("");setNotice("");
    try{
      const row=await api("/runtime/config/versions",token,{model_alias:alias,prompt_template:"intent_v1",
        max_output_tokens:tokens,top_k:top,tone,return_decisions:returnDecisions,return_retrieval_mode:returnRetrieval,return_flow_version:returnFlow});
      setNotice("已保存草稿 v"+row.number+"，尚未影响运行。");await load(row.id);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function publish(){
    if(!current||!selected)return;setBusy(true);setError("");setNotice("");
    try{
      const value=await api("/runtime/config/versions/"+selected+"/publish",token,{expected_revision:current.revision});
      setNotice("已发布 v"+value.current.number+"；仅影响之后创建的新运行。");await load(selected);
    }catch(e){setError((e as Error).message);await load(selected).catch(()=>{});}finally{setBusy(false);}
  }
  return <section className="knowledge"><h2>配置版本</h2>
    <p>发布后仅新运行采用该版本；排队中和已暂停流程保留创建时的快照。权限、审批、配额、密钥与服务地址不在这里修改。</p>
    {current&&<article className="document-card"><h3>当前配置：{current.current.id?"v"+current.current.number:"内置默认"}</h3>
      <p>发布序号 {current.revision} · 模板 intent_v1 · 检索候选 {current.current.values.top_k}</p>
      <p>模型模式：{current.current.values.model_alias==="rules"?"规则，不调用模型":current.allowed_model_aliases.includes("server_default")?"服务端已启用模型":"请求模型（服务端已关闭，将回退规则）"} · 输出上限 {current.current.values.max_output_tokens}</p>
      <p>用语：{current.current.values.tone==="plain"?"标准":"友好问候"}</p>
      <p>退货决策：{current.current.values.return_decisions==="model"?"受限模型建议，非法动作回退规则":"规则基线"} · 政策检索：{current.current.values.return_retrieval_mode==="clause_bound"?"当前分支已召回原文组装":current.current.values.return_retrieval_mode==="policy_scoped"?"按已核对政策版本收窄":"原检索范围"}</p>
      <small>配置指纹 {current.current.config_hash.slice(0,12)}</small>
    </article>}
    {role==="admin"&&current&&<>
      <form onSubmit={draft}><fieldset disabled={busy}><legend>创建新草稿</legend>
        <label>模型选择<select value={alias} onChange={e=>{setAlias(e.target.value);if(e.target.value==="rules")setReturnDecisions("rules");}}>
          {current.allowed_model_aliases.map((item:string)=><option value={item} key={item}>{item==="rules"?"规则（不调用模型）":"服务端已启用的模型"}</option>)}</select></label>
        <label>提示词模板<select value="intent_v1" disabled><option value="intent_v1">intent_v1（受控任务识别模板）</option></select></label>
        <p className="muted">模板正文由代码审查维护，不接收任意指令；温度固定为0。</p>
        <label>模型输出上限<select value={tokens} onChange={e=>setTokens(Number(e.target.value))}>
          {[128,256].filter(value=>value<=current.max_output_cap).map(value=><option key={value} value={value}>{value} token</option>)}</select></label>
        <label>检索候选数<select value={top} onChange={e=>setTop(Number(e.target.value))}>{[1,2,3].map(value=><option key={value}>{value}</option>)}</select></label>
        <label>回复用语<select value={tone} onChange={e=>setTone(e.target.value)}><option value="plain">标准</option><option value="friendly">增加友好问候</option></select></label>
        <label>退货动作决策<select value={returnDecisions} onChange={e=>setReturnDecisions(e.target.value)}><option value="rules">规则基线，不调用模型</option><option value="model" disabled={!current.return_model_available||alias!=="server_default"}>受限模型建议（需服务端启用）</option></select></label>
        <label>退货流程<select value={returnFlow} onChange={e=>setReturnFlow(e.target.value)}><option value="guided_v3">原话确认与事实边界核对</option><option value="observed_v2">上一版观察决策流程</option></select></label>
        <label>退货政策范围<select value={returnRetrieval} onChange={e=>setReturnRetrieval(e.target.value)}><option value="baseline">原检索范围</option><option value="policy_scoped">仅检索已核对的适用政策版本（实验）</option><option value="clause_bound">按当前分支组装已召回且核对的原文（实验）</option></select></label>
        <button disabled={busy}>保存配置草稿</button></fieldset></form>
      <label>待发布版本<select value={selected} onChange={e=>setSelected(e.target.value)}>
        <option value="">请选择</option>{versions.map(row=><option value={row.id} key={row.id}>v{row.number} · {row.id===current.current.id?"当前":row.published_at?"曾发布":"草稿"}</option>)}</select></label>
      <button disabled={busy||!selected} onClick={publish}>发布选中版本</button>
    </>}
    {role!=="admin"&&<p className="muted">当前账号可查看已发布配置；创建和发布由管理员操作。</p>}
    {notice&&<p role="status">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
