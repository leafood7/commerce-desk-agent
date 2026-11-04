"""Deterministic policy conflict candidate detection.

Rules are compared structurally; no model decides applicability or priority. A
conflict candidate is produced only when the applicability scopes overlap and
the rules yield incompatible outcomes. Priority resolution is allowed only when
administrators have explicitly recorded a priority for both rules.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PolicyRule:
    id: str
    sku: str
    return_basis: str
    sales_channel: str
    effective_from: date
    effective_until: date | None
    window_days: int
    requires_unused: bool
    priority: int | None = None
    policy_id: str | None = None
    version_id: str | None = None
    version_number: int | None = None
    source_refs: tuple[str, ...] = ()


def same_applicability(a: PolicyRule, b: PolicyRule) -> bool:
    """Return True only when the two rules can apply to the same case."""
    if a.sku != b.sku:
        return False
    if a.return_basis != b.return_basis:
        return False
    if a.sales_channel != b.sales_channel:
        return False
    a_end = a.effective_until or date.max
    b_end = b.effective_until or date.max
    return a.effective_from <= b_end and b.effective_from <= a_end


def detect_conflict(a: PolicyRule, b: PolicyRule) -> dict:
    if not same_applicability(a, b):
        return {"status": "NO_CONFLICT", "reasons": []}

    differences = []
    if a.window_days != b.window_days:
        differences.append("RETURN_WINDOW_DIFFERS")
    if a.requires_unused != b.requires_unused:
        differences.append("ITEM_CONDITION_DIFFERS")
    if not differences:
        return {"status": "NO_CONFLICT", "reasons": []}

    # A newer version of the same policy replaces the older one; this is a
    # normal version transition, not a policy conflict.
    if a.policy_id and a.policy_id == b.policy_id and a.version_id != b.version_id:
        newer = a if (a.version_number or 0) >= (b.version_number or 0) else b
        return {
            "status": "SUPERSEDED_BY_VERSION",
            "preferred_rule_id": newer.id,
            "reasons": differences,
        }

    # Priority must come from an administrator-confirmed rule mapping. Two
    # unknown priorities are never treated as an implicit ordering.
    if a.priority is not None and b.priority is not None and a.priority != b.priority:
        return {
            "status": "RESOLVED_BY_PRIORITY",
            "preferred_rule_id": a.id if a.priority > b.priority else b.id,
            "reasons": differences,
        }

    return {
        "status": "POTENTIAL_CONFLICT",
        "rule_ids": [a.id, b.id],
        "reasons": differences,
    }