# ruff: noqa: F811
import asyncio

import pytest
from pydantic import ValidationError
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import grant, scoped_mcp  # noqa: F401


@pytest.fixture
def client_fixture(logged_in):
    client, _, headers = logged_in
    return client, headers


def rule(policy_id="POLICY-A", **overrides):
    body = {
        "policy_id": policy_id,
        "sku": "SKU1001",
        "return_basis": "no_reason",
        "sales_channel": "official_store",
        "effective_from": "2026-10-01",
        "effective_until": "2026-12-31",
        "window_days": 7,
        "requires_unused": True,
    }
    body.update(overrides)
    return body


def check(client, headers, rules, **kwargs):
    return client.post(
        "/api/v1/policies/conflicts/check",
        headers=headers("admin", "demo-a"),
        json={"rules": rules, **kwargs},
    )


def test_same_scope_differing_window_is_conflict(client_fixture):
    client, headers = client_fixture
    response = check(
        client,
        headers,
        [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3)],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pairs"][0]["status"] == "POTENTIAL_CONFLICT"
    assert body["pairs"][0]["reasons"] == ["RETURN_WINDOW_DIFFERS"]
    assert len(body["conflicts"]) == 1
    assert body["blocks_automatic_decision"] is True
    assert body["conflicts"][0]["status"] == "pending_review"


def test_different_sku_or_channel_not_conflict(client_fixture):
    client, headers = client_fixture
    different_channel = check(
        client,
        headers,
        [
            rule("POLICY-A", window_days=7),
            rule("POLICY-B", window_days=3, sales_channel="third_party"),
        ],
    )
    assert different_channel.status_code == 200
    assert different_channel.json()["pairs"][0]["status"] == "NO_CONFLICT"
    assert different_channel.json()["conflicts"] == []

    different_sku = check(
        client,
        headers,
        [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3, sku="SKU2002")],
    )
    assert different_sku.status_code == 200
    assert different_sku.json()["pairs"][0]["status"] == "NO_CONFLICT"
    assert different_sku.json()["conflicts"] == []


def test_non_overlapping_versions_not_conflict(client_fixture):
    client, headers = client_fixture
    response = check(
        client,
        headers,
        [
            rule(
                "POLICY-A",
                version_id="v1",
                effective_from="2026-07-01",
                effective_until="2026-09-30",
                window_days=7,
            ),
            rule("POLICY-A", version_id="v2", effective_from="2026-10-01", window_days=3),
        ],
    )
    assert response.status_code == 200
    assert response.json()["pairs"][0]["status"] == "NO_CONFLICT"
    assert response.json()["conflicts"] == []


def test_overlapping_same_policy_versions_superseded(client_fixture):
    client, headers = client_fixture
    response = check(
        client,
        headers,
        [
            rule("POLICY-A", version_id="v1", version_number=1, window_days=7),
            rule("POLICY-A", version_id="v2", version_number=2, window_days=3),
        ],
    )
    assert response.status_code == 200
    pair = response.json()["pairs"][0]
    assert pair["status"] == "SUPERSEDED_BY_VERSION"
    assert pair["preferred_rule_id"] == "POLICY-A:v2:SKU1001"
    assert response.json()["conflicts"] == []


def test_confirmed_priority_resolves(client_fixture):
    client, headers = client_fixture
    response = check(
        client,
        headers,
        [
            rule("POLICY-A", window_days=7, priority=10),
            rule("POLICY-B", window_days=3, priority=20),
        ],
    )
    assert response.status_code == 200
    pair = response.json()["pairs"][0]
    assert pair["status"] == "RESOLVED_BY_PRIORITY"
    assert pair["preferred_rule_id"] == "POLICY-B::SKU1001"
    assert response.json()["conflicts"] == []


def test_duplicate_check_does_not_duplicate_pending(client_fixture):
    client, headers = client_fixture
    rules = [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3)]
    assert check(client, headers, rules).json()["conflicts"]
    second = check(client, headers, rules)
    assert second.status_code == 200
    assert second.json()["conflicts"] == []
    listing = client.get("/api/v1/policies/conflicts", headers=headers("support", "demo-a"))
    assert len(listing.json()["items"]) == 1
    assert listing.json()["blocks_automatic_decision"] is True


def test_adjudication_records_actor_time_and_source(client_fixture):
    client, headers = client_fixture
    created = check(
        client,
        headers,
        [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3)],
    ).json()["conflicts"][0]
    denied = client.post(
        f"/api/v1/policies/conflicts/{created['id']}/resolve",
        headers=headers("support", "demo-a"),
        json={"decision": "preferred_rule", "preferred_rule_id": created["rule_ids"][0], "note": "无权"},
    )
    assert denied.status_code == 403

    resolved = client.post(
        f"/api/v1/policies/conflicts/{created['id']}/resolve",
        headers=headers("approver", "demo-a"),
        json={
            "decision": "preferred_rule",
            "preferred_rule_id": created["rule_ids"][1],
            "note": "促销政策为明确例外，按已审核例外适用。",
        },
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolution"]["preferred_rule_id"] == created["rule_ids"][1]
    assert body["resolution"]["reviewed_by"] and body["resolution"]["reviewed_at"]

    detail = client.get(
        f"/api/v1/policies/conflicts/{created['id']}", headers=headers("support", "demo-a")
    )
    assert detail.status_code == 200
    assert detail.json()["reviews"][0]["resolution_note"] == "促销政策为明确例外，按已审核例外适用。"
    assert detail.json()["reviews"][0]["reviewed_by"]

    again = client.post(
        f"/api/v1/policies/conflicts/{created['id']}/resolve",
        headers=headers("approver", "demo-a"),
        json={"decision": "no_conflict", "note": "重复裁决"},
    )
    assert again.status_code == 409


def test_resolution_rejects_foreign_rule_and_missing_note(client_fixture):
    client, headers = client_fixture
    created = check(
        client,
        headers,
        [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3)],
    ).json()["conflicts"][0]
    foreign = client.post(
        f"/api/v1/policies/conflicts/{created['id']}/resolve",
        headers=headers("approver", "demo-a"),
        json={"decision": "preferred_rule", "preferred_rule_id": "POLICY-Z::SKU9", "note": "越界规则"},
    )
    assert foreign.status_code == 422
    blank = client.post(
        f"/api/v1/policies/conflicts/{created['id']}/resolve",
        headers=headers("approver", "demo-a"),
        json={"decision": "no_conflict", "note": ""},
    )
    assert blank.status_code == 422


def test_check_requires_admin(client_fixture):
    client, headers = client_fixture
    response = client.post(
        "/api/v1/policies/conflicts/check",
        headers=headers("support", "demo-a"),
        json={"rules": [rule("POLICY-A"), rule("POLICY-B")]},
    )
    assert response.status_code == 403


def test_cross_organization_isolated(client_fixture):
    client, headers = client_fixture
    created = check(
        client,
        headers,
        [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3)],
    ).json()["conflicts"][0]
    other = client.get("/api/v1/policies/conflicts", headers=headers("support", "demo-b"))
    assert other.json()["items"] == []
    hidden = client.get(
        f"/api/v1/policies/conflicts/{created['id']}", headers=headers("support", "demo-b")
    )
    assert hidden.status_code == 404


def test_version_mapping_skips_undeclared_scope(return_case):
    client, app, headers, _, policy = return_case
    response = client.post(
        "/api/v1/policies/conflicts/check",
        headers=headers("admin", "demo-a"),
        json={"version_ids": [policy["version_id"]]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rules"] == 0
    assert body["conflicts"] == []
    assert body["skipped"]
    assert "sales_channel" in body["skipped"][0]["missing"]


def test_mcp_reports_blocked_decision(scoped_mcp):
    client, app, headers, gateway, _ = scoped_mcp
    check(
        client,
        headers,
        [rule("POLICY-A", window_days=7), rule("POLICY-B", window_days=3)],
    )
    token = grant(client, headers("admin"))
    data = asyncio.run(
        gateway.call("get_policy_conflicts", {"sku": "SKU1001"}, token)
    )
    assert data["status"] == "found"
    assert len(data["conflicts"]) == 1
    assert data["conflicts"][0]["status"] == "pending_review"
    assert data["blocks_automatic_decision"] is True
    assert "rule_ids" in data["conflicts"][0]

    other = asyncio.run(
        gateway.call("get_policy_conflicts", {"sku": "SKU9999"}, token)
    )
    assert other["conflicts"] == []
    assert other["blocks_automatic_decision"] is False

    with pytest.raises(ValidationError):
        asyncio.run(gateway.call("get_policy_conflicts", {"sku": "bad sku!"}, token))