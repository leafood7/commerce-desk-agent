import { useEffect, useState } from "react";
import { api } from "./api";

const statuses: Record<string, string> = {
  WAITING_SHIPMENT: "待寄回",
  SHIPPED: "已寄出",
  IN_TRANSIT: "运输中",
  DELIVERED: "已送达（物流签收）",
  WAREHOUSE_RECEIVED: "仓库已收货",
  INSPECTION_PASSED: "验收通过",
  INSPECTION_FAILED: "验收不通过",
  REFUND_PENDING: "待退款",
  REFUND_PROCESSING: "退款处理中",
  REFUNDED: "退款完成（模拟）",
  REFUND_FAILED: "退款失败（未执行）",
  REFUND_UNKNOWN: "退款结果待核验",
};
const refundStatuses: Record<string, string> = {
  PENDING: "待处理",
  PROCESSING: "处理中",
  SUCCEEDED: "已成功（模拟）",
  FAILED: "未执行",
  UNKNOWN: "结果未知待核验",
};
const inspectionEmoji: Record<string, string> = { passed: "通过", failed: "不通过" };

function money(cents: number | null | undefined) {
  if (cents === null || cents === undefined) return "未提供";
  try {
    return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(cents / 100);
  } catch {
    return (cents / 100).toFixed(2) + " CNY";
  }
}
function when(value?: string | null) {
  if (!value) return null;
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString("zh-CN", { hour12: false });
}

function Timeline({ items }: { items: any[] }) {
  if (!items?.length) return <p className="muted">暂无进度事件。</p>;
  return (
    <ol>
      {items.map((item: any, index: number) => (
        <li key={index}>
          {item.description}
          {item.source === "customer" ? " · 客户登记" : item.source === "logistics" ? " · 模拟物流" : item.source === "warehouse" ? " · 仓库操作" : ""}
          {when(item.created_at) ? <small> · {when(item.created_at)}</small> : null}
        </li>
      ))}
    </ol>
  );
}

function Detail({ selected }: { selected: any }) {
  return (
    <>
      <p role="status">
        履约状态：{statuses[selected.status] || selected.status} · 修订 {selected.version}
      </p>
      <p>订单 {selected.order_number || "未提供"} · 商品行 {selected.item_id || "未提供"} · 商品金额参考值 {money(selected.goods_value_cents)}</p>
      {selected.tracking_number && (
        <p>
          寄件：{selected.carrier} · {selected.tracking_number}
        </p>
      )}
      {selected.inspection && (
        <p>
          仓库验收：{selected.inspection.inspection_status === "received" ? "已收货" : inspectionEmoji[selected.inspection.inspection_result] || "已收货"}
          {selected.inspection.inspection_remark ? ` · ${selected.inspection.inspection_remark}` : ""}
          {when(selected.inspection.received_at) ? ` · 收货 ${when(selected.inspection.received_at)}` : ""}
        </p>
      )}
      {selected.refund && (
        <p>
          退款：{selected.refund.refund_no} · {money(selected.refund.refund_amount_cents)} · {refundStatuses[selected.refund.refund_status] || selected.refund.refund_status}
        </p>
      )}
      <p>
        {selected.actual_refund_executed ? "已执行真实退款" : "模拟退款，未执行真实到账"} · 外部受理方式 {selected.external_mode}
      </p>
      <details>
        <summary>履约时间线</summary>
        <Timeline items={selected.timeline} />
      </details>
    </>
  );
}

export function Fulfillments({ token, role }: { token: string; role: string }) {
  const [rows, setRows] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [carrier, setCarrier] = useState("顺丰速运");
  const [tracking, setTracking] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function reload() {
    setRows((await api("/fulfillments", token)).items);
  }
  useEffect(() => {
    void reload().catch((e) => setError(e.message));
  }, [token]);
  async function act(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string) {
    setSelected(await api("/fulfillments/" + id, token));
    setTracking("");
    setError("");
  }
  return (
    <section className="conversation">
      <h2>客户售后进度</h2>
      <p className="notice">模拟履约链路 · 物流、仓库与退款仅演示，不执行真实退款。审批通过后自动建履约单，客服从登记寄件开始跟进。</p>
      <button disabled={busy} onClick={() => act(reload)}>
        刷新履约单
      </button>
      <label>
        选择履约单
        <select aria-label="选择履约单" value={selected?.id || ""} disabled={busy} onChange={(e) => { if (e.target.value) void act(() => open(e.target.value)); }}>
          <option value="">请选择</option>
          {rows.map((row: any) => (
            <option key={row.id} value={row.id}>
              {row.order_number || row.id.slice(0, 8)} · {statuses[row.status] || row.status} · {row.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      {!rows.length && <p className="muted">暂无履约单。退货申请被审批通过后会自动创建履约单。</p>}
      {selected && (
        <article>
          <h3>履约单 {selected.id.slice(0, 8)}</h3>
          <Detail selected={selected} />
          {role === "support" && selected.status === "WAITING_SHIPMENT" && (
            <>
              <label>
                承运商
                <input aria-label="承运商" value={carrier} maxLength={64} onChange={(e) => setCarrier(e.target.value)} />
              </label>
              <label>
                运单号
                <input aria-label="运单号" placeholder="字母数字与连字符" value={tracking} maxLength={64} onChange={(e) => setTracking(e.target.value)} />
              </label>
              <button
                disabled={busy || !tracking.trim() || !/^[A-Za-z0-9][A-Za-z0-9-]{0,63}$/.test(tracking.trim())}
                onClick={() =>
                  act(async () => {
                    const row = await api(`/fulfillments/${selected.id}/tracking`, token, { fulfillment_id: selected.id, carrier: carrier.trim(), tracking_number: tracking.trim() });
                    setSelected(row);
                  })
                }
              >
                登记客户寄件
              </button>
            </>
          )}
        </article>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

export function Warehouse({ token }: { token: string }) {
  const [rows, setRows] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<"passed" | "failed">("passed");
  const [remark, setRemark] = useState("");
  const [outcome, setOutcome] = useState<"succeeded" | "failed">("succeeded");
  const [timeout, setTimeout_] = useState(false);
  async function reload() {
    setRows((await api("/fulfillments", token)).items);
  }
  useEffect(() => {
    void reload().catch((e) => setError(e.message));
  }, [token]);
  async function act(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function open(id: string) {
    setSelected(await api("/fulfillments/" + id, token));
    setResult("passed");
    setRemark("");
    setOutcome("succeeded");
    setTimeout_(false);
    setError("");
  }
  return (
    <section className="conversation">
      <h2>仓库验收工作台</h2>
      <p className="notice">管理员按状态机推进物流、收货、验收与模拟退款。每步由后端校验，物流“签收”与仓库“收货”相互独立，退款幂等，结果不明须先核验。</p>
      <button disabled={busy} onClick={() => act(reload)}>
        刷新履约单
      </button>
      <label>
        选择履约单
        <select aria-label="选择履约单" value={selected?.id || ""} disabled={busy} onChange={(e) => { if (e.target.value) void act(() => open(e.target.value)); }}>
          <option value="">请选择</option>
          {rows.map((row: any) => (
            <option key={row.id} value={row.id}>
              {row.order_number || row.id.slice(0, 8)} · {statuses[row.status] || row.status} · {row.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      {!rows.length && <p className="muted">暂无履约单。</p>}
      {selected && (
        <article>
          <h3>履约单 {selected.id.slice(0, 8)}</h3>
          <Detail selected={selected} />
          {(selected.status === "SHIPPED" || selected.status === "IN_TRANSIT") && (
            <button disabled={busy} onClick={() => act(async () => setSelected(await api(`/fulfillments/${selected.id}/logistics/advance`, token, {})))}>
              推进模拟物流（{selected.status === "SHIPPED" ? "揽收运输" : "送达签收"}）
            </button>
          )}
          {selected.status === "DELIVERED" && (
            <button disabled={busy} onClick={() => act(async () => setSelected(await api(`/fulfillments/${selected.id}/warehouse/receive`, token, { fulfillment_id: selected.id })))}>
              确认仓库收货
            </button>
          )}
          {selected.status === "WAREHOUSE_RECEIVED" && (
            <>
              <label>
                验收结论
                <select aria-label="验收结论" value={result} disabled={busy} onChange={(e) => setResult(e.target.value as "passed" | "failed")}>
                  <option value="passed">通过</option>
                  <option value="failed">不通过</option>
                </select>
              </label>
              <label>
                验收说明
                <textarea aria-label="验收说明" rows={2} maxLength={500} value={remark} onChange={(e) => setRemark(e.target.value)} />
              </label>
              <button
                disabled={busy}
                onClick={() => act(async () => setSelected(await api(`/fulfillments/${selected.id}/warehouse/inspect`, token, { fulfillment_id: selected.id, result, remark })))}
              >
                登记仓库验收
              </button>
            </>
          )}
          {(selected.status === "INSPECTION_PASSED" || selected.status === "REFUND_FAILED") && (
            <>
              <label>
                模拟退款结果
                <select aria-label="模拟退款结果" value={outcome} disabled={busy} onChange={(e) => setOutcome(e.target.value as "succeeded" | "failed")}>
                  <option value="succeeded">成功</option>
                  <option value="failed">失败</option>
                </select>
              </label>
              <label>
                <input type="checkbox" checked={timeout} onChange={(e) => setTimeout_(e.target.checked)} /> 模拟退款超时（结果待核验）
              </label>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => setSelected(await api(`/fulfillments/${selected.id}/refund/initiate`, token, { fulfillment_id: selected.id, idempotency_key: crypto.randomUUID(), outcome, timeout })))
                }
              >
                发起模拟退款
              </button>
            </>
          )}
          {selected.status === "REFUND_UNKNOWN" && (
            <button disabled={busy} onClick={() => act(async () => setSelected(await api(`/fulfillments/${selected.id}/refund/reconcile`, token, {})))}>
              核验退款结果
            </button>
          )}
        </article>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}