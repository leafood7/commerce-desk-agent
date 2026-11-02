import {useEffect,useState} from 'react';
import {api} from './api';
export function KnowledgeImpactPanel({token,version,candidateId,onClose,onConfirm,operation='publish'}:{token:string;version:any;candidateId?:string;onClose:()=>void;onConfirm?:()=>Promise<void>;operation?:string}){
 const [data,setData]=useState<any>(null),[error,setError]=useState(''),[ack,setAck]=useState(false),[busy,setBusy]=useState(false);
 useEffect(()=>{api('/knowledge/versions/'+version.id+'/update-impact'+(candidateId?'?candidate_id='+candidateId:''),token).then(setData).catch(e=>setError(e.message));},[token,version.id,candidateId]);
 return <article className="document-card" aria-label="知识更新影响"><h3>知识更新影响 · v{version.number}</h3><button onClick={onClose}>关闭更新影响</button>
 {error&&<p role="alert">{error}</p>}{data&&<><p>{data.title} · {operation==='withdraw'?'撤回后原文将停止用于新检索':data.annotation_update_only?'原文版本不变，更新审核整理':'切换后仅当前有效版本参与新检索'}</p>
 <p>关联FAQ：{data.faqs.length}项 · 需核对的政策整理：{data.policy_candidates.length}项 · 原退货规则：{data.return_rules.length}项</p>
 {data.faqs.map((f:any)=><p key={f.id}>FAQ：{f.title} · v{f.version}，需要重新核对。</p>)}
 {!!data.bound_open_runs&&<p>有{data.bound_open_runs}个未结束流程明确绑定了列出的原退货规则。此数不包含所有咨询；恢复时重新核对，不自动迁移审批。</p>}
 <p>发布、撤回或回退后，系统会生成知识更新待办。FAQ不会因政策回退自动恢复；退货规则与旧流程绑定不会被自动替换。</p>
 <details><summary>查看原文片段变化</summary>{data.changes.map((c:any,i:number)=><div key={i}><strong>{({replace:'修改',insert:'新增',delete:'移除'} as Record<string,string>)[c.kind]}</strong><p>原版本</p><pre style={{whiteSpace:'pre-wrap'}}>{c.before.join('\n\n')||'无'}</pre><p>目标版本</p><pre style={{whiteSpace:'pre-wrap'}}>{c.after.join('\n\n')||'无'}</pre></div>)}{!data.changes.length&&<p>当前预览没有原文片段变化；审核整理仍可能换版。</p>}{data.diff_truncated&&<p>这里只显示部分差异，请核对完整原文，不能据此认为其他内容未变。</p>}</details>
 {onConfirm&&<><label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>已了解受影响内容和待维护事项</label><button disabled={busy||!ack} onClick={async()=>{setBusy(true);try{await onConfirm();onClose();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>{operation==='withdraw'?'确认撤回当前政策':operation==='rollback'?'确认回退政策':'确认发布此版本'}</button></>}
 </>}
 </article>;
}

