import { useState } from "react";
import { api } from "./api";

const returnLabels: Record<string, string> = {
  draft: "草稿",
  pending_approval: "等待审核",
  approved: "已通过审核",
  rejected: "未通过审核",
  accepted_simulated: "模拟受理",
  cancelled: "已取消",
};
const fulfillmentLabels: Record<string, string> = {
  WAITING_SHIPMENT: "待寄回",
  SHIPPED: "已寄出",
  IN_TRANSIT: "运输中",
  DELIVERED: "已送达",
  WAREHOUSE_RECEIVED: "仓库已收货",
  INSPECTION_PASSED: "验收通过",
  INSPECTION_FAILED: "验收不通过",
  REFUND_PENDING: "待退款",
  REFUND_PROCESSING: "退款处理中",
  REFUNDED: "模拟退款完成",
  REFUND_FAILED: "退款未执行",
  REFUND_UNKNOWN: "退款结果待核验",
};
const refundLabels: Record<string, string> = {
  PENDING: "待处理",
  PROCESSING: "处理中",
  SUCCEEDED: "模拟成功（实际到账未确认）",
  FAILED: "未执行成功",
  UNKNOWN: "结果未知待核验",
};
const unknownLabels: Record<string, string> = {
  actual_refund_status: "实际退款结果",
  refund_result: "退款结果",
  pending_evidence_review: "待核验凭证",
  policy_conflict_resolution: "政策冲突裁决",
};
const prohibitedLabels: Record<string, string> = {
  refund_success: "退款成功",
  refund_arrival_date: "退款到账时间",
  return_completed: "退货完成",
};
const draftStatuses: Record<string, string> = {
  draft: "草稿",
  edited: "已编辑",
  confirmed: "已确认",
};
const generationLabels: Record<string, string> = {
  template: "规则模板",
  llm: "模型辅助",
  human_edit: "人工编辑",
};
const fallbackLabels: Record<string, string> = {
  server_disabled: "服务端未启用模型，已回退模板",
  timeout: "模型超时，已回退模板",
  schema_invalid: "模型输出格式非法，已回退模板",
  "Composed text failed validation": "组装文本未通过校验，已回退模板",
};
function fallbackText(reason?: string | null) {
  if (!reason) return "";
  if (fallbackLabels[reason]) return fallbackLabels[reason];
  if (reason.startsWith("引用了未经授权的事实")) return "模型引用了未授权事实，已回退模板";
  return reason;
}

function dateValue(value?: string | null) {
  if (!value) return "未提供";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString("zh-CN", { hour12: false });
}

export function ReplyDrafts({ token, role }: { token: string; role: string }) {
  const [requestId, setRequestId] = useState("");
  const [mode, setMode] = useState<"template" | "llm">("template");
  const [current, setCurrent] = useState<any>(null);
  const [content, setContent] = useState("");
  const [history, setHistory] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canWrite = ["support", "admin"].includes(role);

  async function refreshHistory(id: string) {
    setHistory((await api("/reply-drafts?return_request_id=" + encodeURIComponent(id), token)).items);
  }

  async function generate() {
    if (!requestId.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = await api("/reply-drafts", token, {
        return_request_id: requestId.trim(),
        mode,
      });
      setCurrent(data);
      setContent(data.content);
      await refreshHistory(data.return_request_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setBusy(true);
    setError("");
    try {
      const data = await api("/reply-drafts/" + id, token);
      setCurrent(data);
      setContent(data.content);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    if (!current) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = await api(`/reply-drafts/${current.id}/edit`, token, { content: content.trim() });
      setCurrent(data);
      setContent(data.content);
      setNotice("编辑已保存为新版本。");
      await refreshHistory(data.return_request_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDraft() {
    if (!current) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = await api(`/reply-drafts/${current.id}/confirm`, token, {
        expected_facts_snapshot_hash: current.facts_snapshot_hash,
      });
      setCurrent(data);
      setNotice("草稿已确认，可用于回复客户。");
      await refreshHistory(data.return_request_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      setNotice("草稿已复制到剪贴板。");
    } catch {
      setNotice("复制失败，请检查浏览器剪贴板权限。");
    }
  }

  const facts = current?.facts_snapshot || {};
  const issues = current?.validation?.issues || [];
  const confirmDisabled =
    busy || !canWrite || current?.status === "confirmed" || current?.stale || issues.length > 0;

  return (
    <section className="conversation">
      <h2>客服回复草稿</h2>
      <p className="notice">
        先整理已核实事实，再生成回复。模板不依赖模型，未知的实际退款结果绝不会被描述为退款成功；业务状态变化后，原草稿不能直接确认。
      </p>
      <label>
        退货申请 ID
        <input
          aria-label="退货申请 ID"
          maxLength={36}
          value={requestId}
          placeholder="输入退货申请 ID"
          onChange={(e) => setRequestId(e.target.value)}
        />
      </label>
      <label>
        生成方式
        <select
          aria-label="生成方式"
          value={mode}
          onChange={(e) => setMode(e.target.value as "template" | "llm")}
        >
          <option value="template">规则模板</option>
          <option value="llm">模型辅助（服务端启用时可用）</option>
        </select>
      </label>
      <small>模型辅助只组织语言，关键业务事实仍由后端固定短语提供；服务端未启用时会自动回退模板。</small>
      <button disabled={busy || !canWrite || !requestId.trim()} onClick={() => void generate()}>
        {busy ? "正在生成…" : "生成回复草稿"}
      </button>
      {notice && <p className="success" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {current && (
        <article>
          <h3>草稿 v{current.version}</h3>
          <p>
            {draftStatuses[current.status] || current.status} · 生成人 {current.created_by.slice(0, 8)} · {dateValue(current.created_at)}
            {current.stale ? " · 业务状态已更新，请重新核对" : ""}
          </p>
          <p className="muted">
            生成方式 {generationLabels[current.generation_mode] || current.generation_mode || "规则模板"}
            {current.model_name ? ` · ${current.model_name}` : ""}
            {current.edited_by_human ? " · 人工编辑" : ""}
            {current.validation_status === "passed"
              ? " · 校验通过"
              : current.validation_status === "failed"
                ? " · 校验未通过"
                : ""}
          </p>
          {current.fallback_used && current.fallback_reason && (
            <p className="muted">已回退安全模板：{fallbackText(current.fallback_reason)}</p>
          )}
          <p className="muted">
            订单 {facts.order_number || "未提供"} · 退货状态 {returnLabels[facts.return_status] || facts.return_status || "未提供"}
            {facts.fulfillment_status ? ` · 履约 ${fulfillmentLabels[facts.fulfillment_status] || facts.fulfillment_status}` : ""}
            {facts.refund_status ? ` · 退款 ${refundLabels[facts.refund_status] || facts.refund_status}` : " · 暂无退款记录"}
          </p>
          <p className="muted">
            已核验凭证 {(facts.verified_evidence || []).length} 条 · 待核验 {(facts.pending_evidence_count || 0)} 条 · 政策引用 {(facts.policy_evidence_refs || []).length} 条 · 未裁决冲突 {facts.unresolved_conflicts || 0}
          </p>
          {(facts.unknown_facts || []).length > 0 && (
            <p className="muted">
              未知事实：{(facts.unknown_facts || []).map((name: string) => unknownLabels[name] || name).join("、")}
            </p>
          )}
          {(facts.prohibited_claims || []).length > 0 && (
            <p className="muted">
              禁止表述：{(facts.prohibited_claims || []).map((name: string) => prohibitedLabels[name] || name).join("、")}
            </p>
          )}
          <details>
            <summary>事实来源（{(facts.verified_facts || []).length} 条）</summary>
            <ul>
              {(facts.verified_facts || []).map((fact: any, index: number) => (
                <li key={index}>
                  {fact.name} = {String(fact.value)} · 来源 {fact.source}（{String(fact.source_id).slice(0, 8)}）
                </li>
              ))}
            </ul>
          </details>
          {(facts.policy_evidence || []).length > 0 && (
            <details>
              <summary>政策依据（{facts.policy_evidence.length} 条）</summary>
              <ul>
                {facts.policy_evidence.map((item: any, index: number) => (
                  <li key={index}>
                    {item.purpose} · 版本 {item.version_number ?? "未知"} · {String(item.chunk_id).slice(0, 8)}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {(facts.snapshot?.evidence_states || []).length > 0 && (
            <details>
              <summary>凭证核验状态</summary>
              <ul>
                {facts.snapshot.evidence_states.map((item: any) => (
                  <li key={item.id}>
                    {String(item.id).slice(0, 8)} · {item.status}
                    {item.reviewed_at ? ` · ${dateValue(item.reviewed_at)}` : ""}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {issues.length > 0 && (
            <p className="error" role="alert">
              事实校验未通过：{issues.map((issue: any) => issue.claim + "（" + issue.reason + "）").join("；")}
            </p>
          )}
          {issues.length === 0 && <p className="success">事实校验通过：仅包含有来源的表述。</p>}
          <label>
            草稿内容
            <textarea
              aria-label="回复草稿内容"
              rows={5}
              maxLength={2000}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </label>
          <div>
            {canWrite && current.status !== "confirmed" && (
              <>
                <button disabled={busy || !content.trim()} onClick={() => void saveEdit()}>
                  保存编辑
                </button>
                <button disabled={busy || !requestId.trim()} onClick={() => void generate()}>
                  重新生成
                </button>
                <button disabled={confirmDisabled} onClick={() => void confirmDraft()}>
                  确认使用
                </button>
              </>
            )}
            <button className="quiet" disabled={!content.trim()} onClick={() => void copy()}>
              复制草稿
            </button>
          </div>
        </article>
      )}
      {history.length > 0 && (
        <article>
          <h3>版本历史</h3>
          <ol>
            {history.map((item: any) => (
              <li key={item.id}>
                v{item.version} · {draftStatuses[item.status] || item.status} · {dateValue(item.created_at)}
                {current?.id === item.id ? " · 当前查看" : ""}
                <button className="quiet" disabled={busy} onClick={() => void open(item.id)}>
                  查看
                </button>
              </li>
            ))}
          </ol>
        </article>
      )}
    </section>
  );
}