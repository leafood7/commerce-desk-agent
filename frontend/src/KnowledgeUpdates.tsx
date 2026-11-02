import {useEffect,useState} from 'react';
import {api} from './api';
import {PolicyExtractionPanel} from './PolicyExtraction';
const statuses:Record<string,string>={pending:'待处理',prepared:'已准备替代版本',completed:'已完成',superseded:'被后续更新替代',dismissed:'不再维护，保留失效状态'};
const kinds:Record<string,string>={faq:'FAQ复核',extraction:'政策整理更新',return_rule:'退货规则核对'};
const actions:Record<string,string>={publish:'政策发布',withdraw:'政策撤回',rollback:'政策回退',annotation_update:'审核整理换版'};

export function KnowledgeUpdatesPanel({token,role,onDone,onClose}:{token:string;role:string;onDone:()=>Promise<void>;onClose:()=>void}){
 const [items,setItems]=useState<any[]>([]),[offset,setOffset]=useState(0),[more,setMore]=useState(false),[pending,setPending]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[reason,setReason]=useState('');
 const [sourcePreview,setSourcePreview]=useState<any>(null);
 const [editor,setEditor]=useState<any>(null),[content,setContent]=useState(''),[audience,setAudience]=useState<Record<string,string>>({}),[rule,setRule]=useState(''),[rules,setRules]=useState<any[]>([]),[candidate,setCandidate]=useState<any>(null);
 async function refresh(){const value=await api('/knowledge/updates?offset='+offset+'&limit=20&pending_only='+pending,token);setItems(value.items);setMore(value.has_more);}
 useEffect(()=>{refresh().catch(e=>setError(e.message));},[offset,pending,token]);
 async function openEditor(task:any){const value=await api('/knowledge/update-tasks/'+task.id+'/faq-editor',token);setEditor(value);setContent(value.content);setAudience(value.audience||{});setReason('');}
 async function act(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn();await refresh();await onDone();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const task=editor?.task,version=editor?.prepared_version,review=version?.parse_metadata?.faq_update_review;
 const editable=role==='admin'&&task&&['pending','prepared'].includes(task.status)&&(!version||review?.status==='rejected');
 return <article className="document-card" aria-label="知识更新待办"><h3>知识更新待办</h3><button onClick={onClose}>关闭知识更新待办</button><p>这里处理政策更新后的关联事项；不会自动改写FAQ、退货资格或旧审批。</p>
 <label><input type="checkbox" checked={pending} onChange={e=>{setOffset(0);setPending(e.target.checked);}}/>只看仍需处理的更新</label><button disabled={busy} onClick={()=>refresh().catch(e=>setError(e.message))}>刷新知识更新待办</button>
 {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 {candidate&&<PolicyExtractionPanel token={token} role={role} version={candidate} onDone={async()=>{await refresh();await onDone();}} onClose={()=>setCandidate(null)}/>}
 {sourcePreview&&<section aria-label="当前来源正文"><h4>当前来源正文</h4>{sourcePreview.blocks.map((b:any)=><blockquote key={b.index}>{b.text}</blockquote>)}{sourcePreview.next_offset!==null&&<button onClick={()=>api('/knowledge/versions/'+sourcePreview.version_id+'/parse?offset='+sourcePreview.next_offset+'&limit=20',token).then(v=>setSourcePreview({...v,blocks:[...sourcePreview.blocks,...v.blocks]})).catch(e=>setError(e.message))}>继续核对来源正文</button>}<button onClick={()=>setSourcePreview(null)}>关闭来源正文</button></section>}
 {editor&&<section aria-label="FAQ联动编辑"><h4>{task.title}</h4><p>{editor.warning}</p><p>任务状态：{statuses[task.status]}{version?' · 新FAQ v'+version.number+' · '+({queued:'待处理',processing:'索引处理中',ready:'已就绪',failed:'处理失败'} as Record<string,string>)[version.status]:''}</p>
 {review&&<p>审核状态：{({pending:"待独立审核",approved:"审核通过",rejected:"已驳回"} as Record<string,string>)[review.status]}{review.review_note?" · "+review.review_note:""}</p>}
 {editor.source_details.map((s:any)=><div key={s.id}><p>当前来源：{s.title} · v{s.number} · {Object.values(s.audience).join(' / ')||'未标范围'}</p><button onClick={()=>api('/knowledge/versions/'+s.id+'/parse',token).then(v=>{setSourcePreview(v);}).catch(e=>setError(e.message))}>核对当前来源片段</button></div>)}
 <details><summary>查看旧FAQ内容</summary><pre style={{whiteSpace:'pre-wrap'}}>{editor.original_content}</pre></details>
 <label>FAQ待复核正文<textarea rows={8} maxLength={80000} disabled={!editable||busy} value={content} onChange={e=>setContent(e.target.value)}/></label>
 {Object.entries({merchant:'FAQ适用商家',region:'FAQ适用地区',channel:'FAQ适用渠道',product:'FAQ适用商品'}).map(([key,label])=><label key={key}>{label}<input disabled={!editable||busy} maxLength={80} value={audience[key]||''} onChange={e=>setAudience({...audience,[key]:e.target.value})}/></label>)}
 <label>FAQ处理说明<textarea value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></label>
 <button disabled={busy} onClick={()=>openEditor(task).catch(e=>setError(e.message))}>刷新FAQ处理状态</button><button onClick={()=>setEditor(null)}>关闭FAQ编辑</button>
 {editable&&<button disabled={busy||reason.trim().length<5||!content.trim()} onClick={()=>act(async()=>{const result=await api('/knowledge/update-tasks/'+task.id+'/prepare-faq',token,{expected_revision:task.revision,reason,content,binding_hash:editor.binding_hash,audience:Object.fromEntries(Object.entries(audience).filter(([,v])=>v.trim()))});await openEditor(result.task);setNotice('新FAQ已生成，待索引与独立审核；尚未发布。');})}>生成FAQ复核新版本</button>}
 {role==='approver'&&version?.status==='ready'&&review?.status==='pending'&&<><button disabled={busy||reason.trim().length<5} onClick={()=>act(async()=>{await api('/knowledge/versions/'+version.id+'/faq-review',token,{action:'approve',reason,config_hash:version.config_hash});await openEditor(task);setNotice('FAQ独立审核通过，待管理员发布。');})}>批准联动FAQ</button><button disabled={busy||reason.trim().length<5} onClick={()=>act(async()=>{await api('/knowledge/versions/'+version.id+'/faq-review',token,{action:'reject',reason,config_hash:version.config_hash});await openEditor(task);setNotice('FAQ已驳回，管理员可修改后生成新版本。');})}>驳回联动FAQ</button></>}
 {role==='admin'&&version?.status==='ready'&&review?.status==='approved'&&<button disabled={busy} onClick={()=>act(async()=>{await api('/knowledge/versions/'+version.id+'/publish',token,{acknowledge_warnings:true});setEditor(null);setNotice('新FAQ已发布，关联待办已完成。');})}>发布联动FAQ</button>}
 </section>}
 {!items.length&&<p>当前没有符合筛选条件的知识更新。</p>}
 {items.map(update=><section key={update.id}><h4>{update.title} · {actions[update.action]||update.action}</h4><p>{update.created_at}</p>{!update.tasks.length&&<p>本次更新没有关联维护事项。</p>}
 {update.tasks.map((t:any)=><div className="version-row" key={t.id}><div><strong>{kinds[t.resource_kind]}：{t.title}</strong><p>{statuses[t.status]}</p>{t.note&&<p>{t.note}</p>}{!t.current_source_version_id&&<p>来源已撤回，请先处理有效政策。</p>}</div>
 {['pending','prepared'].includes(t.status)&&<div className="actions">{t.resource_kind==='faq'&&<button disabled={busy} onClick={()=>act(()=>openEditor(t))}>处理FAQ更新</button>}
 {t.resource_kind==='extraction'&&role==='admin'&&<button disabled={busy||!t.target_version_id} onClick={()=>act(async()=>{const result=await api('/knowledge/update-tasks/'+t.id+'/prepare-extraction',token,{expected_revision:t.revision,reason:'按当前有效来源重新准备审核整理'});setCandidate(await api('/knowledge/versions/'+result.candidate.version_id,token));})}>重新准备政策整理</button>}
 {t.resource_kind==='extraction'&&role==='approver'&&t.replacement_id&&<button disabled={busy} onClick={()=>act(async()=>{const value=await api('/knowledge/policy-extractions/'+t.replacement_id,token);setCandidate(await api('/knowledge/versions/'+value.version_id,token));})}>核对替代政策整理</button>}
 {t.resource_kind==='return_rule'&&<details><summary>人工重配规则后处理</summary><p>请先在退货申请中配置当前政策规则。这里仅关联独立核对结果，不迁移旧申请。</p><button onClick={()=>api('/returns/policies',token).then(v=>setRules(v.items)).catch(e=>setError(e.message))}>读取当前退货规则</button><label>替代退货规则<select value={rule} onChange={e=>setRule(e.target.value)}><option value="">请选择已核对的新规则</option>{rules.filter(r=>r.version_id===t.current_source_version_id).map(r=><option key={r.id} value={r.id}>{r.rules.skus.join(' / ')} · {r.rules.window_days}天 · {r.rules.return_basis==='quality_issue'?'质量问题':'无理由'}</option>)}</select></label><label>规则核对说明<textarea value={reason} onChange={e=>setReason(e.target.value)}/></label>{role==='approver'&&<button disabled={busy||!rule||reason.trim().length<5} onClick={()=>act(async()=>{await api('/knowledge/update-tasks/'+t.id+'/resolve-rule',token,{expected_revision:t.revision,replacement_id:rule,reason});})}>确认替代规则已核对</button>}</details>}
 {role==='admin'&&<details><summary>不再维护此项</summary><p>仅关闭待办，原FAQ仍保持失效，不会恢复检索。</p><label>不再维护的原因<textarea value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></label><button disabled={busy||reason.trim().length<5} onClick={()=>act(async()=>{await api('/knowledge/update-tasks/'+t.id+'/dismiss',token,{expected_revision:t.revision,reason});})}>记录不再维护</button></details>}
 </div>}
 </div>)}
 </section>)}
 <button disabled={busy||!offset} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页更新</button><button disabled={busy||!more} onClick={()=>setOffset(offset+20)}>下一页更新</button>
 </article>;
}
