import {useEffect, useState} from "react";
import {api} from "./api";

export function RuntimeProfile({token}:{token:string}) {
  const [profile,setProfile]=useState<any>(null);
  const [error,setError]=useState("");
  const [queue,setQueue]=useState<any>(null);
  useEffect(()=>{
    let active=true;
    api("/runtime/profile",token).then(value=>{if(active)setProfile(value);})
      .catch(problem=>{if(active)setError(problem.message);});
    return ()=>{active=false;};
  },[token]);
  useEffect(()=>{
    let active=true;
    const load=()=>api("/runtime/queue",token).then(value=>{if(active)setQueue(value);})
      .catch(problem=>{if(active)setError(problem.message);});
    load();
    const timer=setInterval(load,5000);
    return ()=>{active=false;clearInterval(timer);};
  },[token]);
  const reasons:Record<string,string>={worker_lease_expired:"执行进程失联，请恢复核实",job_authorization_expired:"登录授权失效",
    unavailable:"工具暂不可用",timeout:"工具超时",active_time_limit:"达到活跃执行时限",tool_call_limit:"达到工具调用上限"};
  return <section className="knowledge"><h2>运行策略</h2>
    <p>这里展示当前服务实际采用的策略。等待确认与审批不会消耗活跃执行时限。</p>
    {profile&&<article className="document-card"><dl>
      <dt>任务调度</dt><dd>{profile.dispatch==="worker"?"持久队列与独立执行进程":"兼容模式：进程内后台执行"}</dd>
      <dt>任务识别</dt><dd>{profile.router.mode==="rules"?"规则识别":"模型识别，失败回退规则"}</dd>
      <dt>模型调用</dt><dd>{profile.router.mode==="rules"?"默认不调用模型":
        profile.router.model+" · 温度0 · 最多256输出token · 不自动重试"}</dd>
      <dt>活跃执行上限</dt><dd>{profile.execution.active_seconds}秒 / 每次继续处理</dd>
      <dt>队列准入上限</dt><dd>每账号{profile.admission.owner_active_runs}个、全局{profile.admission.global_active_runs}个待执行流程；等待人工确认或审批不占位</dd>
      <dt>流程累计上限</dt><dd>{profile.cumulative.tool_attempts}次工具尝试；模型最多{profile.cumulative.model_attempts}次预留尝试、{profile.cumulative.model_tokens}个token额度</dd>
      <dt>工具调用上限</dt><dd>{profile.execution.tool_attempts}次，包含只读重试</dd>
      <dt>只读失败重试</dt><dd>最多额外{profile.execution.read_retries}次；权限与格式错误不重试</dd>
      <dt>写操作</dt><dd>不自动重试；仍需确认、业务校验及必要审批</dd>
      <dt>检索上下文</dt><dd>最多{profile.retrieval.context_chars}字符 · {profile.retrieval.context_mode==="neighbors"?"相邻片段":"章节扩展（实验）"}</dd>
    </dl><p className="muted">本页不修改配置。模型、提示词和业务安全规则由服务端固定；当前不提供自动退款或外部仓库通知。</p></article>}
    {queue&&<article className="document-card"><h3>我的执行队列</h3>
      <p>排队 {queue.counts.queued} 项 · 执行中 {queue.counts.running} 项</p>
      {queue.oldest_queued_seconds!==null&&<p>最早排队作业已等待 {queue.oldest_queued_seconds} 秒。</p>}
      <p className="muted">仅统计当前账号的执行作业，每5秒刷新；一次流程的多次继续处理分别记为作业。</p>
      {!!queue.recent_failures.length&&<><h4>最近暂停或失败的作业</h4><ul>{queue.recent_failures.map((item:any,index:number)=>
        <li key={item.run_id+"-"+index}>流程 {item.run_id.slice(0,8)} · {reasons[item.error_code]||"执行未完成，请打开流程核实"}</li>)}</ul>
        <p className="muted">这是历史作业记录，恢复后原失败记录仍保留；请以流程当前状态为准。</p></>}
    </article>}
    {error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
