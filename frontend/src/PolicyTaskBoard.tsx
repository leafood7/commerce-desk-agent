type Task={id:string;label:string;status:string;answer_status?:string;evidence_ids:string[]};
export type TaskBoard={tasks?:Task[];review_cues?:string[];scope_conflicts?:{dimension:string;values:string[]}[];stop_reason?:string;next_step?:string};
export function PolicyTaskBoard({board}:{board:TaskBoard|null|undefined}){
 if(!board?.tasks?.length)return null;
 const statuses:Record<string,string>={candidate_found:'已找到待核对资料',needs_read:'还有对应条款待补读',not_found:'尚未找到对应资料'};
 return <article className="document-card" aria-label="政策协作事项"><h3>本题政策事项</h3><p className="muted">这里显示资料交接状态；找到片段不等于结论已确认。</p>
 {board.tasks.map(task=><div key={task.id}><strong>{task.label}</strong><p>{statuses[task.status]||'待核对'} · {task.evidence_ids.length}份资料</p>{task.answer_status==='answered'&&<p>对应答复已核验发布</p>}{task.answer_status==='not_confirmed'&&<p>该项尚不能确认，请查看回答中的边界说明。</p>}</div>)}
 {!!board.review_cues?.length&&<p>发现不同期限，需要核对各自适用分支，不能直接认定政策冲突。</p>}
 {!!board.scope_conflicts?.length&&<p>资料存在适用范围差异，尚不能合并为一份规则，请明确商家、地区或渠道。</p>}
 {board.next_step==='targeted_read'&&<p>正在交回取证助手补查或补读对应原文。</p>}
 {board.next_step!=="targeted_read"&&board.stop_reason&&({retrieval_pass_limit:"已到本次补查轮数上限，保留尚未确认的事项。",source_budget_exhausted:"本次补读与补检额度已用尽，不能继续自动查询。",no_more_authorized_source:"当前没有更多获准来源可自动补查。"} as Record<string,string>)[board.stop_reason]&&<p>{({retrieval_pass_limit:"已到本次补查轮数上限，保留尚未确认的事项。",source_budget_exhausted:"本次补读与补检额度已用尽，不能继续自动查询。",no_more_authorized_source:"当前没有更多获准来源可自动补查。"} as Record<string,string>)[board.stop_reason]}</p>}
 </article>;
}
