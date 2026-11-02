import {KnowledgeImpactPanel} from "./KnowledgeImpact";
import {KnowledgeUpdatesPanel} from "./KnowledgeUpdates";
import { useEffect, useState, type FormEvent } from "react";
import {PolicyTaskBoard,type TaskBoard} from "./PolicyTaskBoard";
import { api, watchRun } from "./api";
import {PolicyExtractionPanel} from "./PolicyExtraction";
import {ParseRevisionPanel} from "./ParseRevision";

const scopes: Record<string,string> = {
  unknown:"未标注，需核实", general:"通用政策", goods_coupon:"商品券",
  freight_coupon:"运费券", returns:"退货", shipping:"发货与物流"
};
type Version = {config_hash:string;published_at:string|null;document_id:string;knowledge_kind?:string;faq_sources?:string[];source_review_required?:boolean;id:string;number:number;filename:string;status:string;published:boolean;policy_scope:string;
  chunk_count:number;error_code:string|null;job:{attempts:number};
  parse_metadata:{faq_update_review?:{status:string};revision?:{review_status:string};text_origin?:string;audience?:Record<string,string>;warnings?:{code:string;page?:number}[]}};
type Document = {id:string;title:string;active_version_id:string|null;versions:Version[]};
type Evidence = {text_origin?:string;id:string;version_id:string;version:number;title:string;filename:string;
  parent_id?:string;audience?:Record<string,string>;content:string;section:string;page_start:number|null;page_end:number|null;policy_scope:string};
const states:Record<string,string> = {queued:"待处理",processing:"解析与索引中",ready:"待发布",failed:"处理失败"};

async function download(token:string, version:string, filename:string) {
  const response = await fetch("/api/v1/knowledge/versions/" + version + "/file",
    {headers:{Authorization:"Bearer " + token}});
  if (!response.ok) throw new Error("无法读取原文件。");
  const url = URL.createObjectURL(await response.blob());
  const anchor = window.document.createElement("a");
  anchor.href = url; anchor.download = filename; anchor.click();
  URL.revokeObjectURL(url);
}

export function KnowledgeManager({token,role}:{token:string;role:string}) {
  const [storage,setStorage]=useState<{backend:string;namespace:string}|null>(null);
  useEffect(()=>{let active=true;setStorage(null);if(role==="admin")void api("/knowledge/vector-storage",token).then(value=>{if(active)setStorage(value);}).catch(()=>{});return ()=>{active=false;};},[token,role]);
  const [showUpdates,setShowUpdates]=useState(false),[impact,setImpact]=useState<{version:Version;operation:string;documentId:string}|null>(null);
  const [candidateVersion,setCandidateVersion]=useState<Version|null>(null);
  const [inspect,setInspect]=useState<{version:Version;active:string|null}|null>(null);
  const [documents,setDocuments] = useState<Document[]>([]);
  const [title,setTitle] = useState("");
  const [target,setTarget] = useState("");
  const [scope,setScope] = useState("unknown");
  const [strategy,setStrategy] = useState("structural");
  const [knowledgeKind,setKnowledgeKind] = useState("document");
  const [faqSources,setFaqSources] = useState<string[]>([]);
  const [audience,setAudience] = useState<Record<string,string>>({});
  const [file,setFile] = useState<File|null>(null);
  const [busy,setBusy] = useState(false);
  const [notice,setNotice] = useState("");
  const [error,setError] = useState("");
  const [ack,setAck] = useState<Record<string,boolean>>({});
  async function refresh() { const result = await api("/knowledge/documents",token); setDocuments(result.items); }
  useEffect(() => {
    let active = true;
    const load = () => api("/knowledge/documents",token).then(result => {
      if (active) setDocuments(result.items);
    }).catch(problem => { if(active) setError(problem.message); });
    load();
    const interval = setInterval(load,1800);
    return () => {active=false;clearInterval(interval);};
  },[token]);
  async function upload(event:FormEvent) {
    event.preventDefault(); if (!file) return;
    setBusy(true);setError("");setNotice("");
    try {
      const form = new FormData();
      form.append("file",file);form.append("title",title || file.name);
      form.append("policy_scope",scope);form.append("chunk_strategy",strategy);
      form.append("knowledge_kind",knowledgeKind);form.append("faq_sources",JSON.stringify(knowledgeKind==="faq"?faqSources:[]));
      const conditions=Object.fromEntries(Object.entries(audience).filter(([,v])=>v.trim()));
      if(Object.keys(conditions).length) form.append("audience",JSON.stringify(conditions));
      if(target) form.delete("title");
      const result = await api(target ? "/knowledge/documents/" + target + "/versions" :
        "/knowledge/documents",token,form);
      setNotice(result.created ? "已接收文件，等待后台处理。处理完成后请核对并发布。" :
        "相同文件和配置已存在，已复用原版本。");
      await refresh();
    } catch(problem) { setError((problem as Error).message); }
    finally {setBusy(false);}
  }
  async function act(path:string,body:unknown={}) {
    setBusy(true);setError("");
    try { await api(path,token,body);await refresh(); }
    catch(problem) {setError((problem as Error).message);}
    finally {setBusy(false);}
  }
  return <section className="knowledge"><h2>知识库</h2>
    {storage&&<p className="muted" role="status">向量存储配置：{storage.backend==="qdrant"?"Qdrant":"Chroma"} · {storage.namespace||"原有默认索引"}。切换前须完成隔离重建；不会自动回退。</p>}
    <p className="muted">只有当前商家已发布且在有效期内的版本，才会参与政策检索。</p>
    {role==="admin" && <form onSubmit={upload} className="upload-form"><fieldset disabled={busy}>
      <label>上传到<select aria-label="上传到" value={target} onChange={event => {
        const id=event.target.value;setTarget(id);
        const chosen=documents.find(row=>row.id===id)?.versions[0];
        setScope(chosen?.policy_scope || "unknown");setKnowledgeKind(chosen?.knowledge_kind||"document");setFaqSources(chosen?.faq_sources||[]);setAudience(chosen?.parse_metadata?.audience||{});
      }}><option value="">新建文档</option>{documents.map(row=><option value={row.id} key={row.id}>{row.title}（添加版本）</option>)}</select></label>
      {!target && <label>文档名称<input value={title} onChange={e=>setTitle(e.target.value)} placeholder="可留空，使用文件名" maxLength={200}/></label>}
      <label>适用范围<select aria-label="适用范围" value={scope} onChange={e=>setScope(e.target.value)}>{Object.entries(scopes).map(([value,label])=>
        <option key={value} value={value}>{label}</option>)}</select></label>
      <label>资料类型<select value={knowledgeKind} onChange={e=>{setKnowledgeKind(e.target.value);setFaqSources([]);}}><option value="document">政策文档</option><option value="faq">审核FAQ（须关联政策来源）</option><option value="product">产品资料</option></select></label>
      {knowledgeKind==="faq"&&<label>FAQ政策来源（可多选）<select multiple value={faqSources} onChange={e=>setFaqSources(Array.from(e.target.selectedOptions,v=>v.value))}>{documents.flatMap(d=>d.versions.filter(v=>v.published&&v.knowledge_kind!=="faq").map(v=><option key={v.id} value={v.id}>{d.title} · v{v.number}</option>))}</select></label>}
      <label>分块方式<select aria-label="分块方式" value={strategy} onChange={e=>setStrategy(e.target.value)}>
        <option value="parent_child">父子分块（检索片段关联原文章节）</option><option value="structural">按章节与段落</option><option value="fixed">定长重叠</option></select></label>
      {Object.entries({merchant:"适用商家",region:"适用地区",channel:"适用渠道",product:"适用商品范围"}).map(([key,label])=><label key={key}>{label}<input maxLength={80} value={audience[key]||""} onChange={e=>setAudience({...audience,[key]:e.target.value})} placeholder="未确认则留空，不代表通用"/></label>)}
      <label>政策文件<input type="file" accept=".txt,.md,.pdf,.docx" required onChange={e=>setFile(e.target.files?.[0]||null)}/></label>
      <p className="muted">支持 UTF-8 文本、Markdown、文本型 PDF 和普通 DOCX，单文件不超过 20 MB。扫描件需要先做 OCR。</p>
      <button disabled={busy||!file}>{busy?"正在提交…":"上传并处理"}</button>
    </fieldset></form>}
    {["admin","approver"].includes(role)&&<button onClick={()=>setShowUpdates(true)}>知识更新待办</button>}
    {showUpdates&&<KnowledgeUpdatesPanel token={token} role={role} onDone={refresh} onClose={()=>setShowUpdates(false)}/>}
    {impact&&<KnowledgeImpactPanel token={token} version={impact.version} operation={impact.operation} onClose={()=>setImpact(null)} onConfirm={role==="admin"&&impact.operation!=="inspect"?async()=>{await api(impact.operation==="withdraw"?"/knowledge/documents/"+impact.documentId+"/withdraw":"/knowledge/versions/"+impact.version.id+"/publish",token,{acknowledge_warnings:!!ack[impact.version.id]});await refresh();}:undefined}/>}
    {notice&&<p className="success" role="status">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}
    {!documents.length&&<p className="empty">尚无文档。管理员上传并发布后，客服即可检索。</p>}
    {candidateVersion&&<PolicyExtractionPanel token={token} role={role} version={candidateVersion} onDone={refresh} onClose={()=>setCandidateVersion(null)}/>}
    {inspect&&<ParseRevisionPanel token={token} role={role} version={inspect.version} activeVersion={inspect.active} onDone={refresh} onClose={()=>setInspect(null)}/>}
    {documents.map(document=><article className="document-card" key={document.id}>
      <div className="row"><h3>{document.title}</h3>{role==="admin"&&document.active_version_id&&
        <button className="quiet" disabled={busy} onClick={()=>{const version=document.versions.find(v=>v.id===document.active_version_id);if(version)setImpact({version,operation:"withdraw",documentId:document.id});}}>撤回当前版本</button>}</div>
      {document.versions.map(version=><div className="version-row" key={version.id}>
        <div><strong>v{version.number} · {version.filename}</strong>
          <p className="muted">{version.published?"当前已发布":states[version.status]||version.status} · {scopes[version.policy_scope]} · {version.chunk_count} 个片段</p>
          {version.parse_metadata?.revision&&<p>人工修订解析 · {version.parse_metadata.revision.review_status==="approved"?"独立审核通过":version.parse_metadata.revision.review_status==="rejected"?"已驳回":"待独立审核"}</p>}
          {version.parse_metadata?.faq_update_review&&<p>联动FAQ：{({pending:"待独立审核",approved:"审核通过",rejected:"已驳回"} as Record<string,string>)[version.parse_metadata.faq_update_review.status]}，请在知识更新待办中处理。</p>}
          {version.source_review_required&&<p className="error">关联政策已变更或失效，FAQ暂停检索，请审核并发布新版本。</p>}
          {version.error_code&&<p className="error">{version.error_code==="no_extractable_text" ?
            "未提取到正文，请提供文本文件或先做 OCR。" : "处理未完成：" + version.error_code}</p>}
          {!!version.parse_metadata?.warnings?.length&&<p>有 {version.parse_metadata.warnings.length} 页未提取到文本，请核对原文件。</p>}
        </div>
        <div className="actions">{["admin","approver"].includes(role)&&version.status==="ready"&&<button className="quiet" onClick={()=>setImpact({version,operation:"inspect",documentId:document.id})}>查看更新影响 v{version.number}</button>}{["admin","approver"].includes(role)&&version.status==="ready"&&version.policy_scope==="returns"&&version.knowledge_kind!=="faq"&&<button className="quiet" onClick={()=>setCandidateVersion(version)}>政策提取与审核 v{version.number}</button>}{["admin","approver"].includes(role)&&version.status==="ready"&&<button className="quiet" onClick={()=>setInspect({version,active:document.active_version_id})}>核对解析 v{version.number}</button>}<button className="quiet" onClick={()=>download(token,version.id,version.filename).catch(e=>setError(e.message))}>下载原文件</button>
          {role==="admin"&&version.status==="ready"&&!version.published&&<>
            {!!version.parse_metadata?.warnings?.length&&<label><input type="checkbox" checked={ack[version.id]||false}
              onChange={e=>setAck({...ack,[version.id]:e.target.checked})}/>已核对缺失页面</label>}
            <button disabled={busy || (!!version.parse_metadata?.revision&&version.parse_metadata.revision.review_status!=="approved") || (!!version.parse_metadata?.faq_update_review&&version.parse_metadata.faq_update_review.status!=="approved") || !!version.source_review_required || (!!version.parse_metadata?.warnings?.length&&!ack[version.id])}
              onClick={()=>setImpact({version,operation:"publish",documentId:document.id})}>发布 v{version.number}</button>
          </>}
          {role==="admin"&&version.status==="failed"&&version.job.attempts<3&&<button disabled={busy}
            onClick={()=>act("/knowledge/versions/"+version.id+"/retry")}>重试处理</button>}
        </div>
      </div>)}
    </article>)}
  </section>;
}

export function PolicySearch({token}:{token:string}) {
  const [query,setQuery] = useState("");
  const [audience,setAudience] = useState<Record<string,string>>({});
  const [readings,setReadings] = useState<Record<string,{content:string;next_offset:number|null;complete:boolean;parent_id:string;related_sections:{parent_id:string;section:string}[]}>>({});
  const [scope,setScope] = useState("");
  const [answer,setAnswer] = useState("");
  const [evidence,setEvidence] = useState<Evidence[]>([]);
  const [bindings,setBindings] = useState<Record<string,number>>({});
  const [summaries,setSummaries]=useState<Record<string,any>>({});
  const [taskBoard,setTaskBoard]=useState<TaskBoard|null>(null);
  const [steps,setSteps] = useState<string[]>([]);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  async function search(event:FormEvent) {
    event.preventDefault();setBusy(true);setError("");setEvidence([]);setAnswer("");setSteps([]);setTaskBoard(null);setReadings({});setSummaries({});
    try {
      const run=await api("/runs",token,{kind:"knowledge_search",query,policy_scope:scope||null,audience:Object.fromEntries(Object.entries(audience).filter(([,v])=>v.trim()))},
        {"Idempotency-Key":crypto.randomUUID()});
      const labels:Record<string,string>={"run.queued":"检索已排队","run.started":"开始处理",
        "tool.started":"检索已发布政策","tool.finished":"已取得检索结果",
        "answer.check":"正在核对回答依据","answer.published":"依据已发布","run.finished":"处理结束","tool.failed":"检索未完成"};
      const checks:Record<string,string>={generating:"正在组织回答",reviewing:"正在核对回答依据",repairing:"发现问题，正在修订一次",reviewing_repair:"正在重新核验修订结果"};
      const agents:Record<string,string>={task_planner:"任务整理",evidence_coordinator:"证据协调",retrieval_agent:"取证助手",answer_agent:"回答助手",review_agent:"回答核验助手",repair_agent:"修订助手",supervisor:"协调步骤"};
      await watchRun(run.id,token,event=>{if(event.type==="agent.task_board"){setTaskBoard(event.payload?.board||null);return;}setSteps(previous=>[...previous,event.type==="tool.started"&&event.payload?.name==="read_policy_source"?"补读已授权原文":event.type==="tool.finished"&&event.payload?.name==="read_policy_source"?"已取得原文片段":event.type.startsWith("agent.")?(agents[event.payload?.agent]||"协作助手")+(event.type==="agent.started"?"开始处理":event.type==="agent.failed"?"未完成，已保守处理":"完成交接"):event.type==="answer.check"?(checks[event.payload?.phase]||labels[event.type]):(labels[event.type]||event.type)]);});
      const result=await api("/runs/"+run.id,token);
      if(result.status!=="completed") throw new Error("本次检索未完成，请稍后重试。");
      if(result.output.answer_gate?.collaboration)setTaskBoard(result.output.answer_gate.collaboration);
      setAnswer(result.output.answer);setEvidence(result.output.evidence||[]);setBindings(Object.fromEntries((result.output.answer_gate?.evidence_bindings||[]).map((r:{id:string;label:number})=>[r.id,r.label])));
    } catch(problem) {setError((problem as Error).message);}
    finally {setBusy(false);}
  }
  return <section className="knowledge"><h2>政策检索</h2>
    <p className="muted">此入口用于规则问答，展示已发布原文与来源。办理退货请进入退货申请；原文不代表订单事实已确认。未标注范围的资料仍需核对适用条件。</p>
    <form onSubmit={search}><label>政策问题<textarea disabled={busy} value={query} onChange={e=>{setQuery(e.target.value);setEvidence([]);setReadings({});setSummaries({});setAnswer("");}}
      placeholder="例如：运费券可以用于抵扣商品金额吗？" required maxLength={1200}/></label>
      <label>问题涉及的范围<select disabled={busy} aria-label="问题涉及的范围" value={scope} onChange={e=>{setScope(e.target.value);setEvidence([]);setReadings({});setSummaries({});setAnswer("");}}>
        <option value="">尚未确认 / 全部范围</option>{Object.entries(scopes).filter(([key])=>key!=="unknown").map(([value,label])=>
          <option key={value} value={value}>{label}</option>)}</select></label>
      {Object.entries({merchant:"商家",region:"地区",channel:"购买渠道",product:"商品范围"}).map(([key,label])=><label key={key}>{label}<input disabled={busy} maxLength={80} value={audience[key]||""} onChange={e=>{setAudience({...audience,[key]:e.target.value});setEvidence([]);setReadings({});setSummaries({});setAnswer("");}} placeholder="仅填写已确认条件"/></label>)}
      <button disabled={busy}>{busy?"正在检索…":"检索政策"}</button></form>
    {!!steps.length&&<ol className="inline-steps">{steps.map((step,index)=><li key={index}>{step}</li>)}</ol>}
    {error&&<p className="error" role="alert">{error}</p>}
    <PolicyTaskBoard board={taskBoard}/>
    {answer&&<p className="answer" role="status">{answer}</p>}
    {evidence.map(item=><article className="evidence" key={item.id}>
      <div className="row"><h3>{bindings[item.id]?"依据"+bindings[item.id]+" · ":""}{item.title}</h3><span className="badge">{scopes[item.policy_scope]}</span></div>
      <blockquote>{item.content}</blockquote>
      <p className="muted">{item.filename} · v{item.version}{item.page_start ? " · 第 "+item.page_start+" 页" : ""}
        {item.section ? " · "+item.section : ""}</p>
      {item.text_origin==="human_corrected_parse"&&<p className="muted">人工修订的解析文本，上传原文件保留。</p>}{item.parent_id&&<><button className="quiet" disabled={busy} onClick={async()=>{
        setBusy(true);setError("");
        try {const prior=readings[item.id]; const result=await api("/knowledge/read",token,{version_id:item.version_id,parent_id:prior?.parent_id||item.parent_id,offset:prior?.next_offset??0,max_chars:2000,audience:item.audience||{}});
          setReadings(previous=>({...previous,[item.id]:{content:(prior?.next_offset!=null?prior.content:"")+result.content,next_offset:result.next_offset,complete:result.next_offset===null,parent_id:result.parent_id,related_sections:result.related_sections||[]}}));
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
      }}>{!readings[item.id]?"阅读相关原文章节":readings[item.id].next_offset!==null?"继续阅读原文":"重新阅读章节"}</button>
      {readings[item.id]&&<div><p className="muted">{readings[item.id].complete?"本章节已读完；其他章节的例外仍需核对。":"仅显示部分章节，请继续阅读，不代表证据完整。"}</p><blockquote>{readings[item.id].content}</blockquote>
      {readings[item.id].related_sections.map(link=><button key={link.parent_id} className="quiet" disabled={busy} onClick={async()=>{
        setBusy(true);setError("");try{const result=await api("/knowledge/read",token,{version_id:item.version_id,parent_id:link.parent_id,max_chars:2000,audience:item.audience||{}});
          setReadings(previous=>({...previous,[item.id]:{content:result.content,next_offset:result.next_offset,complete:result.next_offset===null,parent_id:result.parent_id,related_sections:result.related_sections||[]}}));
        }catch(e){setError((e as Error).message);}finally{setBusy(false);}
      }}>阅读关联条款：{link.section}</button>)}</div>}</>}
      <button className="quiet" disabled={busy} onClick={async()=>{setBusy(true);setError("");try{const value=await api("/knowledge/versions/"+item.version_id+"/policy-summary",token);setSummaries(previous=>({...previous,[item.version_id]:value}));}catch{setError("该版本尚无可用的已审核政策条款，或来源已变化。请核对原文。");}finally{setBusy(false);}}}>查看已审核条款</button>
      {summaries[item.version_id]&&<details open><summary>人工审核的政策整理（不等于退货资格已确认）</summary>{summaries[item.version_id].cards.map((card:any)=><div key={card.id}><strong> {({scope:"适用范围",window:"期限",condition:"条件",exception:"例外",responsibility:"费用与责任"} as Record<string,string>)[card.category]||"审核条款"}</strong><p>{card.statement}</p><details><summary>查看对应原文</summary>{card.sources.map((source:any,index:number)=><blockquote key={index}>{source.quote}</blockquote>)}</details></div>)}</details>}
      <button className="quiet" onClick={()=>download(token,item.version_id,item.filename).catch(e=>setError(e.message))}>查看原文件</button>
    </article>)}
  </section>;
}
