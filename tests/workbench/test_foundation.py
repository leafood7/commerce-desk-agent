from datetime import timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from workbench.app import create_app
from workbench.cli import migration_config
from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.models import AuthSession, Order, Run, ShippingTicket, User, now
from workbench.runs import create_run
from workbench.schemas import RunInput
from workbench.security import digest, password_hash, password_matches


def test_password_and_session_revocation(logged_in):
    client, app, headers = logged_in
    auth = headers()
    assert client.get("/api/v1/auth/me", headers=auth).json()["role"] == "support"
    with app.state.database.sessions() as session:
        token = auth["Authorization"].split()[1]
        record = session.get(AuthSession, digest(token))
        assert record and record.token_hash != token
    assert client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401


def test_passwords_are_salted_and_malformed_hash_fails():
    first, second = password_hash("strong-passphrase"), password_hash("strong-passphrase")
    assert first != second
    assert password_matches("strong-passphrase", first)
    assert not password_matches("wrong", first)
    assert not password_matches("secret", "corrupt")


def test_expired_and_disabled_users_cannot_continue(logged_in):
    client, app, headers = logged_in
    auth = headers()
    with app.state.database.sessions() as session:
        record = session.get(AuthSession, digest(auth["Authorization"].split()[1]))
        record.expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
    auth_b = headers("support", "demo-b")
    with app.state.database.sessions() as session:
        record = session.get(AuthSession, digest(auth_b["Authorization"].split()[1]))
        session.get(User, record.user_id).active = False
        session.commit()
    assert client.get("/api/v1/orders", headers=auth_b).status_code == 401


def test_no_anonymous_or_legacy_bypass(logged_in):
    client, _, headers = logged_in
    assert client.get("/api/v1/orders").status_code == 401
    for path in ["/history", "/invoke", "/supportpilot/shipping/orders"]:
        assert client.get(path, headers=headers()).status_code == 404


def test_cross_organization_order_lookup_is_denied(logged_in):
    client, _, headers = logged_in
    a, b = headers(), headers("support", "demo-b")
    assert client.get("/api/v1/orders/A1001", headers=a).status_code == 200
    assert client.get("/api/v1/orders/A1001", headers=b).status_code == 404
    assert client.get("/api/v1/orders/B2001", headers=a).status_code == 404
    order = client.get("/api/v1/orders/A1001", headers=a).json()
    assert order["signed_at"] is None
    assert order["data_source"] == "demo_seed"
    assert order["items"][0]["unit_price_cents"] > 0


def test_roles_and_identity_cannot_be_supplied_by_body(logged_in):
    client, _, headers = logged_in
    support = headers()
    response = client.post(
        "/api/v1/users",
        headers=support,
        json={
            "email": "new@demo.test",
            "display_name": "new",
            "role": "admin",
            "password": "strong-password-12345",
        },
    )
    assert response.status_code == 403
    body = {"kind": "order_lookup", "order_number": "A1001", "user_id": "victim"}
    assert client.post("/api/v1/runs", headers=support, json=body).status_code == 422
    body.pop("user_id")
    body["organization_id"] = "other"
    assert client.post("/api/v1/runs", headers=support, json=body).status_code == 422


def test_admin_can_create_user_only_in_own_organization(logged_in):
    client, _, headers = logged_in
    body = {
        "email": "new@demo.test",
        "display_name": "new",
        "role": "support",
        "password": "strong-password-12345",
    }
    created = client.post("/api/v1/users", headers=headers("admin"), json=body)
    assert created.status_code == 201
    assert "password" not in created.text
    assert client.post("/api/v1/users", headers=headers("admin"), json=body).status_code == 409


def test_login_throttle_and_secret_redaction(workbench):
    client, _, _ = workbench
    body = {"organization": "demo-a", "email": "absent@demo.test", "password": "wrong-password-123"}
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json=body).status_code == 401
    assert client.post("/api/v1/auth/login", json=body).status_code == 429
    body["password"] = "xy#91"
    response = client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 422
    assert "xy#91" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_order_run_events_history_and_idempotency(logged_in):
    client, app, headers = logged_in
    auth = {**headers(), "Idempotency-Key": "lookup-001"}
    body = {"order_number": "A1001"}
    created = client.post("/api/v1/runs", headers=auth, json=body)
    assert created.status_code == 202
    identifier = created.json()["id"]
    result = client.get("/api/v1/runs/" + identifier, headers=auth).json()
    assert result["status"] == "completed"
    assert result["output"]["order"]["number"] == "A1001"
    assert "实际签收时间未提供" in result["output"]["answer"]
    again = client.post("/api/v1/runs", headers=auth, json=body)
    assert again.json()["id"] == identifier
    assert (
        client.post("/api/v1/runs", headers=auth, json={"order_number": "A1002"}).status_code == 409
    )
    events = client.get(f"/api/v1/runs/{identifier}/events", headers=auth)
    assert events.status_code == 200
    assert "answer.published" in events.text and "tool.finished" in events.text
    ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
    assert ids == sorted(set(ids))
    resumed = client.get(
        f"/api/v1/runs/{identifier}/events", headers={**auth, "Last-Event-ID": str(ids[-2])}
    )
    assert resumed.text.count("event: ") == 1
    assert (
        client.get(
            f"/api/v1/runs/{identifier}/events", headers={**auth, "Last-Event-ID": "invalid"}
        ).status_code
        == 422
    )
    history = client.get(
        f"/api/v1/conversations/{result['conversation_id']}/messages", headers=auth
    ).json()["items"]
    assert [row["role"] for row in history] == ["human", "assistant"]
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1


def test_cross_user_history_run_event_and_cancel_are_denied(logged_in):
    client, _, headers = logged_in
    auth = headers()
    run = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"}).json()
    for other in [headers("approver"), headers("support", "demo-b")]:
        paths = [
            f"/api/v1/runs/{run['id']}",
            f"/api/v1/runs/{run['id']}/events",
            f"/api/v1/conversations/{run['conversation_id']}/messages",
        ]
        for path in paths:
            assert client.get(path, headers=other).status_code == 404
        assert client.post(f"/api/v1/runs/{run['id']}/cancel", headers=other).status_code == 404
        assert (
            client.post(
                "/api/v1/runs",
                headers=other,
                json={"order_number": "A1001", "conversation_id": run["conversation_id"]},
            ).status_code
            == 404
        )


def test_shipping_preserves_approval_and_duplicate_guards(logged_in):
    client, app, headers = logged_in
    support, approver = headers(), headers("approver")
    normal = client.post(
        "/api/v1/shipping/requests",
        headers=support,
        json={"order_number": "A1001", "kind": "normal"},
    )
    assert normal.status_code == 201 and normal.json()["ticket_id"]
    body = {"order_number": "A1001", "kind": "expedite"}
    request = client.post(
        "/api/v1/shipping/requests",
        headers={**support, "Idempotency-Key": "shipping-001"},
        json=body,
    ).json()
    assert request["status"] == "pending" and request["ticket_id"] is None
    url = f"/api/v1/shipping/requests/{request['id']}/decision"
    assert client.post(url, headers=support, json={"action": "approve"}).status_code == 403
    assert (
        client.post(
            url, headers=headers("approver", "demo-b"), json={"action": "approve"}
        ).status_code
        == 404
    )
    approved = client.post(url, headers=approver, json={"action": "approve"})
    assert approved.json()["status"] == "completed"
    repeated = client.post(url, headers=approver, json={"action": "approve"})
    assert repeated.json()["ticket_id"] == approved.json()["ticket_id"]
    assert approved.json()["external_warehouse_notified"] is False
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ShippingTicket)) == 2


def test_shipping_rechecks_order_after_approval_wait(logged_in):
    client, app, headers = logged_in
    auth = headers()
    response = client.post(
        "/api/v1/shipping/requests",
        headers=auth,
        json={"order_number": "A1001", "kind": "expedite"},
    ).json()
    with app.state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == "A1001"))
        order.shipping_status = "运输中"
        session.commit()
    result = client.post(
        f"/api/v1/shipping/requests/{response['id']}/decision",
        headers=headers("approver"),
        json={"action": "approve"},
    ).json()
    assert result["status"] == "invalidated" and result["ticket_id"] is None


def test_shipping_invalid_arguments_and_same_key_different_payload(logged_in):
    client, _, headers = logged_in
    auth = {**headers(), "Idempotency-Key": "shipping-001"}
    assert (
        client.post(
            "/api/v1/shipping/requests",
            headers=auth,
            json={"order_number": "A1002", "kind": "normal"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/shipping/requests",
            headers=auth,
            json={"order_number": "A1001", "kind": "unknown"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/shipping/requests",
            headers=auth,
            json={"order_number": "A1001", "kind": "normal"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/shipping/requests",
            headers=auth,
            json={"order_number": "A1001", "kind": "expedite"},
        ).status_code
        == 409
    )


def test_restart_preserves_results_and_marks_unfinished_runs(logged_in):
    client, app, headers = logged_in
    auth = headers()
    completed = client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"}).json()
    with app.state.database.sessions() as session:
        user = session.scalar(
            select(User).where(
                User.id
                == session.get(AuthSession, digest(auth["Authorization"].split()[1])).user_id
            )
        )
        unfinished, _ = create_run(session, user, RunInput(order_number="A1001"))
        unfinished_id = unfinished.id
    from helpers import LocalTestGateway

    second = create_app(
        app.state.settings, gateway=LocalTestGateway(app.state.database, app.state.settings)
    )
    with TestClient(second) as restarted:
        previous = restarted.get(f"/api/v1/runs/{completed['id']}", headers=auth)
        assert previous.json()["status"] == "completed"
        stopped = restarted.get(f"/api/v1/runs/{unfinished_id}", headers=auth)
        assert stopped.json()["status"] == "interrupted"
        assert stopped.json()["error_code"] == "service_restarted"


def test_cancel_is_idempotent_and_does_not_execute(logged_in):
    client, app, headers = logged_in
    auth = headers()
    with app.state.database.sessions() as session:
        user = session.scalar(
            select(User).where(
                User.id
                == session.get(AuthSession, digest(auth["Authorization"].split()[1])).user_id
            )
        )
        run, _ = create_run(session, user, RunInput(order_number="A1001"))
        identifier = run.id
    first = client.post(f"/api/v1/runs/{identifier}/cancel", headers=auth).json()
    again = client.post(f"/api/v1/runs/{identifier}/cancel", headers=auth).json()
    assert first["status"] == again["status"] == "cancelled"
    assert first["output"] is None


def test_migrations_upgrade_downgrade_and_schema_drift(tmp_path):
    settings = WorkbenchSettings(
        environment="test",
        database_url=SecretStr(
            "sqlite:///" + str(tmp_path / "migration.sqlite3").replace("\\", "/")
        ),
    )
    config = migration_config(settings)
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    database = Database(settings)
    database.check_schema()
    database.close()


def test_mysql_schema_compiles_without_sqlite_types(tmp_path):
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    from workbench.models import Base

    statements = [
        str(CreateTable(table).compile(dialect=mysql.dialect()))
        for table in Base.metadata.sorted_tables
    ]
    assert any("DATETIME(6)" in statement for statement in statements)
    assert any("JSON" in statement for statement in statements)
    assert not any("AUTOINCREMENT" in statement for statement in statements)
    with pytest.raises(ValueError):
        WorkbenchSettings(
            environment="production", database_url=SecretStr("sqlite:///forbidden.db")
        )


def test_admin_disable_revokes_sessions_and_cannot_cross_scope(logged_in):
    client, _, headers = logged_in
    support = headers()
    identifier = client.get("/api/v1/auth/me", headers=support).json()["id"]
    foreign = headers("admin", "demo-b")
    assert client.post(f"/api/v1/users/{identifier}/disable", headers=foreign).status_code == 404
    admin = headers("admin")
    own_id = client.get("/api/v1/auth/me", headers=admin).json()["id"]
    assert client.post(f"/api/v1/users/{own_id}/disable", headers=admin).status_code == 409
    assert client.post(f"/api/v1/users/{identifier}/disable", headers=admin).status_code == 200
    assert client.get("/api/v1/orders", headers=support).status_code == 401


def test_order_list_uses_one_item_query(logged_in):
    from sqlalchemy import event

    client, app, headers = logged_in
    auth = headers()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(app.state.database.engine, "before_cursor_execute", capture)
    try:
        result = client.get("/api/v1/orders", headers=auth)
    finally:
        event.remove(app.state.database.engine, "before_cursor_execute", capture)
    assert result.status_code == 200
    assert len(result.json()["items"]) == 4
    assert sum("FROM wb_order_items" in statement for statement in statements) == 1


def test_concurrent_approvals_create_one_ticket(logged_in):
    from concurrent.futures import ThreadPoolExecutor

    client, app, headers = logged_in
    support, approver = headers(), headers("approver")
    request = client.post(
        "/api/v1/shipping/requests",
        headers=support,
        json={"order_number": "A1001", "kind": "expedite"},
    ).json()

    def approve(_):
        return client.post(
            f"/api/v1/shipping/requests/{request['id']}/decision",
            headers=approver,
            json={"action": "approve"},
        )

    # SELECT FOR UPDATE is the real MySQL guarantee; SQLite cannot model row locks.
    if app.state.database.engine.dialect.name != "mysql":
        pytest.skip("Concurrent row-lock behavior is exercised against real MySQL")
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(approve, range(4)))
    assert all(reply.status_code == 200 for reply in replies)
    assert len({reply.json()["ticket_id"] for reply in replies}) == 1
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ShippingTicket)) == 1


def test_concurrent_identical_runs_do_not_duplicate_messages(logged_in):
    from concurrent.futures import ThreadPoolExecutor

    client, app, headers = logged_in
    auth = {**headers(), "Idempotency-Key": "parallel-run"}
    if app.state.database.engine.dialect.name != "mysql":
        pytest.skip("Concurrent transaction test requires real MySQL")

    def create(_):
        return client.post("/api/v1/runs", headers=auth, json={"order_number": "A1001"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(create, range(4)))
    assert all(reply.status_code == 202 for reply in replies)
    assert len({reply.json()["id"] for reply in replies}) == 1
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1


def test_runtime_profile_requires_login_and_only_returns_safe_actual_settings(logged_in):
    client, app, headers = logged_in
    assert client.get("/api/v1/runtime/profile").status_code == 401
    result = client.get("/api/v1/runtime/profile", headers=headers()).json()
    assert result["execution"]["tool_attempts"] == app.state.settings.agent_tool_limit
    assert result["router"]["mode"] == "rules"
    assert result["router"]["model"] is None
    assert set(result) == {
        "router",
        "execution",
        "retrieval",
        "dispatch",
        "admission",
        "cumulative",
    }
    serialized = str(result)
    for forbidden in ("database_url", "agent_router_key", "mcp_url", "password", "Bearer"):
        assert forbidden not in serialized
    assert (
        client.post(
            "/api/v1/runtime/profile", json={"tool_attempts": 999}, headers=headers()
        ).status_code
        == 405
    )
