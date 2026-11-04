import {KnowledgeImpactPanel} from "./KnowledgeImpact";
import {useEffect,useState} from 'react';
import {api} from './api';
const names:Record<string,string>={scope:'适用范围',window:'期限',condition:'条件',exception:'例外',responsibility:'费用与责任'};
const states:Record<string,string>={draft:'草稿',pending:'待独立审核',approved:'已审核待发布',rejected:'已驳回',published:'已发布'};
type Ref={chunk_id:string;start:number;end:number;quote:string};
type Card={id:string;category:string;statement:string;sources:Ref[]};
type Candidate={id:string;version_id:string;revision:number;status:string;stale:boolean;review_note:string|null;payload:{cards:Card[];original_cards?:Card[];unknown_categories:string[];review_flags:string[];conflict_cues?:{kind:string;values:string[];chunk_ids:string[];meaning:string}[];references:string[];method:string;source_origin:string};sources:{id:string;content:string;section:string;page:number|null}[]};
export function PolicyExtractionPanel({token,role,version,onDone,onClose}:{token:string;role:string;version:{id:string;number:number};onDone:()=>Promise<void>;onClose:()=>void}){
 const [showImpact,setShowImpact]=useState(false);
 const [row,setRow]=useState<Candidate|null>(null),[history,setHistory]=useState<any[]>([]),[cards,setCards]=useState<Card[]>([]),[note,setNote]=useState(''),[ack,setAck]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 function accept(value:Candidate){setRow(value);setCards(value.payload.cards);setAck(false);}
 async function load(id?:string){const list=await api('/knowledge/versions/'+version.id+'/policy-extractions',token);setHistory(list.items);const selected=id||list.items[0]?.id;if(selected)accept(await api('/knowledge/policy-extractions/'+selected,token));else{setRow(null);setCards([]);}}
 useEffect(()=>{load().catch(e=>setError(e.message));},[version.id,token]);
 async function act(action:string){setBusy(true);setError('');setNotice('');try{
  if(action==='extract'){const created=await api('/knowledge/versions/'+version.id+'/policy-extractions',token,{});await load(created.id);setNotice('已生成待审核候选，请对照原文修改。');}
  else if(row){const body=['save','submit'].includes(action)?{expected_revision:row.revision,cards,note}:{expected_revision:row.revision,note,acknowledge_warnings:ack,...(['approve','reject'].includes(action)?{action}:{})};
   const result=await api('/knowledge/policy-extractions/'+row.id+'/'+(['approve','reject'].includes(action)?'review':action),token,body);
   await load((result.candidate||result).id);setNotice(action==='publish'?'政策与审核条款已发布，新的检索使用该有效版本。':action==='submit'?'已提交独立审核。':action==='approve'?'审核通过，等待管理员发布。':action==='reject'?'已驳回，可重新提取新候选。':'修改已保存。');}
  await onDone();
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 function patch(index:number,change:Partial<Card>){setCards(cards.map((c,i)=>i===index?{...c,...change}:c));}
 const editable=role==='admin'&&row?.status==='draft'&&!row.stale;
 return <article className="document-card" aria-label="政策候选工作台"><div className="row"><h3>政策提取与审核 · v{version.number}</h3><button onClick={onClose}>关闭政策候选</button></div>
 {showImpact&&row&&<KnowledgeImpactPanel token={token} version={version} candidateId={row.id} onClose={()=>setShowImpact(false)} onConfirm={async()=>{await act("publish");}}/>}
 <p>自动整理是规则辅助摘录，不代表已理解全部政策。请逐项核对原文、范围和例外。发布的是审核条款，不会自动创建可执行退货规则，也不执行退款。</p>
 {role==='admin'&&<button disabled={busy} onClick={()=>act('extract')}>自动提取政策候选</button>}
 {!!history.length&&<label>历史候选<select value={row?.id||''} onChange={e=>load(e.target.value).catch(e=>setError(e.message))}>{history.map(h=><option key={h.id} value={h.id}>第{h.number}次 · {states[h.status]}{h.stale?' · 来源已变化':''}</option>)}</select></label>}
 {row&&<><p role="status">候选状态：{states[row.status]} · 修订{row.revision}</p>{row.stale&&<p role="alert">来源或发布版本已变化，请重新提取和审核，旧候选不能继续发布。</p>}
 <p>未识别的项目：{row.payload.unknown_categories.map(k=>names[k]).join('、')||'无；仍须人工核对遗漏'}。未识别不等于政策没有规定。</p>
 {!!row.payload.review_flags.length&&<p>待核对：{row.payload.review_flags.map(f=>f==='multiple_windows_require_scope_review'?'存在不同期限，请核对各自范围':'未找到明确例外，请检查全文').join('；')}</p>}
 {!!(row.payload.conflict_cues||[]).length&&<p role="status">冲突提示：{(row.payload.conflict_cues||[]).map(c=>c.kind==='different_windows'?`出现不同期限 ${c.values.join('、')}（涉及来源片段 ${c.chunk_ids.join('、')}）——${c.meaning}`:c.meaning).join('；')}</p>}
 {!!row.payload.references.length&&<p>原文明确提及：{row.payload.references.join('、')}，请核对引用条款是否齐全。</p>}
 {row.review_note&&<p>审核意见：{row.review_note}</p>}
 {cards.map((card,index)=><fieldset disabled={busy||!editable} key={card.id}><legend>条款{index+1}</legend><label>条款类型<select value={card.category} onChange={e=>patch(index,{category:e.target.value})}>{Object.entries(names).map(([k,n])=><option key={k} value={k}>{n}</option>)}</select></label>{row.payload.original_cards?.find(c=>c.id===card.id)?.statement!==card.statement&&<p className="muted">原自动候选：{row.payload.original_cards?.find(c=>c.id===card.id)?.statement||'人工补入条款'}</p>}<label>整理后的说明<textarea value={card.statement} maxLength={2000} onChange={e=>patch(index,{statement:e.target.value})}/></label>
 {card.sources.map((ref,i)=>{const source=row.sources.find(s=>s.id===ref.chunk_id);return <div key={i}><p>{source?.section||'原文片段'}{source?.page?' · 第'+source.page+'页':''} · 字符{ref.start}—{ref.end} · {row.payload.source_origin==='human_corrected_parse'?'人工修订解析':'原文解析'}</p><blockquote>{ref.quote}</blockquote><details><summary>查看完整来源片段</summary><p>{source?.content}</p></details></div>;})}
 {editable&&<button onClick={()=>setCards(cards.filter((_,i)=>i!==index))}>移除此候选</button>}</fieldset>)}
 {editable&&<details><summary>补充遗漏条款</summary><p>从来源片段补入条款，再修改分类和说明。引用保留完整片段，不能伪造原文。</p>{row.sources.map(source=><button key={source.id} disabled={busy||cards.length>=100} onClick={()=>{let n=1;while(cards.some(c=>c.id==='c'+n))n++;setCards([...cards,{id:'c'+n,category:'condition',statement:source.content,sources:[{chunk_id:source.id,start:0,end:source.content.length,quote:source.content}]}]);}}>补入：{source.section||source.content.slice(0,30)}</button>)}</details>}
 <details><summary>核对全文片段（含未自动提取的内容）</summary>{row.sources.map(source=><div key={source.id}><h4>{source.section||'来源片段'}</h4><blockquote>{source.content}</blockquote></div>)}</details>
 <label>修改、审核或发布说明<textarea maxLength={500} value={note} onChange={e=>setNote(e.target.value)} placeholder="说明核对了哪些内容或需要修改什么"/></label>
 <label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>已对照原文核对适用范围、条件、例外、未知项及解析警告</label>
 {editable&&<><button disabled={busy||!cards.length||note.trim().length<5} onClick={()=>act('save')}>保存候选修改</button><button disabled={busy||!cards.length||note.trim().length<5} onClick={()=>act('submit')}>提交政策独立审核</button></>}
 {role==='approver'&&row.status==='pending'&&!row.stale&&<><button disabled={busy||!ack||note.trim().length<5} onClick={()=>act('approve')}>批准政策候选</button><button disabled={busy||note.trim().length<5} onClick={()=>act('reject')}>驳回政策候选</button></>}
 {role==='admin'&&row.status==='approved'&&!row.stale&&<button disabled={busy||!ack||note.trim().length<5} onClick={()=>setShowImpact(true)}>发布已审核政策</button>}
 </>}
 {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </article>;
}
