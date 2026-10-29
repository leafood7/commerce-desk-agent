import { useState, type FormEvent } from "react";
import { api, watchRun } from "./api";
import { ConfigVersions } from "./ConfigVersions";
import { Conversations } from "./Conversations";
import { RuntimeProfile } from "./RuntimeProfile";
import { AfterSales, Approvals } from "./AfterSales";
import { KnowledgeManager, PolicySearch } from "./Knowledge";
import { Returns } from "./Returns";

type User = { id: string; organization_id: string; display_name: string; role: string };
type Order = { number: string; customer_name: string; payment_status: string;
  shipping_status: string; estimated_delivery: string | null; signed_at: string | null };
type Step = { id: number; type: string; payload: { answer?: string; order?: Order; status?: string } };
const labels: Record<string, string> = {
  "run.queued": "查询已排队", "run.started": "开始处理",
  "tool.retry": "查询短暂失败，正在重试", "tool.started": "正在查询订单", "tool.finished": "已取得订单信息",
  "answer.published": "结果已发布", "run.finished": "处理结束"
};

export default function App() {
  const [tab,setTab] = useState("orders");
  const [context,setContext]=useState<{conversation:string;run?:string}|null>(null);
  const [organization, setOrganization] = useState("demo-a");
  const [email, setEmail] = useState("support@demo.test");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [number, setNumber] = useState("A1001");
  const [answer, setAnswer] = useState("");
  const [order, setOrder] = useState<Order | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function signIn(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api("/auth/login", "", { organization, email, password });
      setToken(result.access_token); setUser(result.user); setPassword("");
    } catch (problem) { setError((problem as Error).message); }
    finally { setBusy(false); }
  }

  async function lookup(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setSteps([]); setAnswer(""); setOrder(null);
    try {
      const run = await api("/runs", token, { order_number: number },
        { "Idempotency-Key": crypto.randomUUID() });
      await watchRun(run.id, token, (step: Step) => {
        setSteps(previous => previous.some(row => row.id === step.id) ? previous : [...previous, step]);
        if (step.type === "answer.published") {
          setAnswer(step.payload.answer || ""); setOrder(step.payload.order || null);
        }
      });
      const final = await api("/runs/" + run.id, token);
      if (final.output) { setAnswer(final.output.answer); setOrder(final.output.order); }
      if (final.status !== "completed") setError("查询状态：" + final.status);
    } catch (problem) { setError((problem as Error).message); }
    finally { setBusy(false); }
  }

  async function signOut() {
    try { await api("/auth/logout", token, {}); }
    finally { setContext(null);setToken(""); setUser(null); setTab("orders"); setAnswer(""); setOrder(null); setSteps([]); }
  }

  return <main>
    <header><div className="brand">C</div><div><h1>CommerceDesk <span>商询</span></h1>
      <p>电商客服工作台</p></div>
      {user && <button className="quiet" onClick={signOut}>退出登录</button>}
    </header>
    <div className="notice">订单查询与政策检索已接通。政策依据来自当前商家已发布的文档，请结合适用范围与条件核对。</div>
    {user && <nav className="tabs" aria-label="工作台功能">
      <button className={tab==="orders"?"active":""} onClick={()=>setTab("orders")}>订单查询</button>
      <button className={tab==="search"?"active":""} onClick={()=>setTab("search")}>政策检索</button>
      <button className={tab==="knowledge"?"active":""} onClick={()=>setTab("knowledge")}>知识库</button>
      <button className={tab==="aftersales"?"active":""} onClick={()=>setTab("aftersales")}>售后助手</button>
      <button className={tab==="returns"?"active":""} onClick={()=>{setContext(null);setTab("returns");}}>退货申请</button>
      {user.role==="approver"&&<button className={tab==="approvals"?"active":""} onClick={()=>setTab("approvals")}>加急审批</button>}
      <button className={tab==="runtime"?"active":""} onClick={()=>setTab("runtime")}>运行策略</button>
      <button className={tab==="history"?"active":""} onClick={()=>setTab("history")}>会话记录</button>
      <button className={tab==="config"?"active":""} onClick={()=>setTab("config")}>配置版本</button>
    </nav>}
    {!user ? <section className="login"><h2>登录工作台</h2><p>使用初始化时创建的演示账号。</p>
      <form onSubmit={signIn}>
        <label>商家编号<input value={organization} onChange={e => setOrganization(e.target.value)} required /></label>
        <label>邮箱<input type="email" value={email} onChange={e => setEmail(e.target.value)} required /></label>
        <label>密码<input type="password" value={password} onChange={e => setPassword(e.target.value)} required autoComplete="current-password" /></label>
        <button disabled={busy}>{busy ? "正在登录…" : "进入工作台"}</button>
      </form></section> :
      tab === "orders" ? <div className="workspace"><aside><p className="eyebrow">当前账号</p><h2>{user.display_name}</h2>
        <p>{user.role === "support" ? "客服" : user.role === "approver" ? "审批员" : "管理员"}</p>
        <hr/><h3>本次处理步骤</h3>
        {steps.length ? <ol>{steps.map(step => <li key={step.id}>{labels[step.type] || step.type}</li>)}</ol> :
          <p className="muted">查询后在这里查看实际执行过程。</p>}
      </aside><section className="conversation"><h2>订单助手</h2>
        <p className="muted">查询当前商家的订单，预计送达与实际签收分别展示。</p>
        <form className="query" onSubmit={lookup}><input aria-label="订单号" value={number}
          onChange={e => setNumber(e.target.value)} required maxLength={64} />
          <button disabled={busy}>{busy ? "查询中…" : "查询订单"}</button></form>
        {answer && <article><p>{answer}</p>{order && <dl>
          <dt>订单号</dt><dd>{order.number}</dd><dt>客户</dt><dd>{order.customer_name}</dd>
          <dt>付款状态</dt><dd>{order.payment_status}</dd><dt>物流状态</dt><dd>{order.shipping_status}</dd>
          <dt>预计信息</dt><dd>{order.estimated_delivery || "未提供"}</dd>
          <dt>实际签收</dt><dd>{order.signed_at || "未提供"}</dd>
        </dl>}<small>演示数据 · 查询结果来自业务数据库</small></article>}
      </section></div> : tab === "search" ? <PolicySearch token={token}/> :
      tab === "aftersales" ? <AfterSales token={token} conversationId={context?.conversation} initialRunId={context?.run} onResetContext={id=>setContext(id?{conversation:id}:null)}/> : tab === "approvals" ? <Approvals token={token}/> :
      tab === "history" ? <Conversations token={token} onContinue={id=>{setContext({conversation:id});setTab("aftersales");}}
        onResume={(id,conversation)=>{void api("/runs/"+id,token).then(row=>{setContext({conversation,run:id});setTab(row.input.return_policy_id?"returns":"aftersales");}).catch(e=>setError(e.message));}}/> :
      tab === "config" ? <ConfigVersions token={token} role={user.role}/> :
      tab === "returns" ? <Returns token={token} role={user.role} initialRunId={context?.run}/> :
      tab === "runtime" ? <RuntimeProfile token={token}/> : <KnowledgeManager token={token} role={user.role}/>}
    {error && <p className="error" role="alert">{error}</p>}
  </main>;
}
