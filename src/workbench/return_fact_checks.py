"""Requested order facts are distinct from policy excerpts and missing tool fields."""

import re

PATTERNS = {
    "signed_at": r"只查实际签收|(?:查|核对|查询|核实).{0,8}实际签收|(?:有没有|是否已|什么时候).{0,4}签收",
    "refund_amount": r"实际退款金额|具体退款金额|能退多少钱|精确到分|(?:退|退款).{0,5}多少(?:钱|现金|元)",
    "bank_received": r"银行.{0,10}(?:到账|入账)|(?:是否|有没有|查询|核实).{0,12}(?:到账|入账)",
    "activation_status": r"(?:查|核实).{0,10}激活|激活时间|激活状态|是否已激活|有没有激活",
    "member_entitlement": r"(?:核实|查明|查询).{0,20}(?:15天|权益标识)|有没有15天权益标识",
    "quality_verdict": r"质检.{0,8}(?:结果|判定|结论|判为)|(?:确认|核实).{0,8}合格",
    "coupon_reissue_amount": r"(?:补发|返还).{0,10}(?:多少钱|金额|多少元)",
}
LABELS = {
    "signed_at": "实际签收时间",
    "refund_amount": "实际退款金额",
    "bank_received": "实际到账",
    "activation_status": "实际激活记录",
    "member_entitlement": "订单15天权益标识",
    "quality_verdict": "实际质检结论",
    "coupon_reissue_amount": "具体券补发金额",
}
GAPS = {
    "refund_amount": "当前没有实付优惠分摊与退款结算字段，商品金额参考值不能替代。",
    "bank_received": "当前工具不提供支付渠道或银行的实际到账记录。",
    "activation_status": "当前订单工具不提供设备激活记录。",
    "member_entitlement": "当前订单工具不提供15天权益标识。",
    "quality_verdict": "没有该商品行及数量的有效明确演示质检结论；用户描述或照片不能替代。",
    "coupon_reissue_amount": "当前工具没有该活动的具体补发额度与执行记录。",
}


RETURN_REASONS = {
    "policy_basis_mismatch": "当前退货原因分支与所选政策不一致，请明确选择并重新核对",
    "quality_defect_finding": "尚无独立登记的明确缺陷结论；不能把质检未通过推断为存在质量缺陷",
    "confirmed_defect_requirement_not_met": "当前登记为未确认缺陷，不满足该质量问题演示分支",
    "actual_signed_at": "实际签收时间尚未提供",
    "verified_signed_at": "实际签收时间需核实",
    "item_condition": "商品使用状态尚未确认",
    "applicable_policy_for_sku": "尚无适用该商品的已核对政策",
    "outside_configured_return_window": "超出当前所选退货分支的演示申请窗口",
    "payment_not_paid": "当前付款状态不满足演示规则",
    "configured_unused_condition_not_met": "不满足当前规则的未使用条件",
    "quantity_exceeds_available": "申请数量超过当前可申请数量",
    "activation_status": GAPS["activation_status"],
    "member_entitlement": GAPS["member_entitlement"],
    "quality_verdict": GAPS["quality_verdict"],
    "configured_quality_pass_not_met": "不满足管理员配置的质检通过条件（演示结果）",
}


def return_reasons(codes):
    return "、".join(RETURN_REASONS.get(code, "需要人工核对的业务条件") for code in codes)


def question_requirements(query):
    query = query or ""
    requested = []
    for field, pattern in PATTERNS.items():
        match = re.search(pattern, query)
        if match:
            requested.append(
                {
                    "field": field,
                    "request_quote": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    read_only = bool(
        re.search(
            r"不要(?:帮我)?(?:申请|办理|提交)|不(?:用|要)(?:帮我)?(?:申请|办理|提交)|只(?:想|要|需要)?(?:知道|查|看|问|了解)",
            query,
        )
    )
    return {"requested": requested, "read_only": read_only}


def fact_checks(query, order, *, evidence=None, quality=None):
    plan = question_requirements(query)
    checks = []
    for item in plan["requested"]:
        field = item["field"]
        value = order.get("signed_at") if field == "signed_at" else None
        available = field == "signed_at" and value is not None
        inspection = (quality or {}).get("inspection") or {}
        if field == "quality_verdict" and (quality or {}).get("known"):
            value, available = inspection["verdict"], True
        checks.append(
            {
                **item,
                "label": LABELS[field],
                "status": "available" if available else "unknown",
                "value": value,
                **(
                    {"finding": inspection.get("finding")}
                    if field == "quality_verdict" and available
                    else {}
                ),
                "provenance": {
                    "kind": "tool_field" if available else "missing_fact",
                    "tool": "get_order_status"
                    if field == "signed_at"
                    else ("get_quality_inspection" if available else None),
                    "field": "signed_at"
                    if field == "signed_at"
                    else ("inspection.verdict" if available else None),
                    **(
                        {
                            "inspection_id": inspection["id"],
                            "revision": inspection["revision"],
                            "data_source": "operator_recorded_demo",
                        }
                        if field == "quality_verdict" and available
                        else {}
                    ),
                },
                "message": (
                    {"passed": "演示质检通过", "failed": "演示质检未通过"}.get(
                        str(value), str(value)
                    )
                )
                if available
                else (
                    "实际签收字段缺失，预计到达不作为签收。"
                    if field == "signed_at"
                    else GAPS[field]
                ),
                "policy_snippets_are_not_fact_proof": not available,
            }
        )
    return {
        "read_only": plan["read_only"],
        "items": checks,
        "all_requested_facts_available": all(row["status"] == "available" for row in checks)
        if checks
        else None,
        "related_policy_sources": [row["id"] for row in (evidence or [])],
    }


def render_fact_checks(value):
    if not value.get("items"):
        return ""
    return "\n同时核对：" + "；".join(
        row["label"] + "：" + row["message"] for row in value["items"]
    )
