"""Literal, source-grounded condition candidates. No candidate is a confirmed fact."""

import re

from workbench.domain import fingerprint

CORRECTION = re.compile(r"更正[：:]?|刚才说错了[：:]?|改口[：:]?|现在补充[：:]?")
UNUSED = re.compile(r"从未(?:使|试)?用(?:过)?|没有使用过|未使用(?:过)?|没(?:有)?(?:使|试)?用过")
USED = re.compile(r"已经使用(?:过)?|使用过|试用过|用过")


def condition_candidate(text):
    text = text or ""
    changes = list(CORRECTION.finditer(text))
    offset = changes[-1].end() if changes else 0
    current = text[offset:]
    label, candidate, span = "insufficient", None, None
    if re.search(r"忽略|告诉系统|假装|直接.{0,8}认定", current):
        label = "instruction_not_fact"
    elif re.search(r"如果|假设|要是|举例|例如", current):
        label = "hypothetical"
    elif re.search(r"不确定|记不清|可能|好像|应该|不清楚", current):
        label = "uncertain"
    elif re.search(r"(?:不是|并非|不能说|不代表|不等于).{0,4}(?:没|未)", current):
        label = "complex_negation"
    elif re.search(r"客服说|商家说|别人说|助手说|他说|她说", current):
        label = "unverified_report"
    else:
        unused = list(UNUSED.finditer(current))
        used = [
            m
            for m in USED.finditer(current)
            if not any(m.start() < n.end() and m.end() > n.start() for n in unused)
        ]
        if unused and used:
            label = "conflicting"
        elif unused or used:
            candidate = "unused" if unused else "used"
            found = (unused or used)[0]
            span = {
                "start": offset + found.start(),
                "end": offset + found.end(),
                "unit": "unicode_codepoint",
                "quote": found.group(),
            }
            label = "latest_correction" if changes else "explicit_" + candidate
    result = {
        "candidate": candidate,
        "label": label,
        "source": span,
        "description_sha256": fingerprint(text),
        "scope_start": offset,
        "requires_confirmation": candidate is not None,
    }
    result["proposal_hash"] = fingerprint(result)
    return result


def description_digest(text):
    return fingerprint(text or "")
