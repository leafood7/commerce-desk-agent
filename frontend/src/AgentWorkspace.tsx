import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Activity, Bot, Check, CircleAlert, ClipboardCheck, FileSearch,
  LoaderCircle, MessageSquare, Plus, RotateCcw, Send, ShieldCheck, Sparkles,
  Truck, UserRound,
} from "lucide-react";
import { api, watchRun } from "./api";
import ContextCard from "./ContextCard";

type Run = {
  id: string;
  conversation_id: string;
  kind: string;
  status: string;
  input: any;
  output: any;
  error_code?: string;
  created_at?: string;
};
type Message = { id: string; role: string; content: string; run_id?: string | null; created_at?: string };
type Event = { id: number; type: string; timestamp?: string; payload: any };
type ReturnSetup = {
  query: string;
  orderNumber: string;
  order: any | null;
  policies: any[];
  itemId: string;
  policyId: string;
  quantity: number;
  condition: "unknown" | "unused" | "used";
  basis: "no_reason" | "quality_issue";
  reason: string;
  application: boolean;
  error: string;
  loading: boolean;
};

const statusLabels: Record<string, string> = {
  queued: "等待开始", running: "正在处理", awaiting_input: "等待你补充",
  awaiting_confirmation: "等待你确认", awaiting_approval: "等待审批员",
  interrupted: "处理已暂停", completed: "本轮完成", cancelled: "已取消", failed: "处理失败",
};
const eventLabels: Record<string, string> = {
  "run.queued": "任务已进入处理队列", "run.started": "Agent 开始处理",
  "run.resumed": "继续处理当前任务", "run.paused": "任务暂停，等待下一步",
  "run.finished": "本轮处理结束", "answer.published": "处理结果已生成",
  "tool.retry": "只读工具短暂失败，正在重试", "tool.failed": "工具调用未完成",
  "agent.started": "协作步骤开始", "agent.completed": "协作步骤完成",
  "agent.failed": "协作步骤未完成", "agent.decision": "Agent 已选择下一步",
};
const toolLabels: Record<string, string> = {
  get_order_status: "查询订单", search_knowledge_base: "检索政策",
  read_policy_source: "阅读政策原文", request_shipping: "登记售后申请",
  get_shipping_status: "查询申请状态", get_return_policy: "读取退货规则",
  evaluate_return: "核对退货条件", create_return_request: "创建退货申请",
};
const graphLabels: Record<string, string> = {
  route: "识别任务", read_plan: "校验可执行步骤", gather: "整理缺少的信息",
  clarify_policy_scope: "确认政策适用范围",
  observe: "核对订单记录", retrieve: "检索政策依据", propose: "准备申请预览",
  confirmation: "等待确认", recheck: "重新核对业务条件", submit: "提交申请",
  wait_approval: "等待独立审批", refresh: "核实审批状态", result: "整理处理结果",
  lookup_result: "整理订单信息", knowledge_result: "整理政策依据", unsupported: "核对能力边界",
  task_planner: "整理任务", retrieval_agent: "检索依据", evidence_coordinator: "核对证据",
  answer_agent: "组织答复", review_agent: "检查答复", repair_agent: "修订答复",
};
const taskLabels: Record<string, string> = { policy: "只查退货规则", facts: "只查业务记录", eligibility: "核对能否退货", application: "申请退货" };
const factLabels: Record<string, string> = {
  signed_at: "实际签收时间", refund_amount: "实际退款金额", bank_received: "银行到账记录",
  activation_status: "商品激活状态", member_entitlement: "订单会员权益",
  quality_verdict: "质检结论", coupon_reissue_amount: "优惠券补发金额",
};
const conditionLabels: Record<string, string> = { unknown: "尚未核实", unused: "未使用", used: "已使用" };

function orderNumberIn(text: string) {
  const found = text.match(/(?<![A-Za-z0-9_-])([A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)(?![A-Za-z0-9_-])/g) || [];
  return [...new Set(found.map(value => value.toUpperCase()))].length === 1
    ? [...new Set(found.map(value => value.toUpperCase()))][0]
    : "";
}
function cleanMessage(content: string) {
  return content.replace(/^(?:售后协助|政策咨询|查询订单)\s*/, "");
}
function timeLabel(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}
function toolEventTitle(event: Event) {
  if (event.type === "graph.step") return graphLabels[event.payload?.node] || "流程步骤完成";
  if (event.type === "tool.started") return `正在${toolLabels[event.payload?.name] || "调用工具"}`;
  if (event.type === "tool.finished") return `${toolLabels[event.payload?.name] || "工具调用"}已返回`;
  if (event.type === "agent.started" || event.type === "agent.completed" || event.type === "agent.failed") {
    const agent = graphLabels[event.payload?.agent] || "Agent 协作步骤";
    return event.type === "agent.started" ? `${agent}开始` : event.type === "agent.failed" ? `${agent}未完成` : `${agent}完成`;
  }
  return eventLabels[event.type] || event.type;
}

export function AgentWorkspace({
  token,
  conversationId: incomingConversationId,
  initialRunId,
  onResetContext,
}: {
  token: string;
  conversationId?: string;
  initialRunId?: string;
  onResetContext?: (id: string | null) => void;
}) {
  const [conversationId, setConversationId] = useState<string | null>(incomingConversationId || null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [currentRun, setCurrentRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [conversations, setConversations] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [setup, setSetup] = useState<ReturnSetup | null>(null);
  const [setupMessage, setSetupMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [returnTaskEnabled, setReturnTaskEnabled] = useState(false);
  const eventController = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const currentRunRef = useRef<Run | null>(null);
  currentRunRef.current = currentRun;

  const currentStatus = currentRun?.status || "";
  const currentChoices = currentRun?.output?.choices || [];
  const waitingForInput = currentStatus === "awaiting_input";
  const waitingForConfirmation = currentStatus === "awaiting_confirmation";
  const displayRuns = useMemo(() => Object.values(runs).sort((a, b) =>
    String(b.created_at || "").localeCompare(String(a.created_at || ""))), [runs]);

  async function refreshTranscript(id: string) {
    const [messageData, runData] = await Promise.all([
      api(`/conversations/${id}/messages?limit=200`, token),
      api(`/conversations/${id}/runs?limit=100`, token),
    ]);
    const nextRuns: Record<string, Run> = {};
    for (const row of runData.items as Run[]) nextRuns[row.id] = row;
    setMessages(messageData.items || []);
    setRuns(nextRuns);
    return nextRuns;
  }

  async function followRun(id: string, conversation: string) {
    eventController.current?.abort();
    const controller = new AbortController();
    eventController.current = controller;
    setEvents([]);
    setBusy(true);
    try {
      await watchRun(id, token, (event: Event) => {
        setEvents(previous => previous.some(row => row.id === event.id) ? previous : [...previous, event]);
        if (event.type === "answer.published") {
          setCurrentRun(previous => previous?.id === id ? { ...previous, output: event.payload } : previous);
        }
        if (event.type === "run.paused") {
          setCurrentRun(previous => previous?.id === id ? { ...previous, status: event.payload?.status || previous.status } : previous);
        }
      }, controller.signal);
      const final = await api(`/runs/${id}`, token) as Run;
      setCurrentRun(final);
      setRuns(previous => ({ ...previous, [id]: final }));
      await refreshTranscript(conversation);
    } catch (problem) {
      if (!controller.signal.aborted) setError((problem as Error).message);
    } finally {
      if (eventController.current === controller) setBusy(false);
    }
  }

  async function openConversation(id: string, preferredRunId?: string) {
    setError(""); setNotice(""); setSetup(null); setSetupMessage("");
    setConversationId(id); onResetContext?.(id);
    const nextRuns = await refreshTranscript(id);
    const run = (preferredRunId && nextRuns[preferredRunId])
      || Object.values(nextRuns).sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")))[0]
      || null;
    setCurrentRun(run);
    if (run && ["queued", "running"].includes(run.status)) void followRun(run.id, id);
    else if (run) {
      eventController.current?.abort();
      void followRun(run.id, id);
    } else { setEvents([]); }
  }

  useEffect(() => {
    let active = true;
    eventController.current?.abort();
    async function initialize() {
      try {
        if (initialRunId) {
          const row = await api(`/runs/${initialRunId}`, token) as Run;
          if (active) await openConversation(row.conversation_id, row.id);
        } else if (incomingConversationId) {
          if (active) await openConversation(incomingConversationId);
        } else {
          setConversationId(null); setMessages([]); setRuns({}); setCurrentRun(null); setEvents([]);
        }
        const recent = await api("/conversations?limit=20", token);
        if (active) setConversations(recent.items || []);
      } catch (problem) { if (active) setError((problem as Error).message); }
    }
    void initialize();
    return () => { active = false; eventController.current?.abort(); };
  }, [token, incomingConversationId, initialRunId]);

  useEffect(() => {
    let active = true;
    api("/runtime/config", token)
      .then(value => { if (active) setReturnTaskEnabled(!!value.current?.values?.return_task_clarification); })
      .catch(() => { if (active) setReturnTaskEnabled(false); });
    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, events, currentRun?.output?.answer, setup]);

  async function ensureConversation(title: string) {
    if (conversationId) return conversationId;
    const row = await api("/conversations", token, { title: title.slice(0, 80) || "售后协作" });
    setConversationId(row.id); onResetContext?.(row.id);
    setConversations(previous => [{ id: row.id, title: row.title, updated_at: new Date().toISOString() }, ...previous]);
    return row.id as string;
  }

  async function createRun(query: string, extra: Record<string, unknown> = {}) {
    const conversation = await ensureConversation(query);
    const run = await api("/runs", token, {
      kind: "aftersales", query, conversation_id: conversation, ...extra,
    }, { "Idempotency-Key": crypto.randomUUID() }) as Run;
    setCurrentRun(run); setRuns(previous => ({ ...previous, [run.id]: run }));
    setSetup(null); setSetupMessage("");
    await refreshTranscript(conversation);
    await followRun(run.id, conversation);
  }

  async function beginReturnSetup(query: string) {
    const conversation = await ensureConversation(query);
    const number = orderNumberIn(query);
    setSetup({
      query, orderNumber: number, order: null, policies: [], itemId: "", policyId: "",
      quantity: 1, condition: "unknown", basis: /质量|缺陷|坏了|故障/.test(query) ? "quality_issue" : "no_reason",
      reason: "", application: /申请|办理退货|帮我退|退回商品/.test(query) && !/先不提交|不要提交|只想知道|只查|先看看/.test(query),
      error: "", loading: false,
    });
    setConversationId(conversation);
    setSetupMessage("准备退货核对：需要先关联订单商品和适用规则。Agent 核验完成后会在此对话中返回结果。");
    if (number) await loadReturnContext(number);
  }

  async function loadReturnContext(number: string) {
    setSetup(previous => previous ? { ...previous, orderNumber: number, loading: true, error: "" } : previous);
    try {
      const [order, policies] = await Promise.all([
        api("/orders/" + encodeURIComponent(number), token),
        api("/returns/policies", token),
      ]);
      setSetup(previous => previous ? {
        ...previous, orderNumber: number, order, policies: policies.items || [],
        itemId: order.items?.[0]?.id || "", policyId: "", loading: false, error: "",
      } : previous);
    } catch (problem) {
      setSetup(previous => previous ? { ...previous, loading: false, error: (problem as Error).message } : previous);
    }
  }

  const selectedSetupItem = setup?.order?.items?.find((item: any) => item.id === setup.itemId);
  const applicablePolicies = setup && selectedSetupItem
    ? setup.policies.filter((policy: any) => policy.rules?.skus?.includes(selectedSetupItem.sku)) : [];

  async function startReturnRun() {
    if (!setup || !setup.order || !setup.itemId || !setup.policyId) return;
    const query = setup.query;
    const extra: Record<string, unknown> = {
      order_number: setup.orderNumber,
      return_item_id: setup.itemId,
      return_quantity: setup.quantity,
      return_condition: setup.condition,
      return_basis: setup.basis,
      return_policy_id: setup.policyId,
      ...(returnTaskEnabled ? { return_task: setup.application ? "application" : "eligibility" } : {}),
      ...(setup.reason.trim() ? { reason: setup.reason.trim() } : {}),
    };
    setSetupMessage("已关联订单和所选规则，正在启动退货 Agent 核验。");
    await createRun(query, extra);
  }

  function routeReturnQuery(query: string) {
    const policyOnly = /政策|规则|适用范围|哪些商品|条款/.test(query)
      && !/申请|办理|订单\s*[A-Za-z0-9_-]*\d|能不能退|是否能退|符合资格/.test(query);
    return /退货|退换货/.test(query) && !/发货|催单/.test(query) && !policyOnly;
  }

  async function resumeRun(action: string, extra: Record<string, unknown> = {}, message?: string) {
    const run = currentRunRef.current;
    if (!run) return;
    const body = {
      action,
      pause_id: run.output?.pause_id || null,
      ...extra,
      ...(message ? { message } : {}),
    };
    setBusy(true); setError("");
    try {
      await api(`/runs/${run.id}/resume`, token, body, { "Idempotency-Key": crypto.randomUUID() });
      setCurrentRun(previous => previous?.id === run.id ? { ...previous, status: "queued" } : previous);
      await followRun(run.id, run.conversation_id);
    } catch (problem) { setError((problem as Error).message); setBusy(false); }
  }

  function choiceFor(run: Run, answer: string): { action: string; extra: Record<string, unknown> } | null {
    const pause = run.output?.pause_kind;
    const value = answer.trim().toLowerCase();
    if (pause === "policy_scope") {
      const profiles = run.output?.scope_options?.profiles || [];
      const matches = profiles.filter((profile: Record<string, string>) =>
        Object.values(profile).every(scope => value.includes(String(scope).toLowerCase()))
      );
      return matches.length === 1 ? { action: "supply", extra: { audience: matches[0] } } : null;
    }
    if (pause === "task_clarification") {
      const task = value.includes("只查") || value.includes("政策") || value.includes("规则") ? "policy"
        : value.includes("记录") || value.includes("退款金额") || value.includes("签收") ? "facts"
        : value.includes("申请") || value.includes("办理") ? "application"
        : value.includes("资格") || value.includes("能不能") ? "eligibility" : null;
      return task ? { action: "choose_task", extra: { return_task: task } } : null;
    }
    if (pause === "fact_clarification") {
      const key = Object.keys(factLabels).find(name => value.includes(name) || value.includes(factLabels[name]));
      return key ? { action: "choose_fact", extra: { return_fact: key } } : null;
    }
    const fields: Record<string, any> = {};
    const missing: string[] = run.output?.missing || [];
    const number = orderNumberIn(answer);
    if (missing.includes("order_number") && number) fields.order_number = number;
    if (missing.includes("shipping_kind")) {
      if (/加急/.test(answer)) fields.shipping_kind = "expedite";
      else if (/普通催单|普通催发货|常规催/.test(answer)) fields.shipping_kind = "normal";
    }
    if (missing.includes("reason")) fields.reason = answer.trim();
    if (missing.includes("return_quantity")) {
      const quantity = answer.match(/(?:数量|退|寄回)\D{0,8}(\d{1,4})\s*(?:件|个)?/);
      if (quantity) fields.return_quantity = Number(quantity[1]);
    }
    if (missing.includes("return_condition")) {
      if (/未使用|没有使用|没用过/.test(answer)) fields.return_condition = "unused";
      else if (/已使用|使用过|用过/.test(answer)) fields.return_condition = "used";
    }
    if (missing.includes("return_basis")) {
      if (/质量|缺陷|故障/.test(answer)) fields.return_basis = "quality_issue";
      else if (/无理由/.test(answer)) fields.return_basis = "no_reason";
    }
    if (missing.includes("return_description") || pause === "condition_confirmation") {
      fields.return_description = answer.trim();
    }
    if (!Object.keys(fields).length && run.input?.return_policy_id) {
      if (/未使用|没有使用|没用过/.test(answer)) fields.return_condition = "unused";
      else if (/已使用|使用过|用过/.test(answer)) fields.return_condition = "used";
      if (/质量|缺陷|故障/.test(answer)) fields.return_basis = "quality_issue";
      if (/无理由/.test(answer)) fields.return_basis = "no_reason";
      const numberMatch = orderNumberIn(answer);
      if (numberMatch) fields.order_number = numberMatch;
    }
    return Object.keys(fields).length ? { action: "supply", extra: fields } : null;
  }

  function explicitConfirmation(value: string) {
    return /^(我确认|确认|确认申请|确认提交|确认按此申请|按此提交|提交本次申请)[。！!\s]*$/.test(value.trim());
  }
  function explicitCancel(value: string) {
    return /^(取消|取消本次申请|不提交|先不提交|算了|停止本次申请)[。！!\s]*$/.test(value.trim());
  }

  const runTaskPurpose = currentRun?.output?.task_frame?.purpose
    || currentRun?.input?.return_task
    || (/申请|办理退货|帮我退|退回商品/.test(String(currentRun?.input?.query || "")) ? "application" : "eligibility");
  const isReturnRun = !!(currentRun?.input?.return_policy_id || currentRun?.input?.return_item_id);
  const readOnlyReturnRun = isReturnRun && (
    runTaskPurpose !== "application"
    || /只查|只想知道|只核对|先不提交|不要提交|先别提交|先看看|只想看/.test(String(currentRun?.input?.query || ""))
  );

  async function sendMessage(event?: FormEvent, forcedText?: string) {
    event?.preventDefault();
    const message = (forcedText ?? text).trim();
    if (!message || busy) return;
    setText(""); setError(""); setNotice("");
    const run = currentRunRef.current;
    if (run && ["queued", "running"].includes(run.status)) {
      setText(message); setError("当前任务还在处理，等它暂停或完成后再发送下一条。"); return;
    }
    if (run?.status === "awaiting_confirmation") {
      if (explicitConfirmation(message)) {
        if (readOnlyReturnRun) {
          setText(message);
          setError("这轮任务只核对政策或资格，不会提交申请。若要办理，请重新明确提出申请；也可以结束本次核对。");
          return;
        }
        return void resumeRun("confirm", {}, message);
      }
      if (explicitCancel(message)) return void resumeRun("cancel", {}, message);
      const selected = choiceFor(run, message);
      if (selected) return void resumeRun("correct", selected.extra, message);
      setText(message); setError("当前在等待申请确认。请使用右侧“确认申请”或“取消”；若要修改，请明确说明数量、商品状态或申请原因。"); return;
    }
    if (run?.status === "awaiting_input") {
      const selected = choiceFor(run, message);
      if (!selected) {
        setText(message);
        setError(run.output?.pause_kind === "policy_scope"
          ? "我还不能确定适用范围。请从对话中的政策范围选项中选择，或按“商家、地区、渠道”写出一组完整范围。"
          : "我还不能从这句话中确定要补充的业务字段。请按右侧提示说明订单号、申请类型、原因或商品状态；不会猜测补全。");
        return;
      }
      return void resumeRun(selected.action, selected.extra, message);
    }
    if (routeReturnQuery(message)) {
      setSetupMessage("");
      await beginReturnSetup(message);
      return;
    }
    try {
      await createRun(message);
    } catch (problem) { setError((problem as Error).message); setBusy(false); }
  }

  async function selectRun(id: string) {
    const row = runs[id];
    if (!row) return;
    setCurrentRun(row); setError("");
    void followRun(row.id, row.conversation_id);
  }

  function newConversation() {
    eventController.current?.abort();
    setConversationId(null); setMessages([]); setRuns({}); setCurrentRun(null); setEvents([]);
    setSetup(null); setSetupMessage(""); setText(""); setError(""); setNotice("");
    onResetContext?.(null);
  }

  const currentMessage = currentRun?.output?.answer || "";
  const currentEvidence = currentRun?.output?.evidence || currentRun?.output?.policy_context || [];
  const currentOrder = currentRun?.output?.order;
  const proposal = currentRun?.output?.proposal;
  const currentRequest = currentRun?.output?.request;
  const isWorking = currentStatus === "queued" || currentStatus === "running";

  return (
    <div className="agent-workspace-page">
      <div className="agent-workspace-heading">
        <div className="agent-workspace-title-icon"><Sparkles size={24} /></div>
        <div><span>COMMERCE DESK · AGENT WORKSPACE</span><h2>售后协作工作台</h2><p>直接描述需求。Agent 会按实际工具结果查询、核对、追问或等待确认。</p></div>
        <div className="agent-workspace-actions">
          <label className="conversation-picker"><MessageSquare size={16} /><select aria-label="打开最近对话" value={conversationId || ""} onChange={e => { if (e.target.value) void openConversation(e.target.value); }}>
            <option value="">当前对话</option>{conversations.map(item => <option key={item.id} value={item.id}>{item.title || "售后对话"}</option>)}
          </select></label>
          <button className="quiet" onClick={newConversation}><Plus size={16} />新对话</button>
        </div>
      </div>

      <div className="agent-workspace-grid">
        <section className="agent-conversation-panel" aria-label="Agent 对话">
          <div className="agent-conversation-topline"><div><strong>售后 Agent</strong><span><i className={isWorking ? "is-working" : ""} />{isWorking ? "正在处理" : "等待你的消息"}</span></div>
            {displayRuns.length > 0 && <label className="run-picker">恢复任务<select aria-label="恢复历史 Agent 任务" value={currentRun?.id || ""} onChange={e => void selectRun(e.target.value)}><option value="">选择一次处理记录</option>{displayRuns.map(row => <option key={row.id} value={row.id}>{timeLabel(row.created_at)} · {statusLabels[row.status] || row.status} · {cleanMessage(row.input?.query || "售后任务").slice(0, 26)}</option>)}</select></label>}
          </div>

          <div className="agent-message-list" role="log" aria-live="polite" ref={listRef}>
            {!messages.length && !setup && <div className="agent-welcome"><div className="agent-welcome-icon"><Bot size={30} /></div><h3>你好，我可以帮你核对售后问题</h3><p>用自然语言描述订单、政策或办理需求。每一步都会展示实际查询结果；申请和审批仍需明确确认与授权。</p><div className="agent-prompt-chips">
              <button onClick={() => setText("帮我查一下订单 A1003 的物流状态")}>查订单物流</button>
              <button onClick={() => setText("请查一下退货政策，哪些商品适用？")}>咨询退货规则</button>
              <button onClick={() => setText("我想申请退货，订单号是 A1003")}>发起退货核对</button>
            </div></div>}
            {messages.map(message => {
              const row = message.run_id ? runs[message.run_id] : undefined;
              return <div className={`agent-message ${message.role === "human" ? "from-user" : "from-agent"}`} key={message.id}>
                <div className="agent-message-avatar">{message.role === "human" ? <UserRound size={17} /> : <Bot size={18} />}</div>
                <div className="agent-message-body"><div className="agent-message-meta"><strong>{message.role === "human" ? "你" : "售后 Agent"}</strong><time>{timeLabel(message.created_at)}</time></div>
                  <div className="agent-message-text">{cleanMessage(message.content)}</div>
                  {message.role !== "human" && row && <ConversationResult run={row} />}
                </div>
              </div>;
            })}
            {waitingForInput && currentRun?.output?.pause_kind === "policy_scope" && <PolicyScopeClarification
              run={currentRun}
              busy={busy}
              onSubmit={audience => void resumeRun(
                "supply",
                { audience },
                `我选择适用范围：${Object.values(audience).join(" / ")}`,
              )}
            />}
            {setup && <div className="agent-message from-user" key="return-setup-query">
              <div className="agent-message-avatar"><UserRound size={17} /></div>
              <div className="agent-message-body"><div className="agent-message-meta"><strong>你</strong><time>本次输入</time></div>
                <div className="agent-message-text">{setup.query}</div>
              </div>
            </div>}
            {setupMessage && <div className="agent-inline-note"><Sparkles size={17} />{setupMessage}</div>}
            {setup && <div className="agent-return-setup">
              <div className="agent-card-title"><span><RotateCcw size={18} /></span><div><strong>退货任务信息</strong><small>{setup.application ? "准备申请预览" : "只核对资格，不提交申请"}</small></div></div>
              {!setup.order ? <><label>订单号<input value={setup.orderNumber} onChange={e => setSetup({ ...setup, orderNumber: e.target.value })} placeholder="例如 A1003" /></label>
                <button disabled={setup.loading || !setup.orderNumber.trim()} onClick={() => void loadReturnContext(setup.orderNumber.trim())}>{setup.loading ? <LoaderCircle className="spin" size={16} /> : <FileSearch size={16} />}读取订单与可用规则</button></>
                : <>
                  <div className="agent-fact-strip"><span>订单</span><strong>{setup.order.number}</strong><span>实签</span><strong>{setup.order.signed_at || "未提供"}</strong></div>
                  <label>订单商品<select value={setup.itemId} onChange={e => setSetup({ ...setup, itemId: e.target.value, policyId: "" })}>{(setup.order.items || []).map((item: any) => <option key={item.id} value={item.id}>{item.product_name} · {item.sku} · 共 {item.quantity} 件</option>)}</select></label>
                  <label>适用规则<select value={setup.policyId} onChange={e => setSetup({ ...setup, policyId: e.target.value })}><option value="">选择适用于该商品的规则</option>{applicablePolicies.map((policy: any) => <option key={policy.id} value={policy.id}>{policy.rules.return_basis === "quality_issue" ? "质量问题" : "无理由退货"} · {policy.rules.window_days} 天 · {policy.rules.requires_unused ? "须未使用" : "未限定使用状态"}</option>)}</select></label>
                  {!!selectedSetupItem && !applicablePolicies.length && <p className="agent-inline-warning"><CircleAlert size={16} />当前商品没有可用的已发布退货规则；不代表其他规则下没有退货权利。</p>}
                  <div className="agent-form-row"><label>申请数量<input type="number" min={1} max={selectedSetupItem?.quantity || 1} value={setup.quantity} onChange={e => setSetup({ ...setup, quantity: Number(e.target.value) })} /></label><label>商品状态<select value={setup.condition} onChange={e => setSetup({ ...setup, condition: e.target.value as ReturnSetup["condition"] })}>{Object.entries(conditionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
                  <label>退货原因<input value={setup.reason} onChange={e => setSetup({ ...setup, reason: e.target.value })} maxLength={500} placeholder="说明想核对的原因" /></label>
                  <label>退货分支<select value={setup.basis} onChange={e => setSetup({ ...setup, basis: e.target.value as ReturnSetup["basis"] })}><option value="no_reason">无理由退货</option><option value="quality_issue">质量问题退货</option></select></label>
                  <div className="agent-card-actions"><button disabled={!setup.policyId || !setup.itemId || setup.loading || busy} onClick={() => void startReturnRun()}><Sparkles size={16} />开始 Agent 核对</button><button className="quiet" onClick={() => { setSetup(null); setSetupMessage(""); }}>取消</button></div>
                  <small className="agent-safety-note">Agent 会读取政策并生成核对结果。正式申请仍需你确认，之后由审批员独立审批。</small>
                </>}
              {setup.error && <p className="error" role="alert">{setup.error}</p>}
            </div>}
            {isWorking && <div className="agent-live-bubble"><LoaderCircle className="spin" size={17} />{currentMessage || "Agent 正在执行；右侧显示后端实际事件。"}</div>}
            {notice && <p className="agent-inline-note"><Check size={16} />{notice}</p>}
            {error && <p className="error" role="alert">{error}</p>}
          </div>

          <form className="agent-composer" onSubmit={event => void sendMessage(event)}>
            <textarea aria-label="发送给售后 Agent" value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void sendMessage(); } }} placeholder={waitingForInput ? "补充 Agent 正在等待的信息…（Shift+Enter 换行）" : waitingForConfirmation ? "可输入明确确认，或在右侧选择操作…" : "描述你要处理的售后问题…（Shift+Enter 换行）"} maxLength={1200} disabled={busy || isWorking} />
            <div className="agent-composer-footer"><span>Enter 发送 · Shift+Enter 换行</span><button disabled={!text.trim() || busy || isWorking} aria-label="发送消息"><Send size={17} />发送</button></div>
          </form>
        </section>

        <aside className="agent-task-panel" aria-label="Agent 处理区">
          <div className="agent-task-heading"><span><Activity size={20} /></span><div><strong>Agent 处理区</strong><small>{currentRun ? statusLabels[currentStatus] || currentStatus : "等待开始任务"}</small></div></div>
          {currentRun ? <>
            <div className="agent-current-task"><small>当前任务</small><strong>{cleanMessage(currentRun.input?.query || "售后协作任务")}</strong><span>任务 {currentRun.id.slice(0, 8)} · {currentRun.kind === "aftersales" ? "售后工作流" : currentRun.kind}</span></div>
            <div className="agent-timeline-heading"><strong>真实执行记录</strong><span>{events.length} 条</span></div>
            {events.length ? <ol className="agent-timeline">{events.map(event => <li key={event.id} className={event.type === "tool.failed" || event.type === "agent.failed" ? "event-failed" : event.type === "tool.started" ? "event-running" : ""}>
              <i /> <div><strong>{toolEventTitle(event)}</strong>{event.payload?.name && <small>{toolLabels[event.payload.name] || event.payload.name}</small>}{event.type === "run.finished" && event.payload?.status && <small>{statusLabels[event.payload.status] || event.payload.status}</small>}</div><time>{timeLabel(event.timestamp)}</time>
            </li>)}</ol> : <p className="agent-panel-empty">暂无执行事件。开始处理后，这里只显示后端真实产生的步骤。</p>}

            {currentOrder && <div className="agent-data-card"><div className="agent-card-title"><span><Truck size={17} /></span><div><strong>订单核验</strong><small>来源：订单服务 · 本次任务</small></div><b>已读取</b></div>
              <dl><dt>订单号</dt><dd>{currentOrder.number}</dd><dt>付款状态</dt><dd>{currentOrder.payment_status || "未提供"}</dd><dt>物流状态</dt><dd>{currentOrder.shipping_status || "未提供"}</dd><dt>实际签收</dt><dd>{currentOrder.signed_at || "未提供，不能以预计送达代替"}</dd></dl>
              {(currentOrder.items || []).map((item: any) => <p className="agent-product-line" key={item.id}>{item.product_name} · {item.sku} · {item.quantity} 件</p>)}
            </div>}

            {!!currentEvidence.length && <div className="agent-data-card"><div className="agent-card-title"><span><FileSearch size={17} /></span><div><strong>政策依据</strong><small>来自本次实际检索结果</small></div></div>
              {currentEvidence.slice(0, 4).map((item: any, index: number) => <details className="agent-evidence" key={item.id || index}><summary>{item.title || item.section || "政策原文"} {item.version ? `· v${item.version}` : ""}</summary><blockquote>{item.content || item.text || ""}</blockquote><small>{item.filename || "已发布知识"}{item.section ? ` · ${item.section}` : ""}{item.page_start ? ` · 第 ${item.page_start} 页` : ""}</small>{item.policy_scope && <span className="agent-scope-tag">{item.policy_scope}</span>}</details>)}
            </div>}

            {proposal && <div className="agent-action-preview"><div className="agent-card-title"><span><ClipboardCheck size={17} /></span><div><strong>申请预览</strong><small>未提交 · 等待确认</small></div></div><dl><dt>订单</dt><dd>{proposal.order_number}</dd><dt>申请类型</dt><dd>{proposal.shipping_kind === "expedite" ? "加急申请" : proposal.return_item_id ? "退货申请" : "普通催单"}</dd><dt>原因</dt><dd>{proposal.reason || "未提供"}</dd><dt>将执行</dt><dd>{proposal.effect || "等待确认后继续"}</dd></dl></div>}
            {currentRequest && <div className="agent-action-preview"><div className="agent-card-title"><span><ShieldCheck size={17} /></span><div><strong>业务状态</strong><small>来自已登记的业务记录</small></div></div><p>{currentRequest.status} · {currentRequest.ticket_id || currentRequest.receipt_id || "尚无工单"}</p></div>}
            {currentRun.output?.context && <ContextCard context={currentRun.output.context} />}
            {currentRun.output?.write_in_flight && <p className="agent-inline-warning"><CircleAlert size={16} />提交状态尚未核实。请先恢复并查询实际记录，不要重复发起申请。</p>}
            {waitingForInput && currentRun.output?.pause_kind !== "policy_scope" && <div className="agent-wait-card"><strong>需要你补充</strong><p>{currentRun.output?.answer || "请补充右侧列出的信息。"}</p>{currentRun.output?.missing?.length > 0 && <small>{currentRun.output.missing.join("、")}</small>}
              {currentRun.output?.pause_kind === "condition_confirmation" && <button disabled={busy} onClick={() => void resumeRun("accept_condition", { condition_proposal_hash: currentRun.output?.condition_proposal?.proposal_hash }, "我确认这段商品状态描述")}>确认 Agent 提取的商品状态</button>}
              {currentRun.output?.pause_kind === "quality_wait" && <button disabled={busy} onClick={() => void resumeRun("refresh")}>刷新质检结果</button>}
            </div>}
            {waitingForConfirmation && <div className="agent-wait-card confirmation-card"><strong>{readOnlyReturnRun ? "退货核对已完成" : "等待你确认申请"}</strong><p>{currentRun.output?.answer || (readOnlyReturnRun ? "这轮仅核对规则或资格，不会提交申请。" : "核对预览后，确认才会登记申请。")}</p>
              {!readOnlyReturnRun && <button disabled={busy} onClick={() => void resumeRun("confirm", {}, "我确认按当前预览提交申请")}>确认本次申请</button>}
              <button className="quiet" disabled={busy} onClick={() => void resumeRun("cancel", {}, readOnlyReturnRun ? "结束本次只读核对" : "我取消本次申请")}>{readOnlyReturnRun ? "结束核对（不提交）" : "取消本次申请"}</button>
              <small>{readOnlyReturnRun ? "只读核对不会登记退货申请。" : "确认只会进入对应的登记或审批流程；不会跳过独立审批，也不会执行真实退款。"}</small>
            </div>}
            {currentStatus === "awaiting_approval" && <div className="agent-wait-card"><strong>等待审批员处理</strong><p>客服不能代替审批员批准。可刷新查看实际审批状态。</p><button disabled={busy} onClick={() => void resumeRun("refresh")}>刷新审批状态</button></div>}
            {currentStatus === "interrupted" && <div className="agent-wait-card"><strong>任务暂停</strong><p>{currentRun.output?.answer || "当前流程需要核实状态后才能继续。"}</p><button disabled={busy} onClick={() => void resumeRun("retry")}>恢复并核实</button></div>}
            {currentRun.error_code && <p className="agent-inline-warning"><CircleAlert size={16} />错误代码：{currentRun.error_code}</p>}
          </> : <div className="agent-panel-empty"><div className="agent-empty-icon"><Activity size={24} /></div><strong>处理进度会显示在这里</strong><p>发送一条售后需求后，订单查询、政策检索、补问和等待确认等状态将由真实后端事件更新。</p></div>}
        </aside>
      </div>
    </div>
  );
}

function PolicyScopeClarification({
  run,
  busy,
  onSubmit,
}: {
  run: Run;
  busy: boolean;
  onSubmit: (audience: Record<string, string>) => void;
}) {
  const options = run.output?.scope_options || {};
  const profiles: Record<string, string>[] = options.profiles || [];
  const missing: string[] = run.output?.missing || [];
  const [profileIndex, setProfileIndex] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const labels: Record<string, string> = {
    merchant: "商家",
    region: "地区",
    channel: "购买渠道",
    product: "商品范围",
  };

  useEffect(() => {
    setProfileIndex("");
    setValues({});
  }, [run.id, run.output?.pause_id]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (profiles.length) {
      const selected = profiles[Number(profileIndex)];
      if (selected) onSubmit(selected);
      return;
    }
    const audience = Object.fromEntries(missing.map(key => [key, values[key]]).filter(([, value]) => !!value)) as Record<string, string>;
    if (missing.length && missing.every(key => audience[key])) onSubmit(audience);
  }

  return <form className="agent-scope-clarification" onSubmit={submit}>
    <div className="agent-scope-clarification-heading"><FileSearch size={18} /><div><strong>先确认政策适用范围</strong><span>我会按你选定的范围继续检索，不会把不同商家或渠道的规则混在一起。</span></div></div>
    {profiles.length ? <label>适用范围<select required value={profileIndex} onChange={event => setProfileIndex(event.target.value)}>
      <option value="">请选择一组范围</option>
      {profiles.map((profile, index) => <option key={JSON.stringify(profile)} value={String(index)}>{Object.entries(profile).map(([key, value]) => `${labels[key] || key}：${value}`).join(" · ")}</option>)}
    </select></label> : missing.map(key => <label key={key}>{labels[key] || key}<select required value={values[key] || ""} onChange={event => setValues(previous => ({ ...previous, [key]: event.target.value }))}>
      <option value="">请选择</option>{(options[key] || []).map((value: string) => <option key={value} value={value}>{value}</option>)}
    </select></label>)}
    <button type="submit" disabled={busy || (profiles.length ? !profileIndex : !missing.length || !missing.every(key => values[key]))}>
      <FileSearch size={16} />按此范围继续检索
    </button>
  </form>;
}

function ConversationResult({ run }: { run: Run }) {
  const output = run.output || {};
  const order = output.order;
  const evidence = output.evidence || output.policy_context || [];
  if (!order && !evidence.length && !output.proposal && !output.request) return null;
  return <div className="agent-message-result">
    {order && <div className="agent-result-chip"><Truck size={15} /><span>订单 {order.number} · {order.shipping_status || "状态未提供"} · 实际签收 {order.signed_at || "未提供"}</span></div>}
    {evidence.slice(0, 2).map((item: any, index: number) => <div className="agent-result-chip" key={item.id || index}><FileSearch size={15} /><span>{item.title || "政策依据"}{item.version ? ` · v${item.version}` : ""}{item.section ? ` · ${item.section}` : ""}</span></div>)}
    {output.proposal && <div className="agent-result-chip"><ClipboardCheck size={15} /><span>已准备申请预览，等待明确确认</span></div>}
    {output.request && <div className="agent-result-chip"><ShieldCheck size={15} /><span>业务状态：{output.request.status} · 继续以实际记录为准</span></div>}
  </div>;
}
