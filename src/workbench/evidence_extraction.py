"""Rule-based candidate extraction from evidence files.

First version: PDF text extraction plus deterministic field rules. Images are
not OCR'd; they are marked for manual review. Extracted values are candidates
only — human verification decides whether they become usable facts.
"""

import re
from pathlib import Path

ORDER_NUMBER = re.compile(
    r"(?:订单号|订单编号|Order\s*(?:No\.?|Number|#)?)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9-]{3,31})",
    re.IGNORECASE,
)
AMOUNT = re.compile(
    r"(?:金额|合计|总计|Amount|Total)\s*[:：]?\s*[¥￥$]?\s*(\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
DATE = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)")
TRACKING_LABELLED = re.compile(
    r"(?:运单号|快递单号|Tracking\s*(?:No\.?|Number|#)?)\s*[:：]?\s*([A-Za-z]{0,3}\d{8,18})",
    re.IGNORECASE,
)
TRACKING_PREFIXED = re.compile(r"\b(SF|YT|YTO|ZTO|STO|YD|JD|EMS)\d{8,18}\b", re.IGNORECASE)
CARRIERS = {
    "顺丰": "顺丰速运",
    "圆通": "圆通速递",
    "中通": "中通快递",
    "申通": "申通快递",
    "韵达": "韵达快递",
    "京东": "京东物流",
    "邮政": "中国邮政",
    "SF": "顺丰速运",
    "YTO": "圆通速递",
    "YT": "圆通速递",
    "ZTO": "中通快递",
    "STO": "申通快递",
    "YD": "韵达快递",
    "JD": "京东物流",
    "EMS": "中国邮政",
}
INVOICE_FIELDS = ("order_number", "amount", "date")
LOGISTICS_FIELDS = ("tracking_number", "carrier", "date")


class ExtractionError(Exception):
    pass


def _field(name, value, method):
    return {
        "field": name,
        "value": value,
        "extraction_method": method,
        "verification_status": "pending_review",
    }


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ExtractionError("pdf_parse_failed") from exc


def _rules(text: str, wanted, method):
    fields = []
    if "order_number" in wanted:
        match = ORDER_NUMBER.search(text)
        if match:
            fields.append(_field("order_number", match[1], method))
    if "amount" in wanted:
        match = AMOUNT.search(text)
        if match:
            fields.append(_field("amount", match[1], method))
    if "tracking_number" in wanted:
        match = TRACKING_LABELLED.search(text)
        if match:
            fields.append(_field("tracking_number", match[1], method))
        else:
            match = TRACKING_PREFIXED.search(text)
            if match:
                fields.append(_field("tracking_number", match[0], method))
    if "carrier" in wanted:
        for cue, name in CARRIERS.items():
            if cue in text:
                fields.append(_field("carrier", name, method))
                break
    if "date" in wanted:
        match = DATE.search(text)
        if match:
            fields.append(_field("date", match[1], method))
    return fields


def extract(kind: str, path: Path, extension: str):
    """Return (extraction_method, extracted_data). Raises ExtractionError on failure."""
    if extension == ".pdf":
        method = "pdf_text_rules"
        text = _pdf_text(path)
        wanted = {
            "invoice": INVOICE_FIELDS,
            "express_waybill": LOGISTICS_FIELDS,
        }.get(kind, INVOICE_FIELDS + LOGISTICS_FIELDS)
        fields = _rules(text, wanted, method)
        note = None if fields else "未从 PDF 文本中提取到候选字段，需人工核验。"
        return method, {"fields": fields, "notes": note}
    # Images: no OCR / vision in this version; manual review required.
    return "manual_review", {
        "fields": [],
        "notes": "图片凭证本版不做 OCR/视觉提取，请人工核验并记录关键信息。",
    }