import {useEffect,useState} from "react";
import {api} from "./api";

export function ParseRevisionPanel({token,role,version,activeVersion,onDone,onClose}:{token:string;role:string;version:any;activeVersion:string|null;onDone:()=>Promise<void>;onClose:()=>void}){
  const [parsed,setParsed]=useState<any>(null),[edits,setEdits]=useState<Record<number,string>>({});
  const [reason,setReason]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  async function load(offset=0){setParsed(await api(`/knowledge/versions/${version.id}/parse?offset=${offset}&limit=5`,token));setEdits({});}
  useEffect(()=>{load().catch(e=>setError(e.message));},[token,version.id]);
  async function action(path:string,body:any){setBusy(true);setError("");try{await api(path,token,body);setNotice("操作已记录，请核对版本状态。");await onDone();await load();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  const revision=parsed?.revision;
  const dirty=parsed?.blocks.some((b:any)=>edits[b.index]!==undefined&&edits[b.index]!==b.text);
  const editable=role==="admin"&&version.id===activeVersion;
  return <article className="document-card" aria-label="解析修订面板">
    <h3>核对解析与修订 · v{version.number}</h3><button className="quiet" onClick={onClose}>关闭修订面板</button>
    <p>原文件保留。这里修改的是解析正文，会生成新版本；审核批准后仍需管理员发布。原有退货规则映射需另行核对，不自动迁移。</p>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {parsed&&<><p>正文类型：{parsed.text_origin==="human_corrected_parse"?"人工修订的解析文本":"原始解析文本"} · 共 {parsed.total} 段</p>
      {revision&&<details open><summary>本版修订差异与审核</summary><p>修改原因：{revision.reason} · {({pending:"待独立审核",approved:"审核通过",rejected:"已驳回"} as any)[revision.review_status]}</p>
        <p>提出者 {revision.proposed_by_name||"修订者"} · {revision.proposed_at}{revision.reviewed_by?` · 审核者 ${revision.reviewed_by_name||"审核者"} · ${revision.review_reason}`:""}</p>
        {(revision.changes||[]).map((change:any)=><div key={change.block_index}><strong>第 {change.block_index+1} 段 · {change.section}</strong><p>修订前</p><pre style={{whiteSpace:"pre-wrap"}}>{change.before}</pre><p>修订后</p><pre style={{whiteSpace:"pre-wrap"}}>{change.after}</pre></div>)}
      </details>}
      {parsed.blocks.map((block:any)=><div key={block.index}><label>第 {block.index+1} 段 · {block.section||"未标标题"}{block.page?` · 第${block.page}页`:""}
        <textarea aria-label={`解析段落 ${block.index+1}`} rows={5} disabled={!editable||busy||!block.editable} value={edits[block.index]??block.text} onChange={e=>setEdits({...edits,[block.index]:e.target.value})}/>
      </label>{block.truncated&&<p>段落过长，当前仅展示前部分，请下载原文件核对后上传新文件。</p>}</div>)}
      <button className="quiet" disabled={busy||dirty||parsed.offset===0} onClick={()=>load(Math.max(0,parsed.offset-5)).catch(e=>setError(e.message))}>上一批段落</button>
      <button className="quiet" disabled={busy||dirty||parsed.next_offset===null} onClick={()=>load(parsed.next_offset).catch(e=>setError(e.message))}>下一批段落</button>
      {dirty&&<p>本页有未提交修改。<button className="quiet" onClick={()=>setEdits({})}>放弃本页未提交修改</button></p>}
      <label>修改、审核或回退原因<textarea aria-label="修订处理原因" maxLength={500} value={reason} onChange={e=>setReason(e.target.value)}/></label>
      {editable&&<button disabled={busy||reason.trim().length<5||!parsed.blocks.some((b:any)=>edits[b.index]!==undefined&&edits[b.index]!==b.text)} onClick={()=>action(`/knowledge/versions/${version.id}/revisions`,{expected_active_version_id:activeVersion,reason,patches:parsed.blocks.filter((b:any)=>edits[b.index]!==undefined&&edits[b.index]!==b.text).map((b:any)=>({block_index:b.index,before_sha256:b.sha256,text:edits[b.index]}))})}>提交解析修订新版本</button>}
      {role==="approver"&&revision?.review_status==="pending"&&version.status==="ready"&&<><button disabled={busy||reason.trim().length<5} onClick={()=>action(`/knowledge/versions/${version.id}/review`,{action:"approve",reason,config_hash:version.config_hash})}>批准解析修订</button><button disabled={busy||reason.trim().length<5} onClick={()=>action(`/knowledge/versions/${version.id}/review`,{action:"reject",reason,config_hash:version.config_hash})}>驳回解析修订</button></>}
      {role==="admin"&&activeVersion&&activeVersion!==version.id&&version.published_at&&version.status==="ready"&&(!revision||revision.review_status==="approved")&&<button disabled={busy||reason.trim().length<5||version.source_review_required} onClick={()=>action(`/knowledge/documents/${version.document_id}/rollback`,{version_id:version.id,expected_active_version_id:activeVersion,reason})}>回退到此已发布版本</button>}
    </>}
  </article>;
}
