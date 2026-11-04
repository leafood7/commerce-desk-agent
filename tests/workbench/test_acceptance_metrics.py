"""PR-05 frozen acceptance sets.

Small, hand-frozen cases only. Metrics describe these cases; they are not a
generalisation claim and are reported after running, never as pre-set results.
"""

import json
from datetime import date

from test_after_sales_attachments import make_pdf

from workbench.evidence_extraction import extract
from workbench.policy_conflicts import PolicyRule, detect_conflict
from workbench.reply_drafts import DraftContext, generate_reply_draft
from workbench.reply_validation import validate_draft

EXTRACTION_CASES = [
    (
        "invoice",
        "Invoice Order Number: A1003 Amount: 199.00 Date: 2026-10-01",
        {"order_number": "A1003", "amount": "199.00", "date": "2026-10-01"},
    ),
    (
        "express_waybill",
        "Tracking Number: SF1234567890 Carrier SF Express Date: 2026-10-02",
        {"tracking_number": "SF1234567890", "carrier": "顺丰速运", "date": "2026-10-02"},
    ),
    (
        "invoice",
        "Invoice Order Number: X-99 Amount: 88 Date: 2026-09-09",
        {"order_number": "X-99", "amount": "88", "date": "2026-09-09"},
    ),
]


def make_rule(rule_id, **overrides):
    values = {
        "sku": "SKU1001",
        "return_basis": "no_reason",
        "sales_channel": "official_store",
        "effective_from": date(2026, 10, 1),
        "effective_until": date(2026, 12, 31),
        "window_days": 7,
        "requires_unused": True,
        "priority": None,
        "policy_id": rule_id.split(":")[0],
        "version_id": None,
        "version_number": None,
    }
    values.update(overrides)
    return PolicyRule(id=rule_id, **values)


CONFLICT_CASES = [
    (
        "window_differs",
        make_rule("POLICY-A:SKU1001", window_days=7),
        make_rule("POLICY-B:SKU1001", window_days=3),
        "POTENTIAL_CONFLICT",
    ),
    (
        "channel_differs",
        make_rule("POLICY-A:SKU1001"),
        make_rule("POLICY-B:SKU1001", sales_channel="third_party", window_days=3),
        "NO_CONFLICT",
    ),
    (
        "basis_differs",
        make_rule("POLICY-A:SKU1001"),
        make_rule("POLICY-B:SKU1001", return_basis="quality_issue", window_days=3),
        "NO_CONFLICT",
    ),
    (
        "sku_differs",
        make_rule("POLICY-A:SKU1001"),
        make_rule("POLICY-B:SKU2002", sku="SKU2002", window_days=3),
        "NO_CONFLICT",
    ),
    (
        "version_not_overlapping",
        make_rule(
            "POLICY-A:v1:SKU1001",
            version_id="v1",
            effective_from=date(2026, 7, 1),
            effective_until=date(2026, 9, 30),
        ),
        make_rule("POLICY-A:v2:SKU1001", version_id="v2", window_days=3),
        "NO_CONFLICT",
    ),
    (
        "same_policy_overlap",
        make_rule("POLICY-A:v1:SKU1001", version_id="v1", version_number=1),
        make_rule("POLICY-A:v2:SKU1001", version_id="v2", version_number=2, window_days=3),
        "SUPERSEDED_BY_VERSION",
    ),
    (
        "confirmed_priority",
        make_rule("POLICY-A:SKU1001", priority=10),
        make_rule("POLICY-B:SKU1001", priority=20, window_days=3),
        "RESOLVED_BY_PRIORITY",
    ),
    (
        "condition_differs",
        make_rule("POLICY-A:SKU1001"),
        make_rule("POLICY-B:SKU1001", requires_unused=False),
        "POTENTIAL_CONFLICT",
    ),
    (
        "identical",
        make_rule("POLICY-A:SKU1001"),
        make_rule("POLICY-B:SKU1001"),
        "NO_CONFLICT",
    ),
]

VERSION_CASES = {
    "version_not_overlapping": "NO_CONFLICT",
    "same_policy_overlap": "SUPERSEDED_BY_VERSION",
}

DRAFT_CONTEXTS = [
    DraftContext("A1003", "pending_approval", None, []),
    DraftContext("A1003", "approved", None, []),
    DraftContext("A1003", "accepted_simulated", None, []),
    DraftContext("A1003", "accepted_simulated", "SUCCEEDED", []),
    DraftContext("A1003", "accepted_simulated", "UNKNOWN", []),
    DraftContext("A1003", "accepted_simulated", "FAILED", []),
    DraftContext("A1003", "accepted_simulated", "PENDING", []),
    DraftContext("A1003", "accepted_simulated", "PROCESSING", []),
    DraftContext("A1003", "rejected", None, []),
    DraftContext("A1003", "cancelled", None, []),
    DraftContext("A1003", "draft", None, []),
]

UNFOUNDED_DRAFTS = [
    "您的退款已到账。",
    "退款成功，请查收。",
    "我们保证退款。",
    "3天内到账。",
    "退款已完成。",
    "退货已完成。",
]


def test_acceptance_metrics(tmp_path):
    matched = extracted_total = expected_total = 0
    for kind, text, expected in EXTRACTION_CASES:
        path = tmp_path / (kind + ".pdf")
        path.write_bytes(make_pdf(text))
        _, data = extract(kind, path, ".pdf")
        got = {(field["field"], str(field["value"])) for field in data["fields"]}
        wanted = set(expected.items())
        matched += len(got & wanted)
        extracted_total += len(got)
        expected_total += len(wanted)
    precision = matched / extracted_total
    recall = matched / expected_total

    results = {}
    predicted_positive = true_positive = actual_positive = correct = 0
    for name, left, right, expected in CONFLICT_CASES:
        outcome = detect_conflict(left, right)["status"]
        results[name] = outcome
        predicted_positive += outcome == "POTENTIAL_CONFLICT"
        actual_positive += expected == "POTENTIAL_CONFLICT"
        true_positive += outcome == expected == "POTENTIAL_CONFLICT"
        correct += outcome == expected
    conflict_precision = true_positive / predicted_positive
    conflict_recall = true_positive / actual_positive
    false_positives = predicted_positive - true_positive
    accuracy = correct / len(CONFLICT_CASES)

    version_passed = sum(results[name] == expected for name, expected in VERSION_CASES.items())
    version_rate = version_passed / len(VERSION_CASES)

    valid_drafts = sum(
        validate_draft(generate_reply_draft(ctx)["content"], {"actual_refund_status": "not_confirmed"})["valid"]
        for ctx in DRAFT_CONTEXTS
    )
    blocked_drafts = sum(
        not validate_draft(content, {"actual_refund_status": "not_confirmed"})["valid"]
        for content in UNFOUNDED_DRAFTS
    )

    metrics = {
        "evidence_extraction": {
            "precision": precision,
            "recall": recall,
            "matched_fields": matched,
            "predicted_fields": extracted_total,
            "expected_fields": expected_total,
        },
        "policy_conflict": {
            "precision": conflict_precision,
            "recall": conflict_recall,
            "false_positives": false_positives,
            "classification_accuracy": accuracy,
            "pairs": len(CONFLICT_CASES),
        },
        "version_judgement": {
            "rate": version_rate,
            "passed": version_passed,
            "cases": len(VERSION_CASES),
        },
        "reply_factuality": {"valid": valid_drafts, "cases": len(DRAFT_CONTEXTS)},
        "reply_prohibited_block": {"blocked": blocked_drafts, "cases": len(UNFOUNDED_DRAFTS)},
    }
    print("PR05_METRICS " + json.dumps(metrics, ensure_ascii=False))

    assert precision == 1.0 and recall == 1.0
    assert conflict_precision == 1.0 and conflict_recall == 1.0
    assert false_positives == 0 and accuracy == 1.0
    assert version_rate == 1.0
    assert valid_drafts == len(DRAFT_CONTEXTS)
    assert blocked_drafts == len(UNFOUNDED_DRAFTS)