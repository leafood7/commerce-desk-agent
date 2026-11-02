"""Feature checks only: authorization, continuity, content isolation and failures."""

from sqlalchemy import select
from test_return_graph import resume, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_run_dispatch import dispatch_fixture, drain, queued  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench import tracing
from workbench.trace_models import TraceSpan


def test_request_identity_ownership_and_failure(logged_in):
    client, app, headers = logged_in
    response = client.get(
        "/api/v1/orders/not-real-secret-order",
        headers={**headers(), "X-Trace-ID": "injected-trace"},
    )
    assert response.status_code == 404
    trace = response.headers["X-Trace-ID"]
    assert trace != "injected-trace"
    detail = client.get("/api/v1/traces/" + trace, headers=headers())
    assert detail.status_code == 200
    rows = detail.json()["items"]
    assert any(x["service"] == "api" and x["status"] == "failed" for x in rows)
    assert "not-real-secret-order" not in detail.text
    assert client.get("/api/v1/traces/" + trace, headers=headers("admin")).status_code == 404
    assert (
        client.get("/api/v1/traces/" + trace, headers=headers(organization="demo-b")).status_code
        == 404
    )
    assert client.get("/api/v1/traces/" + trace).status_code == 401


def test_run_and_graph_span_linkage(logged_in):
    client, app, headers = logged_in
    result = client.post("/api/v1/runs", headers=headers(), json={"order_number": "A1001"})
    assert result.status_code in {200, 201, 202}, result.text
    run = result.json()
    trace = result.headers["X-Trace-ID"]
    listed = client.get("/api/v1/traces?run_id=" + run["id"], headers=headers()).json()
    assert trace in {r["id"] for r in listed["items"]}
    detail = client.get("/api/v1/traces/" + trace, headers=headers()).json()
    assert any(r["service"] == "agent" for r in detail["items"])
    assert any(r["details"].get("db_calls", 0) > 0 for r in detail["items"])
    assert "Bearer" not in str(detail)


def test_nested_and_explicit_tool_failure(logged_in):
    client, app, headers = logged_in
    client.get("/api/v1/auth/me", headers=headers())
    with app.state.database.sessions() as s:
        actor = s.scalar(
            select(__import__("workbench.models", fromlist=["User"]).User).where(
                __import__("workbench.models", fromlist=["User"]).User.email == "support@demo.test"
            )
        )
    ctx = tracing.TraceContext(app.state.database)
    marker = tracing.context.set(ctx)
    try:
        tracing.register(app.state.database, actor)
        with tracing.span("agent", "consult") as parent:
            with tracing.span("mcp_server", "read_policy_source") as child:
                tracing.result_details(
                    child,
                    {"status": "error", "error": {"code": "not_found", "message": "private-body"}},
                )
                assert tracing.headers()["X-Parent-Span-ID"] == child["id"]
                child["details"]["answer"] = "private-body"
        tracing.flush()
    finally:
        tracing.context.reset(marker)
    detail = client.get("/api/v1/traces/" + ctx.id, headers=headers()).json()
    child = next(r for r in detail["items"] if r["service"] == "mcp_server")
    assert child["parent_id"] == parent["id"] and child["status"] == "failed"
    assert "private-body" not in str(detail)
    assert client.get("/api/v1/traces?limit=1000", headers=headers()).status_code == 422


def test_real_http_trace_search_pause_and_resume(return_flow):  # noqa: F811
    case = return_flow
    run = start(case, return_condition="unknown")
    assert run["status"] == "awaiting_input"
    run = resume(case, run, "supply", return_condition="unused")
    assert run["status"] == "awaiting_confirmation"
    client, app, headers = case[:3]
    roots = client.get("/api/v1/traces?run_id=" + run["id"], headers=headers()).json()["items"]
    details = [client.get("/api/v1/traces/" + t["id"], headers=headers()).json() for t in roots]
    rows = [s for t in details for s in t["items"]]
    services = {s["service"] for s in rows}
    assert {
        "api",
        "agent",
        "langgraph",
        "mcp_client",
        "mcp_server",
        "business",
        "retrieval",
    } <= services
    assert any(s["status"] == "paused" for s in rows)
    by_id = {s["id"]: s for s in rows}
    assert any(
        s["service"] == "mcp_server"
        and by_id.get(s["parent_id"], {}).get("service") == "mcp_client"
        for s in rows
    )
    assert any(s["operation"] == "vector_search" for s in rows)
    assert all("content" not in s["details"] and "query" not in s["details"] for s in rows)


def test_worker_uses_durable_origin_trace(queued):  # noqa: F811
    client, app, headers, _ = queued
    response = client.post("/api/v1/runs", headers=headers(), json={"order_number": "A1001"})
    origin = response.headers["X-Trace-ID"]
    drain(queued)
    detail = client.get("/api/v1/traces/" + origin, headers=headers()).json()
    assert {"api", "worker", "agent"} <= {s["service"] for s in detail["items"]}
    assert detail["run_id"] == response.json()["id"]
    api_id = next(s["id"] for s in detail["items"] if s["service"] == "api")
    assert next(s["parent_id"] for s in detail["items"] if s["service"] == "worker") == api_id


def test_trace_failure_does_not_replace_business_result(logged_in, monkeypatch):
    client, app, headers = logged_in
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def fail_trace(session, flush_context, instances):
        if any(isinstance(x, TraceSpan) for x in session.new):
            raise RuntimeError("private-trace-storage-failure")

    event.listen(Session, "before_flush", fail_trace)
    try:
        result = client.get("/api/v1/orders/A1001", headers=headers())
        assert result.status_code == 200, result.text
        assert "private-trace-storage-failure" not in result.text
    finally:
        event.remove(Session, "before_flush", fail_trace)


def test_uncaught_api_failure_has_diagnostic_id(logged_in, monkeypatch):
    client, app, headers = logged_in

    def fail(*args, **kwargs):
        raise RuntimeError("do-not-expose-this-body")

    monkeypatch.setattr("workbench.app.get_order", fail)
    response = client.get("/api/v1/orders/A1001", headers=headers())
    assert response.status_code == 500
    trace = response.headers["X-Trace-ID"]
    assert "do-not-expose-this-body" not in response.text
    detail = client.get("/api/v1/traces/" + trace, headers=headers())
    assert detail.status_code == 200
    assert any(r["status"] == "failed" for r in detail.json()["items"])
