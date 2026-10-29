import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from helpers import MemoryIndex
from sqlalchemy import select

from workbench.ingestion import work_once
from workbench.knowledge_models import MCPGrant, ToolInvocation
from workbench.mcp_gateway import MCPGateway, ToolFailure
from workbench.mcp_server import create_mcp_app
from workbench.models import now
from workbench.security import digest


@pytest.fixture
def scoped_mcp(logged_in, workbench):
    client, application, headers = logged_in
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = application.state.settings
    settings.mcp_url = f"http://127.0.0.1:{port}/mcp"
    index = MemoryIndex()
    mcp_app = create_mcp_app(settings, index=index)
    server = uvicorn.Server(
        uvicorn.Config(
            mcp_app, host="127.0.0.1", port=port, log_level="error", timeout_graceful_shutdown=2
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    try:
        yield client, application, headers, MCPGateway(settings), index
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def grant(client, auth):
    response = client.post("/api/v1/mcp/grants", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_actual_http_mcp_rejects_login_token_and_uses_dedicated_grant(scoped_mcp):
    client, app, headers, gateway, _ = scoped_mcp
    login_headers = headers()
    direct = httpx.post(app.state.settings.mcp_url, headers=login_headers, json={})
    assert direct.status_code == 401
    token = grant(client, login_headers)
    data = asyncio.run(gateway.call("get_order_status", {"order_number": "a1001"}, token))
    assert data["order"]["number"] == "A1001"
    # A delegated MCP credential is not a login session for the business API.
    assert (
        client.get("/api/v1/orders", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )
    with app.state.database.sessions() as session:
        rows = session.scalars(select(ToolInvocation)).all()
        assert len(rows) == 1 and rows[0].status == "found"
        assert token not in json.dumps(rows[0].arguments)


def test_mcp_grant_revocation_expiry_and_audience(scoped_mcp):
    from datetime import timedelta

    client, app, headers, gateway, _ = scoped_mcp
    auth = headers()
    token = grant(client, auth)
    with app.state.database.sessions() as session:
        entry = session.get(MCPGrant, digest(token))
        entry.expires_at = now() - timedelta(seconds=1)
        session.commit()
    with pytest.raises(ToolFailure):
        asyncio.run(gateway.call("get_order_status", {"order_number": "A1001"}, token))
    token = grant(client, auth)
    with app.state.database.sessions() as session:
        session.get(MCPGrant, digest(token)).audience = "http://different-resource/mcp"
        session.commit()
    with pytest.raises(ToolFailure):
        asyncio.run(gateway.call("get_order_status", {"order_number": "A1001"}, token))
    token = grant(client, auth)
    client.post("/api/v1/auth/logout", headers=auth).raise_for_status()
    with pytest.raises(ToolFailure):
        asyncio.run(gateway.call("get_order_status", {"order_number": "A1001"}, token))


def test_mcp_scope_parameters_and_write_boundary(scoped_mcp):
    client, app, headers, gateway, _ = scoped_mcp
    foreign = grant(client, headers("support", "demo-b"))
    with pytest.raises(ToolFailure, match="not_found"):
        asyncio.run(gateway.call("get_order_status", {"order_number": "A1001"}, foreign))
    admin = grant(client, headers("admin"))
    with pytest.raises(ToolFailure, match="forbidden"):
        asyncio.run(
            gateway.call(
                "request_shipping",
                {"order_number": "A1001", "reason": "test", "idempotency_key": "admin-cannot-act"},
                admin,
            )
        )
    support = grant(client, headers())
    with pytest.raises(ValueError):
        asyncio.run(
            gateway.call(
                "get_order_status", {"order_number": "A1001", "organization_id": "forged"}, support
            )
        )
    body = {
        "order_number": "A1001",
        "kind": "expedite",
        "reason": "test",
        "idempotency_key": "mcp-write",
    }
    first = asyncio.run(gateway.call("request_shipping", body, support))
    second = asyncio.run(gateway.call("request_shipping", body, support))
    assert first["request"]["status"] == "pending"
    assert first["request"]["ticket_id"] is None
    assert first["request"]["id"] == second["request"]["id"]


def test_actual_mcp_search_only_returns_published_current_organization(scoped_mcp):
    client, app, headers, gateway, index = scoped_mcp
    document = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={"policy_scope": "freight_coupon"},
        files={"file": ("scope.txt", "运费券只用于配送费用。".encode(), "text/plain")},
    ).json()
    assert work_once(app.state.database, app.state.settings, index)
    token = grant(client, headers())
    query = {"query": "运费券", "strategy": "hybrid", "policy_scope": "freight_coupon"}
    assert (
        asyncio.run(gateway.call("search_knowledge_base", query, token))["status"] == "no_evidence"
    )
    version = document["version"]["id"]
    client.post(
        f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
    ).raise_for_status()
    result = asyncio.run(gateway.call("search_knowledge_base", query, token))
    assert result["status"] == "found"
    assert result["matches"][0]["version_id"] == version
    foreign = grant(client, headers("support", "demo-b"))
    assert (
        asyncio.run(gateway.call("search_knowledge_base", query, foreign))["status"]
        == "no_evidence"
    )


def test_audit_failure_preserves_committed_business_result(scoped_mcp):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    client, app, headers, gateway, _ = scoped_mcp
    token = grant(client, headers())
    body = {
        "order_number": "A1001",
        "kind": "expedite",
        "reason": "audit failure test",
        "idempotency_key": "audit-failure",
    }

    def reject_tool_audit(session, flush_context, instances):
        if any(isinstance(item, ToolInvocation) for item in session.new):
            raise RuntimeError("simulated audit store failure")

    event.listen(Session, "before_flush", reject_tool_audit)
    try:
        result = asyncio.run(gateway.call("request_shipping", body, token))
    finally:
        event.remove(Session, "before_flush", reject_tool_audit)
    assert result["status"] == "success"
    assert result["audit_status"] == "unavailable"
    assert result["request"]["status"] == "pending"
    assert result["request"]["ticket_id"] is None
    retry = asyncio.run(gateway.call("request_shipping", body, token))
    assert retry["request"]["id"] == result["request"]["id"]
