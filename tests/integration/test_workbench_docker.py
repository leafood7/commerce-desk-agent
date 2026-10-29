"""Opt-in checks for the authenticated default Compose application."""

import os

import httpx
import pytest


@pytest.mark.docker
def test_workbench_container_login_lookup_and_scope():
    password = os.environ["WORKBENCH_TEST_PASSWORD"]
    with httpx.Client(
        base_url=os.getenv("WORKBENCH_TEST_URL", "http://127.0.0.1:8082"), timeout=30
    ) as client:
        assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/orders").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"organization": "demo-a", "email": "support@demo.test", "password": password},
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        created = client.post("/api/v1/runs", headers=headers, json={"order_number": "A1001"})
        created.raise_for_status()
        run_id = created.json()["id"]
        response = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
        assert "answer.published" in response.text
        result = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        assert result["status"] == "completed"
        assert result["output"]["order"]["number"] == "A1001"
        assert client.get("/api/v1/orders/B2001", headers=headers).status_code == 404
