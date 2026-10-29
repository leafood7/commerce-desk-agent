"""Real worker/MCP/index return flow; DEMO merchant terms, no live model/refund."""

import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
import test_workbench_dispatch_docker as dispatch
import test_workbench_docker_flows as flows

pytestmark = pytest.mark.docker
container_api = flows.container_api


def test_real_return_worker_retrieval_approval_and_simulation(container_api):
    client, headers = container_api
    fixture = json.loads(Path("work/returns-v1/demo.json").read_text(encoding="utf-8"))
    target = fixture["orders"]["normal"]
    support, approver = headers("support"), headers("approver")
    created = client.post(
        "/api/v1/runs",
        headers={**support, "Idempotency-Key": str(uuid4())},
        json={
            "kind": "aftersales",
            "query": "演示未使用商品退货，实际签收7天内的申请规则是什么？",
            "order_number": target["number"],
            "return_item_id": target["item_id"],
            "return_policy_id": fixture["policy_id"],
            "return_quantity": 1,
            "return_condition": "unknown",
            "reason": "真实容器退货链路验收",
        },
    )
    created.raise_for_status()
    run = flows.wait_run(client, support, created.json()["id"])
    assert run["status"] == "awaiting_input", run

    def resume(action, **fields):
        response = client.post(
            f"/api/v1/runs/{run['id']}/resume",
            headers={**support, "Idempotency-Key": str(uuid4())},
            json={"action": action, "pause_id": run["output"].get("pause_id"), **fields},
        )
        response.raise_for_status()
        return flows.wait_run(client, support, run["id"])

    run = resume("supply", return_condition="unused")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["evidence"]
    run = resume("confirm")
    assert run["status"] == "awaiting_approval", run
    application = run["output"]["request"]
    # Actual process restart while paused, preserving the original run/application.
    dispatch.compose("restart", "api")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if client.get("/ready").status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        raise AssertionError("API did not recover after return pause restart")
    persisted = client.get(f"/api/v1/runs/{run['id']}", headers=support).json()
    assert persisted["status"] == "awaiting_approval"
    assert persisted["output"]["request"]["id"] == application["id"]
    denied = client.post(
        f"/api/v1/returns/requests/{application['id']}/decision",
        headers=support,
        json={"expected_revision": 1, "action": "approve", "reason": "should fail"},
    )
    assert denied.status_code == 403
    assert (
        client.get(
            f"/api/v1/returns/requests/{application['id']}", headers=headers("support", "demo-b")
        ).status_code
        == 404
    )
    decision = client.post(
        f"/api/v1/returns/requests/{application['id']}/decision",
        headers=approver,
        json={"expected_revision": 1, "action": "approve", "reason": "独立审批演示版本"},
    )
    decision.raise_for_status()
    run = resume("refresh")
    assert run["status"] == "awaiting_confirmation", run
    run = resume("confirm")
    assert run["status"] == "completed", run
    assert run["output"]["request"]["status"] == "accepted_simulated"
    assert run["output"]["request"]["actual_refund_executed"] is False
    state = client.get(f"/api/v1/runs/{run['id']}/business-status", headers=support).json()
    assert state["request"]["id"] == application["id"]
    assert state["request"]["refund_amount_cents"] is None
    Path("work/returns-v1/live-flow.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_real_return_missing_receipt_and_expired_window(container_api):
    client, headers = container_api
    fixture = json.loads(Path("work/returns-v1/demo.json").read_text(encoding="utf-8"))
    for label, expected in (
        ("missing_receipt", "actual_signed_at"),
        ("expired", "outside_configured_return_window"),
    ):
        target = fixture["orders"][label]
        response = client.post(
            "/api/v1/returns/evaluate",
            headers=headers("support"),
            json={
                "order_number": target["number"],
                "item_id": target["item_id"],
                "policy_id": fixture["policy_id"],
                "quantity": 1,
                "reason": "边界验收",
                "item_condition": "unused",
            },
        )
        response.raise_for_status()
        preview = response.json()
        assert expected in preview["missing"] + preview["blocked"]
        assert preview["eligibility"] != "eligible"
