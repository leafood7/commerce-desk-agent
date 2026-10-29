"""Admission and usage constraints. Model calls are explicit substitutes, never paid."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import test_run_dispatch as dispatch_cases
from fastapi.testclient import TestClient
from sqlalchemy import select

from workbench.app import create_app
from workbench.flow_models import FlowAction
from workbench.job_models import RunJob
from workbench.mcp_gateway import ToolFailure
from workbench.models import Message, Run, RunEvent, ShippingRequest
from workbench.quota_models import RunUsage
from workbench.quotas import reserve_tool

dispatch_fixture = dispatch_cases.dispatch_fixture
queued = dispatch_cases.queued
start = dispatch_cases.start
resume = dispatch_cases.resume
current = dispatch_cases.current
drain = dispatch_cases.drain
count = dispatch_cases.count


def usage(case, run):
    response = case[0].get("/api/v1/runs/" + run["id"] + "/usage", headers=case[2]())
    assert response.status_code == 200, response.text
    return response.json()


def test_owner_backpressure_has_no_partial_run_and_idempotence_survives_full_queue(queued):
    client, app, headers, _ = queued
    app.state.settings.queue_owner_limit = 1
    auth = {**headers(), "Idempotency-Key": "accepted-at-capacity"}
    body = {"order_number": "A1001"}
    first = client.post("/api/v1/runs", headers=auth, json=body)
    assert first.status_code == 202
    duplicate = client.post("/api/v1/runs", headers=auth, json=body)
    assert duplicate.status_code == 202 and duplicate.json()["id"] == first.json()["id"]
    denied = client.post("/api/v1/runs", headers=headers(), json=body)
    assert denied.status_code == 429 and denied.headers["Retry-After"] == "2"
    for model in (Run, RunJob, Message, RunUsage):
        assert count(queued, model) == 1
    client.post("/api/v1/runs/" + first.json()["id"] + "/cancel", headers=headers())
    assert count(queued, RunJob) == 1
    with app.state.database.sessions() as session:
        assert session.scalar(select(RunJob.status)) == "skipped"
    assert client.post("/api/v1/runs", headers=headers(), json=body).status_code == 202


def test_global_backpressure_covers_other_organizations_without_exposing_counts(queued):
    client, app, headers, _ = queued
    app.state.settings.queue_global_limit = 1
    first = client.post("/api/v1/runs", headers=headers(), json={"order_number": "A1001"})
    assert first.status_code == 202
    foreign = headers("support", "demo-b")
    denied = client.post("/api/v1/runs", headers=foreign, json={"order_number": "B2001"})
    assert denied.status_code == 429 and set(denied.json()) == {"detail"}
    client.post("/api/v1/runs/" + first.json()["id"] + "/cancel", headers=headers())
    assert (
        client.post("/api/v1/runs", headers=foreign, json={"order_number": "B2001"}).status_code
        == 202
    )


def test_waiting_does_not_hold_slot_and_cancel_works_while_queue_full(queued):
    client, app, headers, _ = queued
    app.state.settings.queue_owner_limit = 1
    run = start(queued)
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "awaiting_confirmation"
    waiting = client.post("/api/v1/runs", headers=headers(), json={"order_number": "A1001"})
    assert waiting.status_code == 202
    key = "not-admitted-confirmation"
    denied = client.post(
        "/api/v1/runs/" + run["id"] + "/resume",
        headers={**headers(), "Idempotency-Key": key},
        json={"action": "confirm", "pause_id": run["output"]["pause_id"]},
    )
    assert denied.status_code == 429
    assert current(queued, run["id"])["status"] == "awaiting_confirmation"
    with app.state.database.sessions() as session:
        assert session.get(FlowAction, (run["id"], key)) is None
    before = count(queued, RunJob)
    cancelled = resume(queued, run, "cancel", key="cancel-without-slot")
    assert cancelled["status"] == "cancelled"
    assert not cancelled["output"].get("proposal") and not cancelled["output"].get("evidence")
    assert count(queued, RunJob) == before and count(queued, ShippingRequest) == 0
    repeated = resume(queued, run, "cancel", key="cancel-without-slot")
    assert repeated["status"] == "cancelled" and count(queued, RunJob) == before


def test_cumulative_tool_budget_is_not_reset_by_resume_or_configuration_change(queued):
    settings = queued[1].state.settings
    settings.run_tool_limit = 2
    run = start(queued)
    drain(queued)
    run = current(queued, run["id"])
    assert usage(queued, run)["tool_attempts"] == 2
    settings.run_tool_limit = 100
    resume(queued, run, "confirm")
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "interrupted" and run["error_code"] == "run_tool_quota"
    before = len(queued[3])
    resume(queued, run, "retry")
    drain(queued)
    assert len(queued[3]) == before
    data = usage(queued, run)
    assert data["tool_attempts"] == data["max_tools"] == 2 and data["remaining_tools"] == 0
    assert count(queued, ShippingRequest) == 0


def test_failed_read_and_retry_both_consume_cumulative_budget(queued, monkeypatch):
    queued[1].state.settings.run_tool_limit = 2
    original = queued[1].state.gateway.call
    calls = []

    async def flaky(name, arguments, token):
        calls.append(name)
        if len(calls) == 1:
            raise ToolFailure("unavailable")
        return await original(name, arguments, token)

    monkeypatch.setattr(queued[1].state.gateway, "call", flaky)
    run = start(queued)
    drain(queued)
    assert calls == ["get_order_status", "get_order_status"]
    assert usage(queued, run)["tool_attempts"] == 2
    assert current(queued, run["id"])["error_code"] == "run_tool_quota"


def test_known_model_usage_settles_and_is_owner_scoped(queued, monkeypatch):
    settings = queued[1].state.settings
    settings.agent_router_mode = "model"
    calls = []

    def model(settings, query):
        calls.append(query)
        return "shipping", {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}

    monkeypatch.setattr("workbench.intent_router.model_intent", model)
    run = start(queued)
    drain(queued)
    run = current(queued, run["id"])
    data = usage(queued, run)
    assert data["model_attempts"] == 1 and data["model_observed_tokens"] == 16
    assert data["model_reserved_tokens"] == data["unknown_model_attempts"] == 0
    assert data["legacy_estimate"] is False and data["cost_cny"] is None
    resume(queued, run, "confirm")
    drain(queued)
    assert len(calls) == 1 and usage(queued, run)["model_observed_tokens"] == 16
    assert (
        queued[0]
        .get("/api/v1/runs/" + run["id"] + "/usage", headers=queued[2]("admin"))
        .status_code
        == 404
    )
    assert queued[0].get("/api/v1/runs/" + run["id"] + "/usage").status_code == 401


def test_unknown_model_usage_remains_reserved_across_restart(queued, monkeypatch):
    queued[1].state.settings.agent_router_mode = "model"
    calls = []

    def timeout(*args):
        calls.append(True)
        raise TimeoutError("explicit model substitute")

    monkeypatch.setattr("workbench.intent_router.model_intent", timeout)
    run = start(queued)
    drain(queued)
    data = usage(queued, run)
    assert data["model_reserved_tokens"] == 8192 and data["unknown_model_attempts"] == 1
    assert data["model_observed_tokens"] == 0 and data["cost_cny"] is None
    restarted = create_app(queued[1].state.settings, gateway=queued[1].state.gateway)
    with TestClient(restarted) as client:
        again = client.get("/api/v1/runs/" + run["id"] + "/usage", headers=queued[2]()).json()
        assert again == data
    resume(queued, current(queued, run["id"]), "confirm")
    drain(queued)
    assert len(calls) == 1 and usage(queued, run)["model_reserved_tokens"] == 8192


def test_model_reservation_limit_falls_back_before_model_call(queued, monkeypatch):
    queued[1].state.settings.agent_router_mode = "model"
    queued[1].state.settings.run_model_token_limit = 256
    calls = []
    monkeypatch.setattr("workbench.intent_router.model_intent", lambda *args: calls.append(True))
    run = start(queued)
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "awaiting_confirmation"
    assert run["output"]["planner_mode"] == "model_quota_fallback" and calls == []
    data = usage(queued, run)
    assert data["model_attempts"] == data["model_reserved_tokens"] == 0


def test_provider_overrun_is_recorded_not_hidden_by_clamping(queued, monkeypatch):
    queued[1].state.settings.agent_router_mode = "model"
    monkeypatch.setattr(
        "workbench.intent_router.model_intent",
        lambda *args: (
            "shipping",
            {"prompt_tokens": 19999, "completion_tokens": 1, "total_tokens": 20000},
        ),
    )
    run = start(queued)
    drain(queued)
    data = usage(queued, run)
    assert data["model_observed_tokens"] == 20000 and data["remaining_model_tokens"] == 0
    assert current(queued, run["id"])["output"]["planner_mode"] == "model_usage_overrun_fallback"


def test_legacy_usage_is_conservative_and_imported_only_once(queued):
    run = start(queued)
    database = queued[1].state.database
    with database.sessions() as session:
        session.delete(session.get(RunUsage, run["id"]))
        for kind in ("tool.started", "tool.retry"):
            session.add(
                RunEvent(
                    organization_id=run["input"].get("organization_id")
                    or session.get(Run, run["id"]).organization_id,
                    run_id=run["id"],
                    type=kind,
                    payload={},
                )
            )
        session.add(
            FlowAction(
                run_id=run["id"],
                key="__intent_router__",
                input_hash="old",
                body={"status": "reserved"},
            )
        )
        session.commit()
    data = usage(queued, run)
    assert data["legacy_estimate"] is True and data["tool_attempts"] == 2
    assert data["model_attempts"] == data["unknown_model_attempts"] == 1
    assert data["model_reserved_tokens"] == 8192
    assert usage(queued, run) == data


def test_mysql_concurrent_admission_cannot_bypass_owner_limit(queued, monkeypatch):
    database = queued[1].state.database
    if database.engine.dialect.name != "mysql":
        pytest.skip("Row-lock admission needs real MySQL")
    queued[1].state.settings.queue_owner_limit = 1
    auth = queued[2]()
    barrier = Barrier(2)
    from workbench.quotas import acquire_admission

    authenticated = Barrier(2)

    def after_authentication(session):
        # Both MySQL auth snapshots exist before either request acquires admission.
        authenticated.wait()
        acquire_admission(session)

    monkeypatch.setattr("workbench.quotas.acquire_admission", after_authentication)

    def create(index):
        barrier.wait()
        return (
            queued[0]
            .post(
                "/api/v1/runs",
                headers={**auth, "Idempotency-Key": "race-" + str(index)},
                json={"order_number": "A1001"},
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sorted(results) == [202, 429]
    assert count(queued, Run) == count(queued, RunJob) == count(queued, RunUsage) == 1


def test_mysql_tool_reservation_cannot_overspend_concurrently(queued):
    database = queued[1].state.database
    if database.engine.dialect.name != "mysql":
        pytest.skip("Row-lock reservation needs real MySQL")
    queued[1].state.settings.run_tool_limit = 1
    run = start(queued)
    with database.sessions() as session:
        session.get(Run, run["id"]).status = "running"
        session.commit()
    barrier = Barrier(2)

    def reserve(index):
        barrier.wait()
        try:
            reserve_tool(database, run["id"])
            return "reserved"
        except ToolFailure as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sorted(results) == ["reserved", "run_tool_quota"]
    assert usage(queued, run)["tool_attempts"] == 1


def test_exhausted_budget_still_allows_read_only_uncertain_business_reconciliation(
    queued, monkeypatch
):
    queued[1].state.settings.run_tool_limit = 4
    original = queued[1].state.gateway.call

    async def uncertain(name, arguments, token):
        result = await original(name, arguments, token)
        if name == "request_shipping":
            raise ToolFailure("timeout")
        return result

    monkeypatch.setattr(queued[1].state.gateway, "call", uncertain)
    run = start(queued)
    drain(queued)
    resume(queued, current(queued, run["id"]), "confirm")
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "interrupted" and run["output"]["write_in_flight"]
    assert usage(queued, run)["remaining_tools"] == 0 and count(queued, ShippingRequest) == 1
    calls = len(queued[3])
    response = queued[0].get("/api/v1/runs/" + run["id"] + "/business-status", headers=queued[2]())
    assert response.status_code == 200
    assert response.json()["lookup_status"] == "found"
    assert response.json()["request"]["status"] == "pending"
    assert len(queued[3]) == calls and usage(queued, run)["tool_attempts"] == 4
    assert count(queued, ShippingRequest) == 1
    assert (
        queued[0]
        .get("/api/v1/runs/" + run["id"] + "/business-status", headers=queued[2]("admin"))
        .status_code
        == 404
    )
    assert (
        queued[0]
        .post(
            "/api/v1/runs/" + run["id"] + "/usage", headers=queued[2](), json={"tool_attempts": 0}
        )
        .status_code
        == 405
    )


def test_not_observed_business_status_does_not_assert_cancellation(queued):
    run = start(queued)
    response = queued[0].get("/api/v1/runs/" + run["id"] + "/business-status", headers=queued[2]())
    assert response.status_code == 200 and response.json()["lookup_status"] == "not_observed"
    assert response.json()["request"] is None
    assert current(queued, run["id"])["status"] == "queued"


def test_router_preflight_can_reject_before_any_network_request(monkeypatch):
    from pydantic import SecretStr

    from workbench.config import WorkbenchSettings
    from workbench.intent_router import model_intent

    settings = WorkbenchSettings(
        run_model_reserve_tokens=256,
        agent_router_model="test-model",
        agent_router_url="https://test.invalid/chat",
        agent_router_key=SecretStr("explicit-test-secret"),
    )
    calls = []
    monkeypatch.setattr(
        "workbench.intent_router.httpx.post", lambda *args, **kwargs: calls.append(True)
    )
    with pytest.raises(ValueError, match="reservation allowance"):
        model_intent(settings, "only a local preflight check")
    assert calls == []


def test_known_preflight_rejection_releases_tokens_without_claiming_a_provider_call(
    queued, monkeypatch
):
    settings = queued[1].state.settings
    settings.agent_router_mode = "model"
    settings.agent_router_model = "local-substitute"
    settings.run_model_reserve_tokens = 256
    calls = []
    monkeypatch.setattr(
        "workbench.intent_router.httpx.post", lambda *args, **kwargs: calls.append(True)
    )
    run = start(queued)
    drain(queued)
    data = usage(queued, run)
    assert calls == [] and data["model_attempts"] == 1
    assert data["model_reserved_tokens"] == data["unknown_model_attempts"] == 0
    assert current(queued, run["id"])["output"]["planner_mode"] == "model_preflight_fallback"


def test_admission_refuses_to_commit_unrelated_pending_mutations(queued):
    from workbench.models import Order
    from workbench.runs import create_run
    from workbench.schemas import RunInput

    database = queued[1].state.database
    auth = queued[2]()
    with database.sessions() as session:
        from workbench.security import authenticate

        actor = authenticate(session, auth["Authorization"].split()[1])
        order = session.scalar(
            select(Order).where(
                Order.organization_id == actor.organization_id, Order.number == "A1001"
            )
        )
        original = order.payment_status
        order.payment_status = "unpaid"
        with pytest.raises(RuntimeError, match="read-only request session"):
            create_run(session, actor, RunInput(order_number="A1001"), settings=database.settings)
        session.rollback()
        assert session.get(Order, order.id).payment_status == original
    assert count(queued, Run) == 0
