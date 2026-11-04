"""Deterministic fact checks for customer reply drafts.

A draft may only state claims supported by the verified facts snapshot. In this
project the external return is simulated, so "refund succeeded / arrived" is
never a confirmed fact.
"""

import re

# "模拟退款流程已成功完成" is allowed because it is explicitly simulated.
PROHIBITED = (
    (
        re.compile(r"(?<!模拟)(退款成功|退款已完成|已退款|退款已到账|已到账|马上到账|立即到账|全额到账)"),
        "退款成功或到账被描述为已确认",
    ),
    (
        re.compile(r"(?<!模拟)(退货已受理完成|退货已完成|退款已退回)"),
        "退货完成被描述为已确认",
    ),
    (
        re.compile(r"(\d+\s*(?:个)?(?:工作日|天|小时)(?:内|之内)?(?:到账|退款|退回))"),
        "承诺了退款到账时间",
    ),
    (
        re.compile(r"(保证退款|一定(?:会)?退款|承诺退款|肯定退款)"),
        "承诺了必然退款",
    ),
    (
        re.compile(r"(?:退款|货款|款项)[^。；\n]{0,24}(?:将|会|预计)[^。；\n]{0,24}(?:退回|退还|到账)"),
        "承诺了退款流程或到账结果",
    ),
    (
        re.compile(r"按原(?:支付)?(?:方式|路径|路)?退回|原路退回"),
        "承诺了退款退回路径",
    ),
    (
        re.compile(r"(?:到账|退款)(?:时间|日期)[^。；\n]{0,12}(?:以|为)[^。；\n]{0,20}(?:为准|通知)"),
        "承诺了退款到账时间",
    ),
    (
        re.compile(r"(?:审核|处理)(?:完成|通过)后[^。；\n]{0,20}(?:退款|退回|到账)"),
        "承诺了审核后必然退款",
    ),
)


ORDER_REF = re.compile(r"\bA\d{3,12}\b")
STATUS_CLAIMS = (
    ("已通过审核", {"approved", "accepted_simulated"}),
    ("审核通过", {"approved", "accepted_simulated"}),
    ("等待审核", {"pending_approval"}),
    ("正在审核", {"pending_approval"}),
    ("已受理", {"accepted_simulated"}),
    ("受理完成", {"accepted_simulated"}),
    ("未通过审核", {"rejected"}),
    ("已取消", {"cancelled"}),
)
AMOUNT_CLAIM = re.compile(r"(退款金额|退款总额)[^。；\n]{0,12}\d")


def validate_reply_plan(plan, context: dict) -> dict:
    """Source layer: every selected fact id must be an approved backend phrase."""
    allowed = set((context.get("allowed_claims") or {}).keys())
    if not allowed:
        allowed = set(context.get("allowed_fact_ids") or [])
    issues = [
        {"layer": "source", "claim": fact_id, "reason": "引用了未经授权的事实"}
        for fact_id in plan.selected_fact_ids
        if fact_id not in allowed
    ]
    return {"valid": not issues, "issues": issues}


def validate_draft(content: str, facts: dict) -> dict:
    issues = []
    for pattern, reason in PROHIBITED:
        for match in pattern.finditer(content):
            issues.append({"layer": "commitment", "claim": match.group(0), "reason": reason})
    if facts.get("actual_refund_status") != "confirmed":
        # Defensive: the project never confirms the real external refund.
        for phrase in ("退款成功", "已退款"):
            if phrase in content and "模拟" not in content:
                issue = {"layer": "commitment", "claim": phrase, "reason": "实际退款结果尚未确认"}
                if issue not in issues:
                    issues.append(issue)

    expected_order = facts.get("order_number")
    for match in ORDER_REF.finditer(content):
        if expected_order and match.group(0) != expected_order:
            issues.append(
                {
                    "layer": "consistency",
                    "claim": match.group(0),
                    "reason": "引用了与当前订单不一致的订单号",
                }
            )
    status = facts.get("return_status")
    for phrase, allowed in STATUS_CLAIMS:
        if phrase in content and status is not None and status not in allowed:
            issues.append(
                {
                    "layer": "consistency",
                    "claim": phrase,
                    "reason": "声称的退货状态与事实不一致",
                }
            )
    if ("已核验" in content or "核验通过" in content) and not facts.get("verified_evidence"):
        issues.append(
            {"layer": "consistency", "claim": "已核验", "reason": "没有已核验凭证"}
        )
    if ("冲突已解决" in content or "规则已确认" in content) and facts.get(
        "unresolved_conflicts", 0
    ):
        issues.append(
            {
                "layer": "consistency",
                "claim": "冲突已解决",
                "reason": "仍有未裁决的政策冲突",
            }
        )
    amount = AMOUNT_CLAIM.search(content)
    if amount:
        issues.append(
            {"layer": "consistency", "claim": amount.group(0), "reason": "退款金额尚未核实"}
        )
    return {"valid": not issues, "issues": issues}