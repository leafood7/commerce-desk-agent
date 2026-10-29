"""Real container persistence and MCP outage acceptance; retains volumes."""

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work/docker"
DOCKER = r"D:\Docker\app\resources\bin\docker.exe"
COMMAND = [
    DOCKER,
    "--host",
    "npipe:////./pipe/dockerDesktopLinuxEngine",
    "compose",
    "--project-name",
    "commercedesk-acceptance",
    "--env-file",
    str(OUT / "acceptance.env"),
    "-f",
    "compose.yaml",
    "-f",
    "compose.acceptance.yaml",
]


def compose(*args):
    p = subprocess.run(
        COMMAND + list(args),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=360,
    )
    with (OUT / "recovery-compose.log").open("a", encoding="utf-8") as log:
        log.write(p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError("Compose recovery operation failed; see recovery-compose.log")
    return p.stdout


def main():
    values = dict(
        line.split("=", 1)
        for line in (OUT / "acceptance.env").read_text().splitlines()
        if "=" in line
    )
    compose("exec", "-T", "api", "python", "scripts/seed_aftersales_demo.py")
    orders = json.loads(compose("exec", "-T", "api", "cat", "work/fullstack/aftersales-demo.json"))
    with httpx.Client(base_url="http://127.0.0.1:18082", timeout=90, trust_env=False) as client:

        def auth(role="support"):
            r = client.post(
                "/api/v1/auth/login",
                json={
                    "organization": "demo-a",
                    "email": role + "@demo.test",
                    "password": values["WORKBENCH_DOCKER_DEMO_PASSWORD"],
                },
            )
            r.raise_for_status()
            return {"Authorization": "Bearer " + r.json()["access_token"]}

        support, admin = auth(), auth("admin")

        def wait(identifier):
            for _ in range(160):
                r = client.get("/api/v1/runs/" + identifier, headers=support)
                r.raise_for_status()
                row = r.json()
                if row["status"] not in {"queued", "running"}:
                    return row
                time.sleep(0.25)
            raise RuntimeError("Run timeout")

        payload = (
            "运费券只能抵扣运费，不能抵扣商品金额。\n持久化验收标记：" + uuid4().hex
        ).encode()
        r = client.post(
            "/api/v1/knowledge/documents",
            headers=admin,
            data={"title": "容器持久化验收", "policy_scope": "freight_coupon"},
            files={"file": ("persistence.txt", payload, "text/plain")},
        )
        r.raise_for_status()
        version = r.json()["version"]["id"]
        for _ in range(240):
            row = client.get("/api/v1/knowledge/versions/" + version, headers=admin).json()
            if row["status"] == "ready":
                break
            assert row["status"] != "failed"
            time.sleep(0.5)
        else:
            raise RuntimeError("Index timeout")
        client.post(
            "/api/v1/knowledge/versions/" + version + "/publish", headers=admin, json={}
        ).raise_for_status()
        r = client.post(
            "/api/v1/runs",
            headers=support,
            json={
                "kind": "aftersales",
                "query": "请帮我加急",
                "order_number": orders["expedite"],
                "shipping_kind": "expedite",
                "reason": "容器重建恢复验收",
            },
        )
        r.raise_for_status()
        run = wait(r.json()["id"])
        assert run["status"] == "awaiting_confirmation"
        pause = run["output"]["pause_id"]
        identifier = run["id"]
        print(
            "Persisted document and paused graph; recreating all six containers without deleting volumes.",
            flush=True,
        )
        compose("down")
        compose("up", "-d", "--wait", "--wait-timeout", "300")
        support, admin = auth(), auth("admin")
        row = client.get("/api/v1/knowledge/versions/" + version, headers=admin).json()
        assert row["published"]
        assert (
            client.get("/api/v1/knowledge/versions/" + version + "/file", headers=admin).content
            == payload
        )
        result = client.post(
            "/api/v1/knowledge/search",
            headers=support,
            json={
                "query": "运费券可以抵扣商品金额吗？",
                "policy_scope": "freight_coupon",
                "strategy": "hybrid_rerank",
            },
        )
        result.raise_for_status()
        assert any(x["version_id"] == version for x in result.json()["matches"])
        run = wait(identifier)
        assert run["status"] == "awaiting_confirmation" and run["output"]["pause_id"] == pause

        def resume(action):
            nonlocal run
            r = client.post(
                "/api/v1/runs/" + identifier + "/resume",
                headers={**support, "Idempotency-Key": uuid4().hex},
                json={"action": action, "pause_id": run["output"]["pause_id"]},
            )
            r.raise_for_status()
            run = wait(identifier)

        resume("confirm")
        assert run["status"] == "awaiting_approval"
        receipt = run["output"]["request"]["id"]
        client.post(
            "/api/v1/shipping/requests/" + receipt + "/decision",
            headers=auth("approver"),
            json={"action": "approve"},
        ).raise_for_status()
        resume("refresh")
        assert run["status"] == "completed" and run["output"]["request"]["ticket_id"]
        print("Volume, document/vector retrieval and same-pause graph recovery passed.", flush=True)
        compose("stop", "mcp")
        try:
            r = client.post("/api/v1/runs", headers=support, json={"order_number": "A1001"})
            r.raise_for_status()
            failure = wait(r.json()["id"])
            assert failure["status"] == "failed" and not failure.get("output")
        finally:
            compose("up", "-d", "--wait", "--wait-timeout", "120", "mcp")
        r = client.post("/api/v1/runs", headers=support, json={"order_number": "A1001"})
        r.raise_for_status()
        recovered = wait(r.json()["id"])
        assert recovered["status"] == "completed"
        record = {
            "container_recreation_without_volume_delete": True,
            "published_document_and_original_preserved": True,
            "vector_retrieval_after_recreation": True,
            "same_graph_pause_preserved": True,
            "approval_after_recreation": True,
            "mcp_outage_reported_as_failure": True,
            "query_after_mcp_recovery": True,
            "run_id": identifier,
            "document_version": version,
            "paid_model_calls": 0,
        }
        (OUT / "recovery-results.json").write_text(json.dumps(record, indent=2) + "\n")
        print("Real container persistence and fault recovery passed.", flush=True)


if __name__ == "__main__":
    main()
