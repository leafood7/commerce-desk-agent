"""Real scoped retrieval and observation loop; live runtime model stays disabled."""

import json
from pathlib import Path

import pytest
import test_workbench_docker_flows as flows

pytestmark = pytest.mark.docker
container_api = flows.container_api


def test_real_scoped_return_observations_and_safe_profile_restore(container_api):
    client, headers = container_api
    fixture = json.loads(Path("work/returns-v1/demo.json").read_text(encoding="utf-8"))
    support, admin = headers("support"), headers("admin")
    original = client.get("/api/v1/runtime/config", headers=admin).json()
    assert original["return_model_available"] is False
    denied = client.post(
        "/api/v1/runtime/config/versions",
        headers=admin,
        json={"return_decisions": "model", "model_alias": "server_default"},
    )
    assert denied.status_code == 422
    version = client.post(
        "/api/v1/runtime/config/versions",
        headers=admin,
        json={"return_retrieval_mode": "policy_scoped"},
    )
    version.raise_for_status()
    owned = version.json()["id"]
    client.post(
        f"/api/v1/runtime/config/versions/{owned}/publish",
        headers=admin,
        json={"expected_revision": original["revision"]},
    ).raise_for_status()
    try:
        order = fixture["orders"]["normal"]
        created = client.post(
            "/api/v1/runs",
            headers=support,
            json={
                "kind": "aftersales",
                "query": "核对未使用商品退货，实际签收7天内的适用政策",
                "order_number": order["number"],
                "return_item_id": order["item_id"],
                "return_quantity": 1,
                "return_condition": "unused",
                "return_policy_id": fixture["policy_id"],
                "reason": "真实范围收窄链路验收",
            },
        )
        created.raise_for_status()
        run = flows.wait_run(client, support, created.json()["id"])
        assert run["status"] == "awaiting_confirmation", run
        assert [r["action"] for r in run["output"]["decisions"]] == [
            "read_order",
            "check_return",
            "search_policy",
            "review",
        ]
        assert run["output"]["retrieval"]["version_ids"] == [fixture["version_id"]]
        assert run["output"]["retrieval"]["complete"] is True
        usage = client.get(f"/api/v1/runs/{run['id']}/usage", headers=support).json()
        assert usage["model_attempts"] == 0
        client.post(f"/api/v1/runs/{run['id']}/cancel", headers=support, json={}).raise_for_status()
    finally:
        current = client.get("/api/v1/runtime/config", headers=admin).json()
        if current["current"]["id"] == owned:
            previous = original["current"]["id"]
            if not previous:
                values = {
                    k: v
                    for k, v in original["current"]["values"].items()
                    if k not in {"prompt_sha256", "return_prompt_sha256"}
                }
                response = client.post(
                    "/api/v1/runtime/config/versions", headers=admin, json=values
                )
                response.raise_for_status()
                previous = response.json()["id"]
            client.post(
                f"/api/v1/runtime/config/versions/{previous}/publish",
                headers=admin,
                json={"expected_revision": current["revision"]},
            ).raise_for_status()
