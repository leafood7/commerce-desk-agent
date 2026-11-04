# ruff: noqa: F811
"""8B: any case can be located from trace, business id or failed request."""

from test_returns import confirm, draft, return_case  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"t" * 16


def test_case_trail_links_business_actions(return_case):
    client, app, headers, body, policy = return_case
    request = draft(return_case)
    assert confirm(return_case, request).json()["status"] == "pending_approval"
    upload = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", PNG, "image/png")},
        data={"kind": "product_photo", "return_request_id": request["id"]},
        headers=headers("support", "demo-a"),
    )
    assert upload.status_code == 201, upload.text
    generated = client.post(
        "/api/v1/reply-drafts",
        headers=headers("support", "demo-a"),
        json={"return_request_id": request["id"]},
    )
    assert generated.status_code == 201, generated.text

    trail = client.get(
        f"/api/v1/cases/{request['id']}/trail", headers=headers("support", "demo-a")
    )
    assert trail.status_code == 200, trail.text
    trail_body = trail.json()
    assert trail_body["traces"]
    assert trail_body["spans"]
    assert trail_body["audits"]
    assert trail_body["business"]["return_request"]["status"] == "pending_approval"
    assert len(trail_body["business"]["evidence"]) == 1
    assert len(trail_body["business"]["drafts"]) == 1
    assert "storage_key" not in trail.text

    by_attachment = client.get(
        f"/api/v1/cases/{upload.json()['id']}/trail", headers=headers("support", "demo-a")
    ).json()
    assert by_attachment["business"]["return_request"]["id"] == request["id"]
    assert by_attachment["traces"][0]["resource_kind"] == "attachment"

    filtered = client.get(
        f"/api/v1/traces?resource_id={request['id']}", headers=headers("support", "demo-a")
    ).json()
    assert filtered["items"]
    assert filtered["items"][0]["resource_id"] == request["id"]


def test_conflict_trail_links_adjudication(return_case):
    client, app, headers, body, policy = return_case
    checked = client.post(
        "/api/v1/policies/conflicts/check",
        headers=headers("admin", "demo-a"),
        json={
            "rules": [
                {
                    "policy_id": "POLICY-A",
                    "sku": "SKU-TRAIL",
                    "return_basis": "no_reason",
                    "sales_channel": "official_store",
                    "effective_from": "2026-10-01",
                    "effective_until": "2026-12-31",
                    "window_days": 7,
                    "requires_unused": True,
                },
                {
                    "policy_id": "POLICY-B",
                    "sku": "SKU-TRAIL",
                    "return_basis": "no_reason",
                    "sales_channel": "official_store",
                    "effective_from": "2026-10-01",
                    "effective_until": "2026-12-31",
                    "window_days": 3,
                    "requires_unused": True,
                },
            ]
        },
    )
    assert checked.status_code == 200, checked.text
    conflict = checked.json()["conflicts"][0]
    resolved = client.post(
        f"/api/v1/policies/conflicts/{conflict['id']}/resolve",
        headers=headers("approver", "demo-a"),
        json={
            "decision": "preferred_rule",
            "preferred_rule_id": conflict["rule_ids"][1],
            "note": "促销例外优先",
        },
    )
    assert resolved.status_code == 200

    trail = client.get(
        f"/api/v1/cases/{conflict['id']}/trail", headers=headers("admin", "demo-a")
    ).json()
    assert trail["business"]["policy_conflict"]["status"] == "resolved"
    assert trail["business"]["reviews"][0]["preferred_rule_id"] == conflict["rule_ids"][1]
    assert any(row["action"] == "policy.conflict_detected" for row in trail["audits"])


def test_run_trail_contains_events_and_spans(logged_in):
    client, app, headers = logged_in
    response = client.post("/api/v1/runs", headers=headers(), json={"order_number": "A1001"})
    assert response.status_code in {200, 201, 202}, response.text
    run = response.json()
    trail = client.get(
        f"/api/v1/cases/{run['id']}/trail", headers=headers()
    ).json()
    assert trail["runs"] and trail["runs"][0]["id"] == run["id"]
    assert trail["runs"][0]["events"]
    assert trail["spans"]
    assert any(span["service"] == "agent" for span in trail["spans"])


def test_failed_request_is_locatable_by_trace_id(logged_in):
    client, app, headers = logged_in
    response = client.get("/api/v1/orders/not-a-real-order", headers=headers())
    assert response.status_code == 404
    trace_id = response.headers["X-Trace-ID"]
    trail = client.get(f"/api/v1/cases/{trace_id}/trail", headers=headers()).json()
    assert trail["traces"]
    assert any(span["status"] == "failed" for span in trail["spans"])
    assert "not-a-real-order" not in str(trail)


def test_cross_organization_trail_is_empty(return_case):
    client, app, headers, body, policy = return_case
    request = draft(return_case)
    trail = client.get(
        f"/api/v1/cases/{request['id']}/trail", headers=headers("support", "demo-b")
    ).json()
    assert trail["traces"] == []
    assert trail["business"] == {}