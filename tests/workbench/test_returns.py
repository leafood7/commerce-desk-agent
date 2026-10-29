"""Business assertions use deliberately explicit DEMO clauses, not legal defaults."""

import os
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.knowledge_models import Document, DocumentVersion, KnowledgeChunk
from workbench.models import Order, OrderItem, identifier, now


@pytest.fixture
def return_case(logged_in):
    client, app, headers = logged_in
    headers()
    # Test-only fixture, never an inference from ETA or untrusted chat text.
    with app.state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == "A1003"))
        order.payment_status, order.shipping_status = "paid", "delivered"
        order.signed_at = now() - timedelta(days=1)
        item = session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
        item.quantity = 3
        # Fixture facts belong to the explicitly selected demo organization.
        from workbench.models import User

        user = session.scalar(
            select(User).where(User.organization_id == order.organization_id, User.role == "admin")
        )
        doc = Document(
            id=identifier(),
            organization_id=order.organization_id,
            title="DEMO return terms",
            created_by=user.id,
        )
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            id=identifier(),
            organization_id=order.organization_id,
            document_id=doc.id,
            number=1,
            filename="demo-returns.md",
            storage_key="test-only",
            content_hash="a" * 64,
            config_hash="b" * 64,
            policy_scope="returns",
            chunk_strategy="structural",
            status="ready",
            chunk_count=1,
            parse_metadata={},
        )
        session.add(version)
        session.flush()
        doc.active_version_id = version.id
        chunk = KnowledgeChunk(
            id="return-" + identifier(),
            organization_id=order.organization_id,
            version_id=version.id,
            position=0,
            content=f"DEMO only: SKU {item.sku}. Window 7 days after actual receipt. Item must be unused.",
            section="returns",
        )
        session.add(chunk)
        session.commit()
        item_id, version_id, chunk_id, sku = item.id, version.id, chunk.id, item.sku
    policy = {
        "version_id": version_id,
        "skus": [sku],
        "window_days": 7,
        "requires_unused": True,
        "scope_clause": {"chunk_id": chunk_id, "quote": "SKU " + sku},
        "window_clause": {"chunk_id": chunk_id, "quote": "Window 7 days after actual receipt."},
        "condition_clause": {"chunk_id": chunk_id, "quote": "Item must be unused."},
        "administrator_verified": True,
    }
    response = client.post("/api/v1/returns/policies", headers=headers("admin"), json=policy)
    assert response.status_code == 201, response.text
    body = {
        "order_number": "A1003",
        "item_id": item_id,
        "policy_id": response.json()["id"],
        "quantity": 1,
        "reason": "DEMO changed mind",
        "item_condition": "unused",
    }
    return client, app, headers, body, policy


def draft(case, key="return-case"):
    client, _, headers, body, _ = case
    response = client.post(
        "/api/v1/returns/requests", headers={**headers(), "Idempotency-Key": key}, json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm(case, row):
    client, _, headers, _, _ = case
    preview = client.get(f"/api/v1/returns/requests/{row['id']}/preview", headers=headers())
    assert preview.status_code == 200, preview.text
    return client.post(
        f"/api/v1/returns/requests/{row['id']}/confirm",
        headers=headers(),
        json={"expected_revision": row["revision"], "preview_hash": preview.json()["preview_hash"]},
    )


def test_return_end_to_end_and_no_refund(return_case):
    client, _, headers, _, _ = return_case
    row = draft(return_case)
    assert confirm(return_case, row).json()["status"] == "pending_approval"
    decision = client.post(
        f"/api/v1/returns/requests/{row['id']}/decision",
        headers=headers("approver"),
        json={"action": "approve", "reason": "reviewed demo evidence", "expected_revision": 1},
    )
    assert decision.status_code == 200, decision.text
    for _ in range(2):
        result = client.post(
            f"/api/v1/returns/requests/{row['id']}/submit-simulated",
            headers=headers(),
            json={"expected_revision": 1},
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "accepted_simulated"
        assert result.json()["actual_refund_executed"] is False
        assert result.json()["refund_amount_cents"] is None
    actions = [e["action"] for e in result.json()["events"]]
    assert actions.count("accepted_simulated") == 1


@pytest.mark.parametrize(
    "condition,field",
    [("unknown", "item_condition"), ("used", "configured_unused_condition_not_met")],
)
def test_return_missing_or_used_cannot_confirm(return_case, condition, field):
    client, _, headers, body, _ = return_case
    body["item_condition"] = condition
    row = draft(return_case)
    result = confirm(return_case, row)
    assert result.status_code == 409
    assert field in str(result.json())


def test_return_eta_does_not_supply_receipt(return_case):
    client, app, headers, body, _ = return_case
    with app.state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == body["order_number"]))
        order.signed_at, order.estimated_delivery = None, "already tomorrow"
        session.commit()
    preview = client.post("/api/v1/returns/evaluate", headers=headers(), json=body)
    assert preview.json()["eligibility"] == "needs_information"
    assert "actual_signed_at" in preview.json()["missing"]


def test_return_rejects_expired_window_and_unknown_sku(return_case):
    client, app, headers, body, _ = return_case
    with app.state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == body["order_number"]))
        order.signed_at = now() - timedelta(days=8)
        item = session.get(OrderItem, body["item_id"])
        item.sku = "CUSTOM-UNKNOWN"
        session.commit()
    preview = client.post("/api/v1/returns/evaluate", headers=headers(), json=body).json()
    assert "outside_configured_return_window" not in preview["blocked"]
    assert "applicable_policy_for_sku" in preview["missing"]


def test_return_correction_invalidates_confirmation(return_case):
    client, _, headers, body, _ = return_case
    row = draft(return_case)
    old = client.get(f"/api/v1/returns/requests/{row['id']}/preview", headers=headers()).json()
    corrected = client.post(
        f"/api/v1/returns/requests/{row['id']}/correct",
        headers=headers(),
        json={"expected_revision": 1, "application": {**body, "quantity": 2}},
    )
    assert corrected.json()["revision"] == 2
    stale = client.post(
        f"/api/v1/returns/requests/{row['id']}/confirm",
        headers=headers(),
        json={"expected_revision": 1, "preview_hash": old["preview_hash"]},
    )
    assert stale.status_code == 409
    assert confirm(return_case, corrected.json()).status_code == 200


def test_return_roles_and_org_scope(return_case):
    client, _, headers, body, policy = return_case
    assert (
        client.get(
            "/api/v1/returns/policy-sources/" + policy["version_id"], headers=headers()
        ).status_code
        == 403
    )
    source = client.get(
        "/api/v1/returns/policy-sources/" + policy["version_id"], headers=headers("admin")
    )
    assert source.status_code == 200 and source.json()["items"]
    assert (
        client.post("/api/v1/returns/policies", headers=headers(), json=policy).status_code == 403
    )
    row = draft(return_case)
    assert (
        client.get(
            f"/api/v1/returns/requests/{row['id']}", headers=headers(organization="demo-b")
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/returns/requests/{row['id']}/decision",
            headers=headers(),
            json={"action": "approve", "reason": "self", "expected_revision": 1},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/returns/requests/{row['id']}/submit-simulated",
            headers=headers(),
            json={"expected_revision": 1},
        ).status_code
        == 409
    )


def test_return_reserves_quantity_and_cancel_releases(return_case):
    client, _, headers, body, _ = return_case
    body["quantity"] = 2
    first, second = draft(return_case, "one"), draft(return_case, "two")
    assert confirm(return_case, first).status_code == 200
    assert confirm(return_case, second).status_code == 409
    assert (
        client.post(
            f"/api/v1/returns/requests/{first['id']}/cancel",
            headers=headers(),
            json={"expected_revision": 1},
        ).status_code
        == 200
    )
    assert confirm(return_case, second).status_code == 200


def test_return_idempotency_and_strict_quantity(return_case):
    client, _, headers, body, _ = return_case
    first, second = draft(return_case), draft(return_case)
    assert first["id"] == second["id"]
    for quantity in (True, "1", 0, 4):
        result = client.post(
            "/api/v1/returns/requests",
            headers={**headers(), "Idempotency-Key": identifier()},
            json={**body, "quantity": quantity},
        )
        assert result.status_code in (409, 422)
    assert (
        client.post(
            "/api/v1/returns/requests",
            headers={**headers(), "Idempotency-Key": "return-case"},
            json={**body, "quantity": 2},
        ).status_code
        == 409
    )


def test_return_withdrawn_or_forged_policy_blocks_writes(return_case):
    client, app, headers, _, policy = return_case
    row = draft(return_case)
    forged = {
        **policy,
        "window_clause": {**policy["window_clause"], "quote": "fictional entitlement"},
    }
    assert (
        client.post("/api/v1/returns/policies", headers=headers("admin"), json=forged).status_code
        == 409
    )
    assert confirm(return_case, row).status_code == 200
    with app.state.database.sessions() as session:
        version = session.get(DocumentVersion, policy["version_id"])
        doc = session.get(Document, version.document_id)
        doc.active_version_id = None
        session.commit()
    assert (
        client.post(
            f"/api/v1/returns/requests/{row['id']}/decision",
            headers=headers("approver"),
            json={"action": "approve", "reason": "review", "expected_revision": 1},
        ).status_code
        == 409
    )
    assert client.get("/api/v1/returns/policies", headers=headers()).json()["items"] == []


def test_return_changed_order_after_approval_blocks_submission(return_case):
    client, app, headers, body, _ = return_case
    row = draft(return_case)
    assert confirm(return_case, row).status_code == 200
    assert (
        client.post(
            f"/api/v1/returns/requests/{row['id']}/decision",
            headers=headers("approver"),
            json={"action": "approve", "reason": "review", "expected_revision": 1},
        ).status_code
        == 200
    )
    with app.state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == body["order_number"]))
        order.payment_status = "refunded"
        session.commit()
    assert (
        client.post(
            f"/api/v1/returns/requests/{row['id']}/submit-simulated",
            headers=headers(),
            json={"expected_revision": 1},
        ).status_code
        == 409
    )


@pytest.mark.skipif(
    not os.environ.get("WORKBENCH_TEST_MYSQL_URL"), reason="Requires MySQL row locking"
)
def test_return_concurrent_confirm_does_not_overreserve(return_case):
    from concurrent.futures import ThreadPoolExecutor

    client, _, headers, body, _ = return_case
    body["quantity"] = 2
    first, second = draft(return_case, "parallel-one"), draft(return_case, "parallel-two")
    inputs = []
    for row in (first, second):
        preview = client.get(
            f"/api/v1/returns/requests/{row['id']}/preview", headers=headers()
        ).json()
        inputs.append(
            (row["id"], {"expected_revision": 1, "preview_hash": preview["preview_hash"]})
        )

    def submit(entry):
        identifier, data = entry
        return client.post(
            f"/api/v1/returns/requests/{identifier}/confirm", headers=headers(), json=data
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, inputs))
    assert sorted(results) == [200, 409]
