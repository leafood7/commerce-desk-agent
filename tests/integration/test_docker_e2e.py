"""Opt-in integration checks against the actual CommerceDesk legacy container."""

import os

import httpx
import pytest


@pytest.fixture
def container_api():
    url = os.environ.get("COMMERCE_TEST_URL", "http://127.0.0.1:8080")
    token = os.environ.get("AUTH_SECRET", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=url, headers=headers, timeout=90) as client:
        yield client


@pytest.mark.docker
def test_customer_support_container(container_api):
    info = container_api.get("/info")
    info.raise_for_status()
    assert info.json()["default_agent"] == "supportpilot"
    assert container_api.get("/health").json()["status"] == "ok"
    response = container_api.post("/invoke", json={"message": "浣犲ソ", "model": "fake"})
    response.raise_for_status()
    assert response.json()["type"] == "ai"
    assert response.json()["content"] == "This is a test response from the fake model."


@pytest.mark.docker
def test_order_and_shipping_container(container_api):
    orders = container_api.get("/supportpilot/shipping/orders")
    orders.raise_for_status()
    assert any(order["order_id"] == "A1001" for order in orders.json())
    created = container_api.post(
        "/supportpilot/shipping/requests", json={"order_id": "A1001", "kind": "normal"}
    )
    created.raise_for_status()
    assert created.json()["status"] == "completed"
    again = container_api.post(
        "/supportpilot/shipping/requests", json={"order_id": "A1001", "kind": "normal"}
    )
    assert again.json()["request_id"] == created.json()["request_id"]
