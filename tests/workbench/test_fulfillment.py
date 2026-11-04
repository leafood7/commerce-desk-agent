# ruff: noqa: F811
"""Post-approval fulfillment: state machine, idempotency and simulated refund invariants."""

from sqlalchemy import select
from test_returns import confirm, draft, return_case  # noqa: F401

from workbench.fulfillment_models import RefundRecord


def approve(return_case, key="fulfillment-case"):
    client, _, headers, _, _ = return_case
    row = draft(return_case, key)
    confirm(return_case, row)
    decision = client.post(
        f"/api/v1/returns/requests/{row['id']}/decision",
        headers=headers("approver"),
        json={"action": "approve", "reason": "demo evidence", "expected_revision": 1},
    )
    assert decision.status_code == 200, decision.text
    listing = client.get("/api/v1/fulfillments", headers=headers("admin"))
    assert listing.status_code == 200, listing.text
    matched = [f for f in listing.json()["items"] if f["return_request_id"] == row["id"]]
    assert len(matched) == 1, "approval must create exactly one fulfillment"
    return client, headers, matched[0]


def track(client, headers, fid):
    response = client.post(
        f"/api/v1/fulfillments/{fid}/tracking",
        headers=headers(),
        json={"fulfillment_id": fid, "carrier": "SF Express", "tracking_number": "SF123456789"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def logistics(client, headers, fid):
    return client.post(f"/api/v1/fulfillments/{fid}/logistics/advance", headers=headers("admin"))


def receive(client, headers, fid):
    response = client.post(
        f"/api/v1/fulfillments/{fid}/warehouse/receive",
        headers=headers("admin"),
        json={"fulfillment_id": fid},
    )
    assert response.status_code == 200, response.text
    return response.json()


def inspect(client, headers, fid, result):
    return client.post(
        f"/api/v1/fulfillments/{fid}/warehouse/inspect",
        headers=headers("admin"),
        json={"fulfillment_id": fid, "result": result, "remark": ""},
    )


def shipped(return_case):
    client, headers, ful = approve(return_case)
    fid = ful["id"]
    assert track(client, headers, fid)["status"] == "SHIPPED"
    assert logistics(client, headers, fid).json()["status"] == "IN_TRANSIT"
    assert logistics(client, headers, fid).json()["status"] == "DELIVERED"
    return client, headers, fid


def passed(return_case):
    client, headers, fid = shipped(return_case)
    receive(client, headers, fid)
    assert inspect(client, headers, fid, "passed").json()["status"] == "INSPECTION_PASSED"
    return client, headers, fid


def refund(client, headers, fid, key, **extra):
    return client.post(
        f"/api/v1/fulfillments/{fid}/refund/initiate",
        headers=headers("admin"),
        json={"fulfillment_id": fid, "idempotency_key": key, **extra},
    )


def test_fulfillment_created_once_on_approval(return_case):
    client, headers, ful = approve(return_case)
    assert ful["status"] == "WAITING_SHIPMENT"
    assert ful["actual_refund_executed"] is False
    assert [e["event_type"] for e in ful["timeline"]].count("RETURN_CREATED") == 1
    listing = client.get("/api/v1/fulfillments", headers=headers("admin")).json()["items"]
    assert sum(1 for f in listing if f["return_request_id"] == ful["return_request_id"]) == 1


def test_tracking_and_logistics_progress(return_case):
    client, headers, ful = approve(return_case)
    fid = ful["id"]
    assert track(client, headers, fid)["status"] == "SHIPPED"
    assert logistics(client, headers, fid).json()["status"] == "IN_TRANSIT"
    delivered = logistics(client, headers, fid).json()
    assert delivered["status"] == "DELIVERED"
    logistics_events = [e for e in delivered["timeline"] if e["source"] == "logistics"]
    assert [e["event_type"] for e in logistics_events] == ["PACKAGE_IN_TRANSIT", "PACKAGE_DELIVERED"]


def test_delivered_without_receipt_cannot_inspect(return_case):
    client, headers, fid = shipped(return_case)
    assert inspect(client, headers, fid, "passed").status_code == 409


def test_inspection_failed_blocks_refund(return_case):
    client, headers, fid = shipped(return_case)
    receive(client, headers, fid)
    assert inspect(client, headers, fid, "failed").json()["status"] == "INSPECTION_FAILED"
    assert refund(client, headers, fid, "k-failed").status_code == 409


def test_refund_timeout_reconcile_without_blind_retry(return_case):
    client, headers, fid = passed(return_case)
    initiated = refund(client, headers, fid, "k-timeout", timeout=True)
    assert initiated.status_code == 200, initiated.text
    assert initiated.json()["status"] == "REFUND_UNKNOWN"
    assert initiated.json()["refund"]["refund_status"] == "UNKNOWN"
    assert refund(client, headers, fid, "k-timeout-2").status_code == 409, (
        "blind retry must be refused while the result is unknown"
    )
    reconciled = client.post(f"/api/v1/fulfillments/{fid}/refund/reconcile", headers=headers("admin"))
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "REFUNDED"
    assert reconciled.json()["refund"]["refund_status"] == "SUCCEEDED"


def test_refund_idempotent_no_duplicate(return_case):
    client, app, _, _, _ = return_case
    client, headers, fid = passed(return_case)
    first = refund(client, headers, fid, "k-once")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "REFUNDED"
    refund_no = first.json()["refund"]["refund_no"]
    replay = refund(client, headers, fid, "k-once")
    assert replay.status_code == 200, replay.text
    assert replay.json()["refund"]["refund_no"] == refund_no
    assert refund(client, headers, fid, "k-once-2").status_code == 409
    with app.state.database.sessions() as session:
        rows = session.scalars(select(RefundRecord).where(RefundRecord.fulfillment_id == fid)).all()
    assert len(rows) == 1, "an idempotent replay must not create a second refund record"


def test_fulfillment_cross_organization_isolated(return_case):
    client, headers, fid = passed(return_case)
    other = client.get(f"/api/v1/fulfillments/{fid}", headers=headers("admin", organization="demo-b"))
    assert other.status_code == 404