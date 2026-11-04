import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";

const pairStatuses: Record<string, string> = {
  NO_CONFLICT: "无冲突",
  RESOLVED_BY_PRIORITY: "按已确认优先级适用",
  SUPERSEDED_BY_VERSION: "新版本替代（按版本适用）",
  POTENTIAL_CONFLICT: "潜在冲突，需人工裁决",
};
const reasonLabels: Record<string, string> = {
  RETURN_WINDOW_DIFFERS: "退货期限不同",
  ITEM_CONDITION_DIFFERS: "商品状态要求不同",
};
const conflictStatuses: Record<string, string> = {
  pending_review: "待人工裁决",
  resolved: "已裁决",
};

type Rule = {
  policy_id: string;
  version_id: string;
  version_number: string;
  sku: string;
  return_basis: "no_reason" | "quality_issue" | "unspecified";
  sales_channel: string;
  effective_from: string;
  effective_until: string;
  window_days: string;
  requires_unused: boolean;
  priority: string;
};

type Conflict = {
  id: string;
  conflict_type: string;
  rule_ids: string[];
  version_ids: string[];
  evidence_refs: string[];
  reasons: string[];
  status: string;
  resolution: Record<string, string> | null;
  created_at: string;
};

function rule(overrides: Partial<Rule> = {}): Rule {
  return {
    policy_id: "",
    version_id: "",
    version_number: "",
    sku: "SKU1001",
    return_basis: "no_reason",
    sales_channel: "official_store",
    effective_from: "2026-10-01",
    effective_until: "2026-12-31",
    window_days: "7",
    requires_unused: true,
    priority: "",
    ...overrides,
  };
}
function dateValue(value?: string | null) {
  if (!value) return "未提供";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString("zh-CN", { hour12: false });
}
function payload(item: Rule) {
  const body: Record<string, unknown> = {
    policy_id: item.policy_id.trim(),
    sku: item.sku.trim(),
    return_basis: item.return_basis,
    sales_channel: item.sales_channel.trim(),
    effective_from: item.effective_from,
    window_days: Number(item.window_days),
    requires_unused: item.requires_unused,
    source_refs: [],
  };
  if (item.version_id.trim()) body.version_id = item.version_id.trim();
  if (item.version_number.trim()) body.version_number = Number(item.version_number);
  if (item.effective_until.trim()) body.effective_until = item.effective_until;
  if (item.priority.trim()) body.priority = Number(item.priority);
  return body;
}

export function PolicyConflicts({ token, role }: { token: string; role: string }) {
  const [rows, setRows] = useState<Conflict[]>([]);
  const [selected, setSelected] = useState<{ conflict: Conflict; reviews: any[] } | null>(null);
  const [rules, setRules] = useState<Rule[]>([
    rule({ policy_id: "POLICY-A", window_days: "7" }),
    rule({ policy_id: "POLICY-B", window_days: "3" }),
  ]);
  const [mode, setMode] = useState<"rules" | "versions">("rules");
  const [versionIds, setVersionIds] = useState("");
  const [result, setResult] = useState<any>(null);
  const [decision, setDecision] = useState<"preferred_rule" | "no_conflict">("preferred_rule");
  const [preferred, setPreferred] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    setRows((await api("/policies/conflicts", token)).items);
  }
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
  }, [token]);

  function update(index: number, patch: Partial<Rule>) {
    setRules((old) => old.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  }

  async function check(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    setResult(null);
    try {
      const versions = versionIds.split(",").map((item) => item.trim()).filter(Boolean);
      const body =
        mode === "versions"
          ? { version_ids: versions }
          : { rules: rules.map(payload) };
      const data = await api("/policies/conflicts/check", token, body);
      setResult(data);
      setNotice(data.blocks_automatic_decision ? "检测到未裁决的潜在冲突，暂不可自动给出确定资格结论。" : "本次检查未产生需要裁决的冲突。");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setError("");
    setNotice("");
    setSelected(await api("/policies/conflicts/" + id, token));
    setDecision("preferred_rule");
    setPreferred("");
    setNote("");
  }

  async function resolve() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await api(`/policies/conflicts/${selected.conflict.id}/resolve`, token, {
        decision,
        ...(decision === "preferred_rule" ? { preferred_rule_id: preferred } : {}),
        note: note.trim(),
      });
      setNotice("裁决已记录。");
      setSelected(await api("/policies/conflicts/" + selected.conflict.id, token));
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="conversation">
      <h2>政策冲突</h2>
      <p className="notice">
        按结构化规则比较适用范围：只有同一 SKU、同一退货原因、同一销售渠道且生效区间重叠，且期限或商品状态要求不一致时，才进入冲突候选。优先级与版本替代只依据已记录的依据，模型不参与裁决。
      </p>
      {role === "admin" && (
        <form onSubmit={check}>
          <h3>检查候选规则</h3>
          <label>
            检查方式
            <select value={mode} onChange={(e) => setMode(e.target.value as "rules" | "versions")}>
              <option value="rules">手动填写结构化规则</option>
              <option value="versions">复用已有政策版本规则映射</option>
            </select>
          </label>
          {mode === "versions" && (
            <>
              <label>
                政策版本 ID（逗号分隔）
                <input
                  required
                  maxLength={800}
                  value={versionIds}
                  placeholder="例如 version-id-1, version-id-2"
                  onChange={(e) => setVersionIds(e.target.value)}
                />
              </label>
              <small>从管理员已确认的规则映射组装；缺少生效日期或销售渠道的规则会跳过并提示，不推测冲突。</small>
            </>
          )}
          {mode === "rules" && rules.map((item, index) => (
            <article key={index}>
              <p>
                <strong>规则 {index + 1}</strong>
                {rules.length > 2 && (
                  <button type="button" className="quiet" onClick={() => setRules((old) => old.filter((_, position) => position !== index))}>
                    移除
                  </button>
                )}
              </p>
              <label>
                政策编号
                <input required maxLength={36} value={item.policy_id} onChange={(e) => update(index, { policy_id: e.target.value })} />
              </label>
              <label>
                版本 ID（可选）
                <input maxLength={36} value={item.version_id} onChange={(e) => update(index, { version_id: e.target.value })} />
              </label>
              <label>
                版本序号（可选）
                <input type="number" min={1} value={item.version_number} onChange={(e) => update(index, { version_number: e.target.value })} />
              </label>
              <label>
                SKU
                <input required maxLength={64} value={item.sku} onChange={(e) => update(index, { sku: e.target.value })} />
              </label>
              <label>
                退货原因
                <select value={item.return_basis} onChange={(e) => update(index, { return_basis: e.target.value as Rule["return_basis"] })}>
                  <option value="no_reason">无理由</option>
                  <option value="quality_issue">质量问题</option>
                  <option value="unspecified">未声明</option>
                </select>
              </label>
              <label>
                销售渠道
                <input required maxLength={64} value={item.sales_channel} onChange={(e) => update(index, { sales_channel: e.target.value })} />
              </label>
              <label>
                生效日期
                <input type="date" required value={item.effective_from} onChange={(e) => update(index, { effective_from: e.target.value })} />
              </label>
              <label>
                失效日期（可选）
                <input type="date" value={item.effective_until} onChange={(e) => update(index, { effective_until: e.target.value })} />
              </label>
              <label>
                退货期限（天）
                <input type="number" required min={1} max={365} value={item.window_days} onChange={(e) => update(index, { window_days: e.target.value })} />
              </label>
              <label>
                <input type="checkbox" checked={item.requires_unused} onChange={(e) => update(index, { requires_unused: e.target.checked })} /> 要求商品未使用
              </label>
              <label>
                优先级（可选，仅限已确认依据）
                <input type="number" min={0} max={100} value={item.priority} onChange={(e) => update(index, { priority: e.target.value })} />
              </label>
            </article>
          ))}
          {mode === "rules" && rules.length < 10 && (
            <button type="button" className="quiet" onClick={() => setRules((old) => [...old, rule({ policy_id: `POLICY-${old.length + 1}` })] )}>
              添加规则
            </button>
          )}
          <button disabled={busy || (mode === "rules" ? rules.length < 2 : !versionIds.trim())}>
            {busy ? "正在检查…" : "检查冲突"}
          </button>
        </form>
      )}
      {result && (
        <article>
          <h3>检查结果</h3>
          <p>
            已比较 {result.rules} 条规则 ·{" "}
            {result.blocks_automatic_decision ? "存在未裁决冲突，禁止自动给出确定资格结论" : "未产生需裁决冲突"}
          </p>
          <ul>
            {(result.pairs || []).map((pair: any, index: number) => (
              <li key={index}>
                {pair.rule_ids.join(" ↔ ")}：{pairStatuses[pair.status] || pair.status}
                {pair.reasons?.length ? `（${pair.reasons.map((reason: string) => reasonLabels[reason] || reason).join("、")}）` : ""}
                {pair.preferred_rule_id ? ` · 适用 ${pair.preferred_rule_id}` : ""}
              </li>
            ))}
          </ul>
          {result.skipped?.length > 0 && (
            <p className="muted">
              以下规则因缺少 {result.skipped.map((item: any) => item.missing.join("/")).join("；")} 未参与比较：{result.skipped.map((item: any) => item.rule_id).join("、")}
            </p>
          )}
          {(result.conflicts || []).length > 0 && <p>新记录冲突 {result.conflicts.length} 条，可在下方列表查看并裁决。</p>}
        </article>
      )}
      <button disabled={busy} onClick={() => { void refresh().catch((e) => setError(e.message)); }}>
        刷新冲突列表
      </button>
      {notice && <p className="success" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!rows.length && <p className="muted">暂无政策冲突记录。</p>}
      {rows.map((row) => (
        <article key={row.id}>
          <p>
            <strong>{conflictStatuses[row.status] || row.status}</strong> · {row.conflict_type} · {dateValue(row.created_at)}
          </p>
          <p className="muted">
            规则 {row.rule_ids.join(" ↔ ")}
            {row.version_ids.length ? ` · 版本 ${row.version_ids.join("、")}` : ""}
            {row.evidence_refs.length ? ` · 原文引用 ${row.evidence_refs.length} 条` : ""}
          </p>
          <button className="quiet" disabled={busy} onClick={() => void open(row.id).catch((e) => setError(e.message))}>
            查看证据与裁决
          </button>
        </article>
      ))}
      {selected && (
        <article>
          <h3>冲突 {selected.conflict.id.slice(0, 8)}</h3>
          <p>
            状态：{conflictStatuses[selected.conflict.status] || selected.conflict.status} · 原因：{selected.conflict.reasons.map((reason) => reasonLabels[reason] || reason).join("、")}
          </p>
          <p className="muted">
            规则：{selected.conflict.rule_ids.join(" ↔ ")}
            {selected.conflict.version_ids.length ? ` · 版本：${selected.conflict.version_ids.join("、")}` : ""}
          </p>
          {selected.conflict.evidence_refs.length > 0 && (
            <p className="muted">原文引用：{selected.conflict.evidence_refs.join("、")}</p>
          )}
          {selected.conflict.resolution && (
            <p>
              裁决：{selected.conflict.resolution.decision === "preferred_rule" ? `适用 ${selected.conflict.resolution.preferred_rule_id}` : "确认不构成冲突"}
              {" · "}
              {selected.conflict.resolution.note}
              {" · "}
              {dateValue(selected.conflict.resolution.reviewed_at)}
            </p>
          )}
          {(selected.reviews || []).length > 0 && (
            <details>
              <summary>裁决历史</summary>
              <ol>
                {selected.reviews.map((review: any) => (
                  <li key={review.id}>
                    {review.decision === "preferred_rule" ? `适用 ${review.preferred_rule_id}` : "确认不构成冲突"} · {review.resolution_note} · {dateValue(review.created_at)}
                  </li>
                ))}
              </ol>
            </details>
          )}
          {role === "approver" && selected.conflict.status === "pending_review" && (
            <>
              <label>
                裁决结论
                <select
                  value={decision}
                  onChange={(e) => setDecision(e.target.value as "preferred_rule" | "no_conflict")}
                >
                  <option value="preferred_rule">选择适用规则</option>
                  <option value="no_conflict">确认不构成冲突</option>
                </select>
              </label>
              {decision === "preferred_rule" && (
                <label>
                  适用规则
                  <select value={preferred} onChange={(e) => setPreferred(e.target.value)}>
                    <option value="">请选择</option>
                    {selected.conflict.rule_ids.map((rule_id) => (
                      <option key={rule_id} value={rule_id}>
                        {rule_id}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label>
                裁决说明
                <textarea rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <button disabled={busy || !note.trim() || (decision === "preferred_rule" && !preferred)} onClick={() => void resolve()}>
                提交裁决
              </button>
            </>
          )}
        </article>
      )}
    </section>
  );
}