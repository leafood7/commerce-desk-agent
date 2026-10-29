"""Opt-in real container acceptance; no model-provider calls."""

import os
import time
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.docker


@pytest.fixture
def container_api():
    password = os.environ["WORKBENCH_TEST_PASSWORD"]
    with httpx.Client(
        base_url=os.getenv("WORKBENCH_TEST_URL", "http://127.0.0.1:18082"),
        timeout=90,
        trust_env=False,
    ) as client:

        def headers(role="support", organization="demo-a"):
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "organization": organization,
                    "email": role + "@demo.test",
                    "password": password,
                },
            )
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        yield client, headers


def wait_run(client, auth, identifier):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        response = client.get("/api/v1/runs/" + identifier, headers=auth)
        response.raise_for_status()
        run = response.json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.25)
    raise AssertionError("Container run did not reach a public pause/terminal state")


def test_container_upload_publish_retrieve_isolation_and_withdraw(container_api):
    client, headers = container_api
    admin, support = headers("admin"), headers()
    title = "container-acceptance-" + uuid4().hex[:10]
    content = (
        "运费券只能抵扣配送运费，不能抵扣商品金额。\n\n退款后运费券不当然返还，需要核对该券规则。"
    ).encode()
    response = client.post(
        "/api/v1/knowledge/documents",
        headers=admin,
        data={"title": title, "policy_scope": "freight_coupon"},
        files={"file": (title + ".txt", content, "text/plain")},
    )
    response.raise_for_status()
    uploaded = response.json()
    version, document = uploaded["version"]["id"], uploaded["document_id"]
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            row = client.get("/api/v1/knowledge/versions/" + version, headers=admin).json()
            if row["status"] == "ready":
                break
            assert row["status"] != "failed", row.get("error_code")
            time.sleep(0.5)
        else:
            raise AssertionError("Container worker did not finish real indexing")
        query = {
            "query": "运费券可以抵扣商品金额吗？",
            "policy_scope": "freight_coupon",
            "strategy": "hybrid_rerank",
        }
        unpublished = client.post("/api/v1/knowledge/search", headers=support, json=query)
        unpublished.raise_for_status()
        assert not any(item["version_id"] == version for item in unpublished.json()["matches"])
        client.post(
            "/api/v1/knowledge/versions/" + version + "/publish", headers=admin, json={}
        ).raise_for_status()
        result = client.post("/api/v1/knowledge/search", headers=support, json=query)
        result.raise_for_status()
        matches = [item for item in result.json()["matches"] if item["version_id"] == version]
        assert matches and "不能抵扣商品金额" in matches[0]["content"]
        original = client.get("/api/v1/knowledge/versions/" + version + "/file", headers=support)
        assert original.content == content
        foreign = headers("support", "demo-b")
        assert (
            client.get(
                "/api/v1/knowledge/versions/" + version + "/file", headers=foreign
            ).status_code
            == 404
        )
        client.post(
            "/api/v1/knowledge/documents/" + document + "/withdraw", headers=admin, json={}
        ).raise_for_status()
        removed = client.post("/api/v1/knowledge/search", headers=support, json=query)
        removed.raise_for_status()
        assert not any(item["version_id"] == version for item in removed.json()["matches"])
    finally:
        client.post("/api/v1/knowledge/documents/" + document + "/withdraw", headers=admin, json={})


def test_container_aftersales_confirmation_independent_approval_and_receipt(container_api):
    client, headers = container_api
    support = headers()
    order = os.environ["WORKBENCH_DOCKER_ORDER_NUMBER"]
    response = client.post(
        "/api/v1/runs",
        headers=support,
        json={
            "kind": "aftersales",
            "query": "请帮我加急",
            "order_number": order,
            "shipping_kind": "expedite",
            "reason": "容器实机验收",
        },
    )
    response.raise_for_status()
    run = wait_run(client, support, response.json()["id"])
    assert run["status"] == "awaiting_confirmation", run.get("error_code")
    assert not run["output"].get("request")
    response = client.post(
        "/api/v1/runs/" + run["id"] + "/resume",
        headers={**support, "Idempotency-Key": uuid4().hex},
        json={"action": "confirm", "pause_id": run["output"]["pause_id"]},
    )
    response.raise_for_status()
    run = wait_run(client, support, run["id"])
    assert run["status"] == "awaiting_approval", run.get("error_code")
    receipt = run["output"]["request"]
    assert receipt["ticket_id"] is None
    path = "/api/v1/shipping/requests/" + receipt["id"] + "/decision"
    assert client.post(path, headers=support, json={"action": "approve"}).status_code == 403
    client.post(path, headers=headers("approver"), json={"action": "approve"}).raise_for_status()
    response = client.post(
        "/api/v1/runs/" + run["id"] + "/resume",
        headers={**support, "Idempotency-Key": uuid4().hex},
        json={"action": "refresh", "pause_id": run["output"]["pause_id"]},
    )
    response.raise_for_status()
    run = wait_run(client, support, run["id"])
    assert run["status"] == "completed"
    assert run["output"]["request"]["status"] == "completed"
    assert run["output"]["request"]["ticket_id"]
    assert run["output"]["request"]["external_warehouse_notified"] is False
