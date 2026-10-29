"""Real acceptance stack drill: stop only the dispatch worker; never delete volumes."""

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import test_workbench_docker_flows as docker_flows

pytestmark = pytest.mark.docker
container_api = docker_flows.container_api
ROOT = Path(__file__).resolve().parents[2]


def compose(*args):
    command = [
        r"D:\Docker\app\resources\bin\docker.exe",
        "--host",
        "npipe:////./pipe/dockerDesktopLinuxEngine",
        "compose",
        "--project-name",
        "commercedesk-acceptance",
        "--env-file",
        str(ROOT / "work/docker/acceptance.env"),
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "compose.acceptance.yaml"),
        *args,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        timeout=90,
        env={**os.environ, "DOCKER_CONFIG": r"D:\Docker\config"},
    )
    assert result.returncode == 0, "Acceptance compose operation failed"


def ready(client):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if client.get("/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError("API did not become ready after restart")


def test_real_queued_job_survives_api_restart_and_runs_once(container_api):
    client, headers = container_api
    auth = headers()
    profile = client.get("/api/v1/runtime/profile", headers=auth).json()
    assert profile["dispatch"] == "worker"
    compose("stop", "run-worker")
    try:
        key = "queue-restart-" + uuid4().hex
        body = {"kind": "order_lookup", "order_number": "A1001"}
        first = client.post("/api/v1/runs", headers={**auth, "Idempotency-Key": key}, json=body)
        first.raise_for_status()
        run = first.json()
        assert run["status"] == "queued"
        duplicate = client.post(
            "/api/v1/runs", headers={**auth, "Idempotency-Key": key}, json=body
        ).json()
        assert duplicate["id"] == run["id"]
        compose("restart", "api")
        ready(client)
        assert client.get("/api/v1/runs/" + run["id"], headers=auth).json()["status"] == "queued"
    finally:
        compose("start", "run-worker")
    result = docker_flows.wait_run(client, auth, run["id"])
    assert result["status"] == "completed"
    assert result["output"]["order"]["number"] == "A1001"
    response = client.get("/api/v1/runs/" + run["id"] + "/events", headers=auth)
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert sum(row["type"] == "tool.started" for row in events) == 1


def test_real_cancelled_queued_job_never_executes(container_api):
    client, headers = container_api
    auth = headers()
    compose("stop", "run-worker")
    try:
        response = client.post(
            "/api/v1/runs", headers=auth, json={"kind": "order_lookup", "order_number": "A1001"}
        )
        response.raise_for_status()
        run = response.json()
        assert run["status"] == "queued"
        cancelled = client.post("/api/v1/runs/" + run["id"] + "/cancel", headers=auth)
        cancelled.raise_for_status()
        assert cancelled.json()["status"] == "cancelled"
    finally:
        compose("start", "run-worker")
    time.sleep(1)
    assert client.get("/api/v1/runs/" + run["id"], headers=auth).json()["status"] == "cancelled"
    response = client.get("/api/v1/runs/" + run["id"] + "/events", headers=auth)
    assert '"tool.started"' not in response.text


def test_real_admission_full_queue_is_idempotent_and_cancel_frees_capacity(container_api):
    client, headers = container_api
    auth = headers()
    profile = client.get("/api/v1/runtime/profile", headers=auth).json()
    limit = profile["admission"]["owner_active_runs"]
    assert 1 <= limit <= 32, "This small acceptance drill supports at most32 read jobs"
    created = []
    compose("stop", "run-worker")
    try:
        key = "quota-fill-" + uuid4().hex
        first = client.post(
            "/api/v1/runs", headers={**auth, "Idempotency-Key": key}, json={"order_number": "A1001"}
        )
        first.raise_for_status()
        created.append(first.json()["id"])
        for _ in range(limit - 1):
            response = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"})
            response.raise_for_status()
            created.append(response.json()["id"])
        denied = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"})
        assert denied.status_code == 429 and denied.headers["Retry-After"] == "2"
        duplicate = client.post(
            "/api/v1/runs", headers={**auth, "Idempotency-Key": key}, json={"order_number": "A1001"}
        )
        duplicate.raise_for_status()
        assert duplicate.json()["id"] == created[0]
        data = client.get("/api/v1/runs/" + created[0] + "/usage", headers=auth).json()
        assert (
            data["tool_attempts"] == 0
            and data["max_tools"] == profile["cumulative"]["tool_attempts"]
        )
        client.post("/api/v1/runs/" + created.pop() + "/cancel", headers=auth).raise_for_status()
        response = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"})
        response.raise_for_status()
        created.append(response.json()["id"])
    finally:
        for run_id in created:
            client.post("/api/v1/runs/" + run_id + "/cancel", headers=auth).raise_for_status()
        compose("start", "run-worker")
