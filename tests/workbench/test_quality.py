"""Authored inspection business criteria; no provider calls or external inspection."""

from uuid import uuid4

import pytest
from test_returns import confirm, draft, return_case  # noqa: F401


def create(case, *, key=None, **changes):
    client, _, headers, body, _ = case
    return client.post(
        "/api/v1/quality/inspections",
        headers={**headers(), "Idempotency-Key": key or str(uuid4())},
        json={
            "order_number": body["order_number"],
            "item_id": body["item_id"],
            "quantity": body["quantity"],
            "reason": "演示质检",
            **changes,
        },
    )


def change(case, row, action, *, role="approver", **values):
    return case[0].post(
        f"/api/v1/quality/inspections/{row['id']}/transition",
        headers=case[2](role),
        json={
            "action": action,
            "expected_revision": row["revision"],
            "note": "人工登记演示",
            **values,
        },
    )


def completed(case, verdict="passed"):
    response = create(case)
    assert response.status_code == 201, response.text
    row = response.json()
    response = change(case, row, "start")
    assert response.status_code == 200, response.text
    response = change(case, response.json(), "complete", verdict=verdict)
    assert response.status_code == 200, response.text
    return response.json()


def policy(case, requirement="recorded"):
    client, _, headers, body, original = case
    response = client.post(
        "/api/v1/returns/policies",
        headers=headers("admin"),
        json={
            **original,
            "required_facts": ["quality_verdict"],
            "quality_requirement": requirement,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def lookup(case, **changes):
    client, _, headers, body, _ = case
    return client.post(
        "/api/v1/quality/lookup",
        headers=headers(),
        json={
            "order_number": body["order_number"],
            "item_id": body["item_id"],
            "quantity": body["quantity"],
            **changes,
        },
    )


def test_create_idempotency_binding_and_events(return_case):  # noqa: F811
    case = return_case
    row = create(case, key="same").json()
    assert create(case, key="same").json()["id"] == row["id"]
    assert create(case, key="same", reason="changed").status_code == 409
    assert create(case).status_code == 409
    assert create(case, item_id="unrelated").status_code == 404
    assert change(case, row, "complete", verdict="passed").status_code == 409
    assert change(case, row, "start", role="support").status_code == 403
    started = change(case, row, "start").json()
    assert change(case, row, "start").status_code == 409
    final = change(case, started, "complete", verdict="passed").json()
    assert final["revision"] == 3 and final["verdict"] == "passed"
    assert change(case, final, "complete", verdict="failed").status_code == 409
    detail = case[0].get(f"/api/v1/quality/inspections/{row['id']}", headers=case[2]()).json()
    assert [e["action"] for e in detail["events"]] == ["requested", "start", "complete"]
    assert lookup(case).json()["known"] is True


@pytest.mark.parametrize(
    "quantity,status", [(0, 422), (-1, 422), (True, 422), ("1", 422), (4, 409)]
)
def test_strict_quantity(return_case, quantity, status):  # noqa: F811
    assert create(return_case, quantity=quantity).status_code == status


def test_other_organization_cannot_read_or_transition(return_case):  # noqa: F811
    case = return_case
    row = create(case).json()
    client, _, headers, body, _ = case
    other = headers("admin", "demo-b")
    assert client.get(f"/api/v1/quality/inspections/{row['id']}", headers=other).status_code == 404
    assert (
        client.post(
            "/api/v1/quality/lookup",
            headers=other,
            json={k: body[k] for k in ("order_number", "item_id", "quantity")},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/quality/inspections/{row['id']}/transition",
            headers=other,
            json={"action": "void", "expected_revision": 1, "note": "test"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "verdict,requirement,eligibility",
    [
        ("passed", "passed", "eligible"),
        ("failed", "passed", "blocked"),
        ("failed", "recorded", "eligible"),
        ("inconclusive", "recorded", "needs_information"),
    ],
)
def test_policy_explicit_result_requirement(return_case, verdict, requirement, eligibility):  # noqa: F811
    case = return_case
    completed(case, verdict)
    body = {**case[3], "policy_id": policy(case, requirement)}
    preview = case[0].post("/api/v1/returns/evaluate", headers=case[2](), json=body).json()
    assert preview["eligibility"] == eligibility, preview
    assert preview["quality"]["inspection"]["verdict"] == verdict


def test_latest_quantity_and_void_invalidate_preview(return_case):  # noqa: F811
    case = return_case
    row = completed(case)
    assert lookup(case, quantity=2).json()["inspection"] is None
    case[3]["policy_id"] = policy(case, "passed")
    request = draft(case)
    preview = (
        case[0].get(f"/api/v1/returns/requests/{request['id']}/preview", headers=case[2]()).json()
    )
    assert change(case, row, "void", role="support").status_code == 403
    assert change(case, row, "void", role="admin").status_code == 200
    assert not lookup(case).json()["known"]
    stale = case[0].post(
        f"/api/v1/returns/requests/{request['id']}/confirm",
        headers=case[2](),
        json={"expected_revision": request["revision"], "preview_hash": preview["preview_hash"]},
    )
    assert stale.status_code == 409
    assert confirm(case, request).status_code == 409
    completed(case)
    assert lookup(case).json()["known"]
    newer = create(case).json()
    current = lookup(case).json()
    assert current["inspection"]["id"] == newer["id"] and not current["known"]


@pytest.mark.parametrize("after_approval", [False, True])
def test_void_after_confirmation_blocks_approval_or_submission(return_case, after_approval):  # noqa: F811
    case = return_case
    inspection = completed(case)
    case[3]["policy_id"] = policy(case, "passed")
    row = draft(case)
    response = confirm(case, row)
    assert response.status_code == 200, response.text
    row = response.json()
    if after_approval:
        approved = case[0].post(
            f"/api/v1/returns/requests/{row['id']}/decision",
            headers=case[2]("approver"),
            json={"action": "approve", "reason": "reviewed", "expected_revision": row["revision"]},
        )
        assert approved.status_code == 200, approved.text
        row = approved.json()
    assert change(case, inspection, "void", role="admin").status_code == 200
    if after_approval:
        result = case[0].post(
            f"/api/v1/returns/requests/{row['id']}/submit-simulated",
            headers=case[2](),
            json={"expected_revision": row["revision"]},
        )
    else:
        result = case[0].post(
            f"/api/v1/returns/requests/{row['id']}/decision",
            headers=case[2]("approver"),
            json={"action": "approve", "reason": "reviewed", "expected_revision": row["revision"]},
        )
    assert result.status_code == 409, result.text


def test_quality_clause_is_validated_and_bound_into_policy_evidence(return_case):  # noqa: F811
    case = return_case
    original = case[4]
    payload = {
        **original,
        "required_facts": ["quality_verdict"],
        "quality_requirement": "passed",
        "quality_clause": {**original["condition_clause"], "quote": "forged quality quote"},
    }
    assert (
        case[0].post("/api/v1/returns/policies", headers=case[2]("admin"), json=payload).status_code
        == 409
    )
    payload["quality_clause"] = original["condition_clause"]
    registered = case[0].post("/api/v1/returns/policies", headers=case[2]("admin"), json=payload)
    assert registered.status_code == 201, registered.text
    assert any(c["purpose"] == "quality_clause" for c in registered.json()["evidence"]["clauses"])
