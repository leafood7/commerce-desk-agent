"""Workflow invariants using explicit tool substitutes; real HTTP/browser checked separately."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from workbench.app import create_app
from workbench.domain import create_shipping, shipping_view
from workbench.models import Order, ShippingRequest, ShippingTicket
from workbench.schemas import ShippingInput
from workbench.tool_contracts import verify_grant


@pytest.fixture
def flow(logged_in, monkeypatch):
    client, app, headers = logged_in
    gateway = app.state.gateway
    original = gateway.call
    calls = []

    async def call(name, arguments, token):
        calls.append((name, dict(arguments)))
        if name == "get_order_status":
            return await original(name, arguments, token)
        with app.state.database.sessions() as session:
            actor, scopes = verify_grant(session, token, app.state.settings)
            if name == "search_knowledge_base":
                return {"status": "no_evidence", "matches": [], "trace": {}}
            if name == "request_shipping":
                assert "shipping:write" in scopes
                body = ShippingInput(
                    **{k: v for k, v in arguments.items() if k != "idempotency_key"}
                )
                return {
                    "status": "success",
                    "request": shipping_view(
                        session, create_shipping(session, actor, body, arguments["idempotency_key"])
                    ),
                }
            if name == "get_shipping_status":
                receipt = session.get(ShippingRequest, arguments["request_id"])
                assert receipt.organization_id == actor.organization_id
                return {"status": "found", "request": shipping_view(session, receipt)}
        raise ValueError(name)

    monkeypatch.setattr(gateway, "call", call)
    return client, app, headers, calls


def start(flow, **overrides):
    client, app, headers, calls = flow
    data = {
        "kind": "aftersales",
        "query": "请帮我催发货",
        "order_number": "A1001",
        "shipping_kind": "expedite",
        "reason": "已超时，希望加急",
    }
    data.update(overrides)
    response = client.post("/api/v1/runs", headers=headers(), json=data)
    assert response.status_code == 202, response.text
    return current(flow, response.json()["id"])


def current(flow, identifier):
    return flow[0].get("/api/v1/runs/" + identifier, headers=flow[2]()).json()


def resume(flow, run, action, key=None, **changes):
    response = flow[0].post(
        "/api/v1/runs/" + run["id"] + "/resume",
        headers={**flow[2](), "Idempotency-Key": key or str(uuid4())},
        json={"action": action, "pause_id": (run.get("output") or {}).get("pause_id"), **changes},
    )
    assert response.status_code == 202, response.text
    return current(flow, run["id"])


def count(flow, model):
    with flow[1].state.database.sessions() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_graph_collects_missing_fields_and_never_writes_before_confirmation(flow):
    run = start(flow, order_number=None, shipping_kind=None, reason=None)
    assert run["status"] == "awaiting_input"
    assert count(flow, ShippingRequest) == 0
    run = resume(flow, run, "supply", order_number="a1001")
    assert run["status"] == "awaiting_input"
    run = resume(flow, run, "supply", shipping_kind="normal", reason="等待太久")
    assert run["status"] == "awaiting_confirmation"
    assert run["output"]["proposal"]["order_number"] == "A1001"
    assert count(flow, ShippingRequest) == 0
    done = resume(flow, run, "confirm")
    assert done["status"] == "completed"
    assert done["output"]["request"]["status"] == "completed"
    assert done["output"]["request"]["external_warehouse_notified"] is False
    assert count(flow, ShippingTicket) == 1


def test_expedite_confirmation_approval_and_real_status(flow):
    run = start(flow)
    assert run["status"] == "awaiting_confirmation"
    run = resume(flow, run, "confirm")
    assert run["status"] == "awaiting_approval", run
    receipt = run["output"]["request"]
    assert receipt["ticket_id"] is None
    assert count(flow, ShippingTicket) == 0
    run = resume(flow, run, "refresh")
    assert run["status"] == "awaiting_approval"
    path = "/api/v1/shipping/requests/" + receipt["id"] + "/decision"
    assert flow[0].post(path, headers=flow[2](), json={"action": "approve"}).status_code == 403
    response = flow[0].post(path, headers=flow[2]("approver"), json={"action": "approve"})
    assert response.status_code == 200, response.text
    run = resume(flow, run, "refresh")
    assert run["status"] == "completed"
    assert run["output"]["request"]["ticket_id"]
    assert "尚未通知外部仓库" in run["output"]["answer"]


def test_correction_requires_new_confirmation_and_cannot_reuse_stale_pause(flow):
    first = start(flow)
    corrected = resume(
        flow, first, "correct", order_number="A1004", shipping_kind="normal", reason="改为普通催单"
    )
    assert corrected["status"] == "awaiting_confirmation"
    assert corrected["output"]["proposal"]["order_number"] == "A1004"
    assert corrected["output"]["pause_id"] != first["output"]["pause_id"]
    response = flow[0].post(
        "/api/v1/runs/" + first["id"] + "/resume",
        headers={**flow[2](), "Idempotency-Key": "stale"},
        json={"action": "confirm", "pause_id": first["output"]["pause_id"]},
    )
    assert response.status_code == 409
    assert count(flow, ShippingRequest) == 0
    done = resume(flow, corrected, "confirm")
    assert done["output"]["request"]["order_number"] == "A1004"
    assert done["output"]["request"]["kind"] == "normal"


def test_resume_keys_are_idempotent_and_cannot_modify_confirmed_fields(flow):
    run = start(flow)
    bad = flow[0].post(
        "/api/v1/runs/" + run["id"] + "/resume",
        headers={**flow[2](), "Idempotency-Key": "bad"},
        json={"action": "confirm", "pause_id": run["output"]["pause_id"], "order_number": "A1004"},
    )
    assert bad.status_code == 422
    done = resume(flow, run, "confirm", key="same")
    repeated = resume(flow, run, "confirm", key="same")
    assert done["output"]["request"]["id"] == repeated["output"]["request"]["id"]
    assert count(flow, ShippingRequest) == 1


@pytest.mark.parametrize("changed", ["shipping_status", "promised_ship_at"])
def test_business_conditions_rechecked_after_confirmation(flow, changed):
    run = start(flow)
    with flow[1].state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == "A1001"))
        setattr(order, changed, "运输中" if changed == "shipping_status" else None)
        session.commit()
    done = resume(flow, run, "confirm")
    assert done["status"] == "completed", done
    assert "未提交申请" in done["output"]["answer"]
    assert count(flow, ShippingRequest) == 0


def test_waiting_checkpoint_survives_new_application_and_no_duplicate_write(flow):
    run = start(flow)
    old_pause = run["output"]["pause_id"]
    app = create_app(flow[1].state.settings, gateway=flow[1].state.gateway)
    with TestClient(app) as client:
        restored = client.get("/api/v1/runs/" + run["id"], headers=flow[2]()).json()
        assert restored["status"] == "awaiting_confirmation"
        assert restored["output"]["pause_id"] == old_pause
        response = client.post(
            "/api/v1/runs/" + run["id"] + "/resume",
            headers={**flow[2](), "Idempotency-Key": "restart-confirm"},
            json={"action": "confirm", "pause_id": old_pause},
        )
        assert response.status_code == 202, response.text
        final = client.get("/api/v1/runs/" + run["id"], headers=flow[2]()).json()
        assert final["status"] == "awaiting_approval", final
    assert count(flow, ShippingRequest) == 1


def test_cross_organization_and_other_owner_cannot_resume(flow):
    run = start(flow)
    path = "/api/v1/runs/" + run["id"] + "/resume"
    body = {"action": "confirm", "pause_id": run["output"]["pause_id"]}
    for auth in (flow[2]("support", "demo-b"), flow[2]("admin")):
        assert (
            flow[0]
            .post(path, headers={**auth, "Idempotency-Key": str(uuid4())}, json=body)
            .status_code
            == 404
        )
    assert count(flow, ShippingRequest) == 0


def test_cancel_before_confirm_and_cancel_tracking_do_not_claim_withdrawal(flow):
    run = start(flow)
    done = resume(flow, run, "cancel")
    assert done["status"] == "cancelled"
    assert count(flow, ShippingRequest) == 0
    run = resume(flow, start(flow), "confirm")
    response = flow[0].post("/api/v1/runs/" + run["id"] + "/cancel", headers=flow[2]())
    assert response.status_code == 200
    assert "未被撤销" in response.json()["output"]["answer"]
    assert count(flow, ShippingRequest) == 1


def test_uncertain_write_recovers_with_stable_key(flow, monkeypatch):
    from workbench.mcp_gateway import ToolFailure

    gateway = flow[1].state.gateway
    original = gateway.call
    failed = False

    async def flaky(name, arguments, token):
        nonlocal failed
        result = await original(name, arguments, token)
        if name == "request_shipping" and not failed:
            failed = True
            raise ToolFailure("timeout")
        return result

    monkeypatch.setattr(gateway, "call", flaky)
    run = resume(flow, start(flow), "confirm")
    assert run["status"] == "interrupted", run
    assert run["output"]["write_in_flight"] is True
    assert count(flow, ShippingRequest) == 1
    assert (
        flow[0].post("/api/v1/runs/" + run["id"] + "/cancel", headers=flow[2]()).status_code == 409
    )
    recovered = resume(flow, run, "retry")
    assert recovered["status"] == "awaiting_approval", recovered
    assert count(flow, ShippingRequest) == 1


def test_policy_lookup_and_unsupported_task_do_not_write(flow):
    run = start(flow, query="退货政策是什么", order_number=None, shipping_kind=None, reason=None)
    assert run["status"] == "completed"
    assert "没有可用依据" in run["output"]["answer"]
    unsupported = start(flow, query="帮我退款", order_number=None, shipping_kind=None, reason=None)
    assert "尚未接入" in unsupported["output"]["answer"]
    assert count(flow, ShippingRequest) == 0


def test_public_stream_closes_at_pause_without_exposing_graph_internals(flow):
    run = start(flow)
    events = flow[0].get("/api/v1/runs/" + run["id"] + "/events", headers=flow[2]())
    assert events.status_code == 200
    assert "run.paused" in events.text
    messages = flow[0].get(
        "/api/v1/conversations/" + run["conversation_id"] + "/messages", headers=flow[2]()
    )
    public = json.dumps([run, events.text, messages.json()])
    for forbidden in ("channel_values", "pending_writes", "checkpoint_id", "parent_session_hash"):
        assert forbidden not in public
    assert not any(name == "request_shipping" for name, _ in flow[3])


@pytest.mark.parametrize("action", ["confirm", "refresh"])
def test_input_pause_cannot_be_skipped_by_wrong_action(flow, action):
    run = start(flow, order_number=None, shipping_kind=None, reason=None)
    response = flow[0].post(
        "/api/v1/runs/" + run["id"] + "/resume",
        headers={**flow[2](), "Idempotency-Key": str(uuid4())},
        json={"action": action, "pause_id": run["output"]["pause_id"]},
    )
    assert response.status_code == 409
    assert count(flow, ShippingRequest) == 0


def test_model_router_is_optional_bounded_and_does_not_authorize_writes(flow, monkeypatch):
    import workbench.intent_router as router

    count_calls = []

    def classify_with_usage(settings, query):
        count_calls.append(query)
        return "shipping", {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}

    monkeypatch.setattr(router, "model_intent", classify_with_usage)
    flow[1].state.settings.agent_router_mode = "model"
    run = start(flow, query="please expedite my parcel")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["planner_mode"] == "model"
    assert count(flow, ShippingRequest) == 0
    done = resume(flow, run, "confirm")
    assert done["status"] == "awaiting_approval"
    assert len(count_calls) == 1


def test_invalid_model_route_falls_back_without_retry(flow, monkeypatch):
    import workbench.intent_router as router

    calls = []

    def invalid(settings, query):
        calls.append(query)
        raise ValueError("invalid model JSON")

    monkeypatch.setattr(router, "model_intent", invalid)
    flow[1].state.settings.agent_router_mode = "model"
    run = start(flow)
    assert run["status"] == "awaiting_confirmation"
    assert run["output"]["planner_mode"] == "model_unavailable_fallback"
    resume(flow, run, "confirm")
    assert len(calls) == 1


def test_evidence_withdrawn_while_waiting_blocks_new_submission(flow, monkeypatch):
    from fastapi import HTTPException

    import workbench.aftersales as service

    run = start(flow)

    def withdrawn(*args):
        raise HTTPException(409, "evidence changed")

    monkeypatch.setattr(service, "validated_evidence", withdrawn)
    done = resume(flow, run, "confirm")
    assert done["status"] == "interrupted"
    assert done["error_code"] == "invalid_or_expired_evidence"
    assert count(flow, ShippingRequest) == 0


def test_business_approval_does_not_depend_on_old_evidence_after_submission(flow, monkeypatch):
    from fastapi import HTTPException

    import workbench.aftersales as service

    run = resume(flow, start(flow), "confirm")
    request_id = run["output"]["request"]["id"]
    flow[0].post(
        "/api/v1/shipping/requests/" + request_id + "/decision",
        headers=flow[2]("approver"),
        json={"action": "approve"},
    ).raise_for_status()

    def withdrawn(*args):
        raise HTTPException(409, "evidence changed")

    monkeypatch.setattr(service, "validated_evidence", withdrawn)
    done = resume(flow, run, "refresh")
    assert done["status"] == "completed", done
    assert done["output"]["request"]["status"] == "completed"
    assert done["output"]["warnings"]
    assert done["output"]["evidence"] == []


def test_recent_runs_are_owner_scoped_and_extra_business_fields_rejected(flow):
    run = start(flow)
    assert (
        flow[0].get("/api/v1/aftersales/runs", headers=flow[2]()).json()["items"][0]["id"]
        == run["id"]
    )
    assert flow[0].get("/api/v1/aftersales/runs", headers=flow[2]("admin")).json()["items"] == []
    response = flow[0].post(
        "/api/v1/runs",
        headers=flow[2]("admin"),
        json={"kind": "aftersales", "query": "帮我加急", "order_number": "A1001"},
    )
    assert response.status_code == 403


def test_cancel_during_write_is_rejected_until_real_receipt(flow, monkeypatch):
    import threading

    gateway = flow[1].state.gateway
    original = gateway.call
    entered, released = threading.Event(), threading.Event()

    async def slow(name, args, token):
        if name == "request_shipping":
            entered.set()
            assert released.wait(8)
        return await original(name, args, token)

    monkeypatch.setattr(gateway, "call", slow)
    run = start(flow)
    errors = []

    def confirm():
        try:
            resume(flow, run, "confirm")
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=confirm)
    thread.start()
    try:
        assert entered.wait(5)
        response = flow[0].post("/api/v1/runs/" + run["id"] + "/cancel", headers=flow[2]())
        assert response.status_code == 409
    finally:
        released.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert count(flow, ShippingRequest) == 1


@pytest.mark.parametrize("decision", ["reject", "invalidate"])
def test_independent_decision_is_rendered_without_creating_ticket(flow, decision):
    run = resume(flow, start(flow), "confirm")
    identifier = run["output"]["request"]["id"]
    if decision == "invalidate":
        with flow[1].state.database.sessions() as session:
            order = session.scalar(select(Order).where(Order.number == "A1001"))
            order.shipping_status = "运输中"
            session.commit()
    response = flow[0].post(
        "/api/v1/shipping/requests/" + identifier + "/decision",
        headers=flow[2]("approver"),
        json={"action": "reject" if decision == "reject" else "approve"},
    )
    assert response.status_code == 200
    run = resume(flow, run, "refresh")
    assert run["status"] == "completed"
    assert run["output"]["request"]["status"] == (
        "rejected" if decision == "reject" else "invalidated"
    )
    assert run["output"]["request"]["ticket_id"] is None
    assert count(flow, ShippingTicket) == 0


def test_concurrent_confirmation_only_one_continuation_is_accepted(flow):
    import threading

    run = start(flow)
    barrier = threading.Barrier(2)
    results = []

    def confirm(key):
        barrier.wait(timeout=5)
        response = flow[0].post(
            "/api/v1/runs/" + run["id"] + "/resume",
            headers={**flow[2](), "Idempotency-Key": key},
            json={"action": "confirm", "pause_id": run["output"]["pause_id"]},
        )
        results.append(response.status_code)

    threads = [threading.Thread(target=confirm, args=(key,)) for key in ("left", "right")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(results) == [202, 409]
    assert count(flow, ShippingRequest) == 1


def test_checkpoint_payloads_do_not_contain_login_or_tool_credentials(flow):
    from workbench.flow_models import GraphCheckpoint, GraphWrite

    run = start(flow)
    login_token = flow[2]()["Authorization"].split()[1].encode()
    with flow[1].state.database.sessions() as session:
        blobs = [row.payload for row in session.scalars(select(GraphCheckpoint)).all()]
        blobs += [row.payload for row in session.scalars(select(GraphWrite)).all()]
    assert blobs
    assert all(login_token not in blob and b"Bearer " not in blob for blob in blobs)
    assert run["output"]["engine"] == "langgraph"


def test_model_router_schema_and_timeout_are_fixed_without_real_network(flow, monkeypatch):
    import httpx
    from pydantic import SecretStr

    import workbench.intent_router as router

    observed = []

    def fake_post(url, **kwargs):
        observed.append(kwargs)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"intent":"lookup"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(router.httpx, "post", fake_post)
    settings = flow[1].state.settings
    settings.agent_router_mode = "model"
    settings.agent_router_url = "https://router.invalid/chat/completions"
    settings.agent_router_model = "test-model"
    settings.agent_router_key = SecretStr("test-secret")
    run = start(flow, query="查看订单", shipping_kind=None, reason=None)
    assert run["status"] == "completed"
    assert run["output"]["planner_mode"] == "model"
    assert len(observed) == 1
    assert observed[0]["timeout"] == 10 and observed[0]["trust_env"] is False
    assert observed[0]["json"]["max_tokens"] == 256
    assert "test-secret" not in str(run)


def test_model_read_order_is_executed_but_write_still_waits_for_confirm(flow, monkeypatch):
    import workbench.intent_router as router

    def plan(settings, query):
        return "shipping", {
            "prompt_tokens": 10,
            "completion_tokens": 8,
            "total_tokens": 18,
            "read_plan": {"tools": ["search_knowledge_base", "get_order_status"]},
        }

    monkeypatch.setattr(router, "model_intent", plan)
    flow[1].state.settings.agent_router_mode = "model"
    run = start(flow)
    assert run["status"] == "awaiting_confirmation"
    assert [name for name, _ in flow[3]][:2] == ["search_knowledge_base", "get_order_status"]
    assert count(flow, ShippingRequest) == 0
    assert run["output"]["tool_plan_mode"] == "model_validated"
    done = resume(flow, run, "confirm")
    assert done["status"] == "awaiting_approval"
    assert count(flow, ShippingRequest) == 1


def test_invalid_model_plan_falls_back_to_safe_reads(flow, monkeypatch):
    import workbench.intent_router as router

    def invalid(settings, query):
        return "shipping", {
            "prompt_tokens": 10,
            "completion_tokens": 8,
            "total_tokens": 18,
            "read_plan": {"tools": ["request_shipping"]},
        }

    monkeypatch.setattr(router, "model_intent", invalid)
    flow[1].state.settings.agent_router_mode = "model"
    run = start(flow)
    assert run["status"] == "awaiting_confirmation"
    assert run["output"]["tool_plan_mode"] == "rejected_fallback"
    assert count(flow, ShippingRequest) == 0
    assert [name for name, _ in flow[3]][:2] == ["get_order_status", "search_knowledge_base"]


def test_transient_read_recovers_with_bounded_attempts_and_public_event(flow, monkeypatch):
    from workbench.mcp_gateway import ToolFailure

    gateway = flow[1].state.gateway
    original = gateway.call
    attempts = []

    async def flaky(name, arguments, token):
        attempts.append(name)
        if name == "get_order_status" and attempts.count(name) == 1:
            raise ToolFailure("unavailable")
        return await original(name, arguments, token)

    monkeypatch.setattr(gateway, "call", flaky)
    run = start(flow)
    assert run["status"] == "awaiting_confirmation"
    assert attempts == ["get_order_status", "get_order_status", "search_knowledge_base"]
    assert run["output"]["execution"]["tool_attempts"] == 3
    events = flow[0].get("/api/v1/runs/" + run["id"] + "/events", headers=flow[2]()).text
    assert "tool.retry" in events
    assert count(flow, ShippingRequest) == 0


def test_exhausted_read_does_not_loop_or_claim_business_success(flow, monkeypatch):
    from workbench.mcp_gateway import ToolFailure

    attempts = []

    async def unavailable(name, arguments, token):
        attempts.append(name)
        raise ToolFailure("unavailable")

    monkeypatch.setattr(flow[1].state.gateway, "call", unavailable)
    run = start(flow)
    assert run["status"] == "interrupted"
    assert run["error_code"] == "unavailable"
    assert len(attempts) == 2
    assert run["output"]["write_in_flight"] is False
    assert count(flow, ShippingRequest) == 0


def test_tool_limit_stops_before_any_write_and_preserves_real_state(flow):
    flow[1].state.settings.agent_tool_limit = 1
    run = start(flow)
    assert run["status"] == "interrupted"
    assert run["error_code"] == "tool_call_limit"
    assert [name for name, _ in flow[3]] == ["get_order_status"]
    assert run["output"]["write_in_flight"] is False
    assert count(flow, ShippingRequest) == 0
