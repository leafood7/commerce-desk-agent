"""Facts and legal next steps; LLM never constructs tool arguments or eligibility."""

from workbench.domain import fingerprint


def allowed_actions(state, *, scoped=False):
    read, searched, checked = (
        bool(state.get(key)) for key in ("order_read", "policy_searched", "return_checked")
    )
    if not read:
        return ["read_order"] if searched or scoped else ["read_order", "search_policy"]
    if not checked:
        return ["check_return"] if searched or scoped else ["search_policy", "check_return"]
    if not searched:
        return ["search_policy"]
    preview = state["preview"]
    required = {s["id"] for s in preview["evidence"]["sources"]}
    complete = required <= set(state.get("retrieved_ids", []))
    if not complete:
        return ["stop"] if state.get("supplemented") else ["supplement"]
    if preview["blocked"]:
        return ["stop"]
    if preview["missing"] == ["item_condition"]:
        return ["ask_condition"]
    return ["stop"] if preview["missing"] else ["review"]


def decision_observation(state, allowed):
    preview = state.get("preview") or {}
    evidence = (
        (state.get("policy_context") or [])
        if state.get("context_bound")
        else (state.get("evidence") or [])
    )
    selected = []
    remaining = 1200
    for item in evidence[:6]:
        excerpt = item["content"][: min(500, remaining)]
        if not excerpt:
            break
        remaining -= len(excerpt)
        selected.append(
            {
                "source_id": item["id"],
                "version_id": item["version_id"],
                "text": excerpt,
                "clipped": len(excerpt) < len(item["content"]),
            }
        )
    order = state.get("order") or {}
    return {
        "allowed_actions": allowed,
        "task": str(state.get("query", ""))[:360],
        "latest_conditions": {
            "return_basis": state.get("return_basis") or "no_reason",
            "item_condition": state.get("return_condition") or "unknown",
            "quantity": state.get("return_quantity"),
            "reason": str(state.get("reason", ""))[:120],
        },
        "observation_flags": {
            key: bool(state.get(key))
            for key in ("order_read", "policy_searched", "return_checked", "supplemented")
        },
        "order_facts": {
            key: order.get(key) for key in ("payment_status", "shipping_status", "signed_at")
        },
        "server_check": {key: preview.get(key) for key in ("eligibility", "missing", "blocked")},
        "evidence": selected,
        "evidence_fingerprint": fingerprint(
            [{k: e.get(k) for k in ("id", "version_id", "content")} for e in evidence]
        ),
        "correction_count": len(state.get("corrections", [])),
        "binding_fingerprint": fingerprint(state.get("return_policy_id")),
    }
