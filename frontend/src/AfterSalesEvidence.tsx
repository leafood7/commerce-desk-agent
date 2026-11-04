import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";

const kinds: Record<string, string> = {
  product_photo: "商品照片",
  express_waybill: "快递单",
  invoice: "发票凭证",
  quality_photo: "质检照片",
  other: "其他",
};
const statusLabels: Record<string, string> = {
  UPLOADED: "已上传",
  PROCESSING: "提取中",
  PENDING_REVIEW: "待人工核验",
  VERIFIED: "已核验",
  REJECTED: "已驳回",
  NEEDS_SUPPLEMENT: "待补充材料",
  EXTRACTION_FAILED: "提取失败",
};
const fieldLabels: Record<string, string> = {
  order_number: "订单号",
  amount: "金额",
  date: "日期",
  tracking_number: "运单号",
  carrier: "承运商",
};
const decisions: { value: string; label: string }[] = [
  { value: "verified", label: "核验通过" },
  { value: "rejected", label: "驳回" },
  { value: "needs_supplement", label: "要求补充材料" },
];

type Attachment = {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: string;
  return_request_id: string | null;
  order_id: string | null;
  note: string | null;
  status: string;
  extracted_data: { fields?: { field: string; value: string }[]; notes?: string | null } | null;
  extraction_error: string | null;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
};

function size(bytes: number) {
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + " KB";
  return bytes + " B";
}
function dateValue(value?: string | null) {
  if (!value) return "未提供";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString("zh-CN", { hour12: false });
}

async function download(token: string, attachment: Attachment) {
  const response = await fetch("/api/v1/after-sales/attachments/" + attachment.id + "/file", {
    headers: { Authorization: "Bearer " + token },
  });
  if (!response.ok) throw new Error("无法读取凭证。");
  const url = URL.createObjectURL(await response.blob());
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = attachment.filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function AfterSalesEvidence({ token, role }: { token: string; role: string }) {
  const [rows, setRows] = useState<Attachment[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("product_photo");
  const [returnRequestId, setReturnRequestId] = useState("");
  const [orderId, setOrderId] = useState("");
  const [note, setNote] = useState("");
  const [reviewNote, setReviewNote] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canUpload = ["support", "admin"].includes(role);

  async function refresh() {
    setRows((await api("/after-sales/attachments", token)).items);
  }
  useEffect(() => {
    void refresh().catch((e) => setError(e.message));
  }, [token]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      if (returnRequestId.trim()) form.append("return_request_id", returnRequestId.trim());
      if (orderId.trim()) form.append("order_id", orderId.trim());
      if (note.trim()) form.append("note", note.trim());
      await api("/after-sales/attachments", token, form);
      setFile(null);
      setNote("");
      setReturnRequestId("");
      setOrderId("");
      setNotice("凭证已上传并完成规则提取，待人工核验后才可作为售后依据。");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function review(id: string, decision: string) {
    const text = (reviewNote[id] || "").trim();
    if (!text) {
      setError("请先填写核验意见。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/after-sales/attachments/${id}/review`, token, { decision, note: text });
      setReviewNote((old) => ({ ...old, [id]: "" }));
      setNotice("核验结果已记录。");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="conversation">
      <h2>售后凭证</h2>
      <p className="notice">
        上传售后相关的照片、快递单、发票等凭证。系统对 PDF 做规则化候选字段提取（图片本版不做 OCR），
        提取结果仅为候选，需人工核验后才可作为售后依据，不改动退货审批规则。
      </p>
      {canUpload && (
        <form onSubmit={upload}>
          <label>
            凭证文件
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.webp,.gif,.pdf"
              required
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
          <label>
            凭证类型
            <select aria-label="凭证类型" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(kinds).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            关联退货申请（可不填）
            <input
              aria-label="关联退货申请"
              placeholder="退货申请 ID"
              maxLength={36}
              value={returnRequestId}
              onChange={(e) => setReturnRequestId(e.target.value)}
            />
          </label>
          <label>
            关联订单（可不填）
            <input
              aria-label="关联订单"
              placeholder="订单 ID"
              maxLength={36}
              value={orderId}
              onChange={(e) => setOrderId(e.target.value)}
            />
          </label>
          <label>
            备注
            <textarea aria-label="凭证备注" rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <small>支持 PNG、JPG、JPEG、WEBP、GIF 和 PDF，单文件不超过 20 MB。</small>
          <button disabled={busy || !file}>{busy ? "正在上传…" : "上传凭证"}</button>
        </form>
      )}
      <button disabled={busy} onClick={() => { void refresh().catch((e) => setError(e.message)); }}>
        刷新凭证
      </button>
      {notice && <p className="success" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!rows.length && <p className="muted">尚无售后凭证。</p>}
      {rows.map((row) => {
        const fields = row.extracted_data?.fields || [];
        const reviewable = ["PENDING_REVIEW", "EXTRACTION_FAILED"].includes(row.status);
        return (
          <article key={row.id}>
            <p>
              <strong>{row.filename}</strong> · {kinds[row.kind] || row.kind} · {size(row.size_bytes)} ·{" "}
              <strong>{statusLabels[row.status] || row.status}</strong>
            </p>
            <p className="muted">
              {dateValue(row.created_at)}
              {row.return_request_id ? ` · 退货申请 ${row.return_request_id.slice(0, 8)}` : ""}
              {row.order_id ? ` · 订单 ${row.order_id.slice(0, 8)}` : ""}
            </p>
            {row.note && <blockquote>{row.note}</blockquote>}
            {row.extraction_error && <p className="error">提取失败：{row.extraction_error}（原始文件已保留，可直接人工核验）</p>}
            {fields.length > 0 && (
              <details>
                <summary>提取候选字段（待核验，非最终事实）</summary>
                <ul>
                  {fields.map((field, index) => (
                    <li key={index}>
                      {fieldLabels[field.field] || field.field}：{field.value}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {row.extracted_data?.notes && <p className="muted">{row.extracted_data.notes}</p>}
            {row.review_note && (
              <p>
                核验意见（{dateValue(row.reviewed_at)}）：{row.review_note}
              </p>
            )}
            <button className="quiet" disabled={busy} onClick={() => download(token, row).catch((e) => setError(e.message))}>
              下载/查看原文件
            </button>
            {canUpload && reviewable && (
              <>
                <label>
                  核验意见
                  <textarea
                    aria-label="核验意见"
                    rows={2}
                    maxLength={500}
                    value={reviewNote[row.id] || ""}
                    onChange={(e) => setReviewNote((old) => ({ ...old, [row.id]: e.target.value }))}
                  />
                </label>
                <div>
                  {decisions.map((decision) => (
                    <button key={decision.value} disabled={busy} onClick={() => void review(row.id, decision.value)}>
                      {decision.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </article>
        );
      })}
    </section>
  );
}