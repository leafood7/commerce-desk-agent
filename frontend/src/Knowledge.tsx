import { useEffect, useState, type FormEvent } from "react";
import { api, watchRun } from "./api";

const scopes: Record<string,string> = {
  unknown:"未标注，需核实", general:"通用政策", goods_coupon:"商品券",
  freight_coupon:"运费券", returns:"退货", shipping:"发货与物流"
};
type Version = {id:string;number:number;filename:string;status:string;published:boolean;policy_scope:string;
  chunk_count:number;error_code:string|null;job:{attempts:number};
  parse_metadata:{warnings?:{code:string;page?:number}[]}};
type Document = {id:string;title:string;active_version_id:string|null;versions:Version[]};
type Evidence = {id:string;version_id:string;version:number;title:string;filename:string;
  content:string;section:string;page_start:number|null;page_end:number|null;policy_scope:string};
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
  const [documents,setDocuments] = useState<Document[]>([]);
  const [title,setTitle] = useState("");
  const [target,setTarget] = useState("");
  const [scope,setScope] = useState("unknown");
  const [strategy,setStrategy] = useState("structural");
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
    <p className="muted">只有当前商家已发布且在有效期内的版本，才会参与政策检索。</p>
    {role==="admin" && <form onSubmit={upload} className="upload-form"><fieldset disabled={busy}>
      <label>上传到<select aria-label="上传到" value={target} onChange={event => {
        const id=event.target.value;setTarget(id);
        setScope(documents.find(row=>row.id===id)?.versions[0]?.policy_scope || "unknown");
      }}><option value="">新建文档</option>{documents.map(row=><option value={row.id} key={row.id}>{row.title}（添加版本）</option>)}</select></label>
      {!target && <label>文档名称<input value={title} onChange={e=>setTitle(e.target.value)} placeholder="可留空，使用文件名" maxLength={200}/></label>}
      <label>适用范围<select aria-label="适用范围" value={scope} onChange={e=>setScope(e.target.value)}>{Object.entries(scopes).map(([value,label])=>
        <option key={value} value={value}>{label}</option>)}</select></label>
      <label>分块方式<select aria-label="分块方式" value={strategy} onChange={e=>setStrategy(e.target.value)}>
        <option value="structural">按章节与段落</option><option value="fixed">定长重叠</option></select></label>
      <label>政策文件<input type="file" accept=".txt,.md,.pdf,.docx" required onChange={e=>setFile(e.target.files?.[0]||null)}/></label>
      <p className="muted">支持 UTF-8 文本、Markdown、文本型 PDF 和普通 DOCX，单文件不超过 20 MB。扫描件需要先做 OCR。</p>
      <button disabled={busy||!file}>{busy?"正在提交…":"上传并处理"}</button>
    </fieldset></form>}
    {notice&&<p className="success" role="status">{notice}</p>}{error&&<p className="error" role="alert">{error}</p>}
    {!documents.length&&<p className="empty">尚无文档。管理员上传并发布后，客服即可检索。</p>}
    {documents.map(document=><article className="document-card" key={document.id}>
      <div className="row"><h3>{document.title}</h3>{role==="admin"&&document.active_version_id&&
        <button className="quiet" disabled={busy} onClick={()=>act("/knowledge/documents/"+document.id+"/withdraw")}>撤回当前版本</button>}</div>
      {document.versions.map(version=><div className="version-row" key={version.id}>
        <div><strong>v{version.number} · {version.filename}</strong>
          <p className="muted">{version.published?"当前已发布":states[version.status]||version.status} · {scopes[version.policy_scope]} · {version.chunk_count} 个片段</p>
          {version.error_code&&<p className="error">{version.error_code==="no_extractable_text" ?
            "未提取到正文，请提供文本文件或先做 OCR。" : "处理未完成：" + version.error_code}</p>}
          {!!version.parse_metadata?.warnings?.length&&<p>有 {version.parse_metadata.warnings.length} 页未提取到文本，请核对原文件。</p>}
        </div>
        <div className="actions"><button className="quiet" onClick={()=>download(token,version.id,version.filename).catch(e=>setError(e.message))}>下载原文件</button>
          {role==="admin"&&version.status==="ready"&&!version.published&&<>
            {!!version.parse_metadata?.warnings?.length&&<label><input type="checkbox" checked={ack[version.id]||false}
              onChange={e=>setAck({...ack,[version.id]:e.target.checked})}/>已核对缺失页面</label>}
            <button disabled={busy || (!!version.parse_metadata?.warnings?.length&&!ack[version.id])}
              onClick={()=>act("/knowledge/versions/"+version.id+"/publish",{acknowledge_warnings:!!ack[version.id]})}>发布 v{version.number}</button>
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
  const [scope,setScope] = useState("");
  const [answer,setAnswer] = useState("");
  const [evidence,setEvidence] = useState<Evidence[]>([]);
  const [steps,setSteps] = useState<string[]>([]);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  async function search(event:FormEvent) {
    event.preventDefault();setBusy(true);setError("");setEvidence([]);setAnswer("");setSteps([]);
    try {
      const run=await api("/runs",token,{kind:"knowledge_search",query,policy_scope:scope||null},
        {"Idempotency-Key":crypto.randomUUID()});
      const labels:Record<string,string>={"run.queued":"检索已排队","run.started":"开始处理",
        "tool.started":"检索已发布政策","tool.finished":"已取得检索结果",
        "answer.published":"依据已发布","run.finished":"处理结束","tool.failed":"检索未完成"};
      await watchRun(run.id,token,event=>setSteps(previous=>[...previous,labels[event.type]||event.type]));
      const result=await api("/runs/"+run.id,token);
      if(result.status!=="completed") throw new Error("本次检索未完成，请稍后重试。");
      setAnswer(result.output.answer);setEvidence(result.output.evidence||[]);
    } catch(problem) {setError((problem as Error).message);}
    finally {setBusy(false);}
  }
  return <section className="knowledge"><h2>政策检索</h2>
    <p className="muted">此入口用于规则问答，展示已发布原文与来源。办理退货请进入退货申请；原文不代表订单事实已确认。未标注范围的资料仍需核对适用条件。</p>
    <form onSubmit={search}><label>政策问题<textarea value={query} onChange={e=>setQuery(e.target.value)}
      placeholder="例如：运费券可以用于抵扣商品金额吗？" required maxLength={1200}/></label>
      <label>问题涉及的范围<select aria-label="问题涉及的范围" value={scope} onChange={e=>setScope(e.target.value)}>
        <option value="">尚未确认 / 全部范围</option>{Object.entries(scopes).filter(([key])=>key!=="unknown").map(([value,label])=>
          <option key={value} value={value}>{label}</option>)}</select></label>
      <button disabled={busy}>{busy?"正在检索…":"检索政策"}</button></form>
    {!!steps.length&&<ol className="inline-steps">{steps.map((step,index)=><li key={index}>{step}</li>)}</ol>}
    {error&&<p className="error" role="alert">{error}</p>}
    {answer&&<p className="answer" role="status">{answer}</p>}
    {evidence.map(item=><article className="evidence" key={item.id}>
      <div className="row"><h3>{item.title}</h3><span className="badge">{scopes[item.policy_scope]}</span></div>
      <blockquote>{item.content}</blockquote>
      <p className="muted">{item.filename} · v{item.version}{item.page_start ? " · 第 "+item.page_start+" 页" : ""}
        {item.section ? " · "+item.section : ""}</p>
      <button className="quiet" onClick={()=>download(token,item.version_id,item.filename).catch(e=>setError(e.message))}>查看原文件</button>
    </article>)}
  </section>;
}
