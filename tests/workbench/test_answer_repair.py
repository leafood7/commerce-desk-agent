"""Bounded repair fault injection: real API/SQL, explicit model and retrieval doubles."""

# ruff: noqa: F401,F811
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_answer_gate import setup_gate
from test_policy_context_purpose import purpose_flow
from test_return_graph import return_flow, start
from test_returns import return_case
from test_scoped_mcp import scoped_mcp

from workbench.flow_models import FlowAction
from workbench.models import Message, Run, RunEvent
from workbench.quota_models import RunUsage


def execute(case, question="普通商品退货规则"):
    client, app, headers, _ = case
    response = client.post(
        "/api/v1/runs", headers=headers(), json={"kind": "knowledge_search", "query": question}
    )
    assert response.status_code == 202, response.text
    run = client.get("/api/v1/runs/" + response.json()["id"], headers=headers()).json()
    return run


def adapter(monkeypatch, scenario="fixed", captured=None):
    calls = []

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        if captured is not None:
            captured["context"] = context
        if phase == "repair" and scenario == "timeout":
            raise TimeoutError("injected repair timeout")
        source = context["allowed_evidence"][0]
        if phase in {"generate", "repair"}:
            data = {
                "claims": [
                    {
                        "text": source["text"]
                        if phase == "repair" or scenario == "valid"
                        else "PRIVATE_BAD_CLAIM 任意时间都能退",
                        "evidence_ids": [source["id"]],
                    }
                ]
            }
            if phase == "generate" and scenario == "invalid_reference":
                data["claims"][0]["evidence_ids"] = ["made-up-source"]
            if phase == "repair" and scenario == "repair_invalid":
                data["claims"][0]["evidence_ids"] = ["made-up-repair-source"]
                data["claims"][0]["text"] = "PRIVATE_BAD_REPAIR"
        else:
            ok = scenario == "valid" or (phase == "review_repair" and scenario != "repair_rejected")
            data = {"approved": ok, "codes": [] if ok else ["condition_strengthening"]}
        return {
            "raw": json.dumps(data),
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            "response_model": "explicit-fault-injection",
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    return calls


@pytest.mark.parametrize(
    "mode,expected", [("contract", 1), ("reviewed", 2), ("reviewed_repair", 4)]
)
def test_modes_keep_distinct_behavior_without_unbounded_calls(
    logged_in, workbench, monkeypatch, mode, expected
):
    case = setup_gate(logged_in, workbench, monkeypatch, mode)
    calls = adapter(monkeypatch)
    run = execute(case)
    assert len(calls) == expected
    if mode == "reviewed_repair":
        assert calls == ["generate", "review", "repair", "review_repair"]
        assert run["output"]["answer_gate"]["repair"]["outcome"] == "approved"
        assert "PRIVATE_BAD_CLAIM" not in run["output"]["answer"]
    elif mode == "reviewed":
        assert run["output"]["answer_gate"]["status"] == "blocked"


def test_valid_answer_makes_no_repair_call(logged_in, workbench, monkeypatch):
    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    calls = adapter(monkeypatch, "valid")
    run = execute(case)
    assert calls == ["generate", "review"]
    assert not run["output"]["answer_gate"]["repair"]["attempted"]


@pytest.mark.parametrize(
    "scenario,expected_calls,status",
    [
        ("invalid_reference", 1, "invalid_contract_or_review"),
        ("repair_invalid", 3, "repair_invalid"),
        ("repair_rejected", 4, "repair_rejected"),
        ("timeout", 3, "repair_unavailable"),
    ],
)
def test_failed_or_invalid_repairs_stop_without_another_round(
    logged_in, workbench, monkeypatch, scenario, expected_calls, status
):
    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    calls = adapter(monkeypatch, scenario)
    run = execute(case)
    assert len(calls) == expected_calls
    assert run["output"]["answer_gate"]["status"] == status
    assert "PRIVATE_BAD" not in run["output"]["answer"]
    if scenario == "timeout":
        with case[1].state.database.sessions() as session:
            assert session.get(RunUsage, run["id"]).unknown_model_attempts == 1


def test_missing_reference_is_not_repaired_by_generation(logged_in, workbench, monkeypatch):
    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    actual = case[1].state.gateway.call

    async def gap(name, args, token):
        result = await actual(name, args, token)
        result["trace"] = {
            **result.get("trace", {}),
            "reference_gaps": {"unresolved_article_references": 1},
        }
        return result

    monkeypatch.setattr(case[1].state.gateway, "call", gap)
    calls = []

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        data = (
            {
                "claims": [
                    {
                        "text": context["allowed_evidence"][0]["text"],
                        "evidence_ids": [context["allowed_evidence"][0]["id"]],
                    }
                ],
                "cannot_confirm": True,
            }
            if phase == "generate"
            else {"approved": False, "codes": ["missing_exception"]}
        )
        return {"raw": json.dumps(data), "usage": {"total_tokens": 20}}

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    run = execute(case)
    assert calls == ["generate", "review"]
    assert run["output"]["answer_gate"]["status"] == "blocked_evidence_gap"
    assert not run["output"]["answer_gate"]["repair"]["attempted"]


def test_repair_cache_and_once_marker_survive_worker_reentry(logged_in, workbench, monkeypatch):
    from workbench.answer_gate import produce_answer, reserve_repair_once
    from workbench.execution_limits import ExecutionLimits
    from workbench.mcp_gateway import ToolFailure

    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    captured = {}
    calls = adapter(monkeypatch, captured=captured)
    run = execute(case)
    database = case[1].state.database
    with database.sessions() as session:
        record = session.get(Run, run["id"])
        record.status = (
            "running"  # Explicit worker-reentry fixture, not a public terminal transition.
        )
        assert session.get(FlowAction, (run["id"], "__answer_repair_once__"))
        session.commit()
    result = produce_answer(
        database,
        run["id"],
        case[1].state.settings,
        captured["context"],
        ExecutionLimits(case[1].state.settings),
        lambda: None,
    )
    assert result["answer_gate"]["status"] == "approved"
    assert len(calls) == 4
    with pytest.raises(ToolFailure, match="repair_already_consumed"):
        reserve_repair_once(
            database, run["id"], {"changed": True}, {}, ["unsupported_claim"], lambda: None
        )


def test_original_and_repaired_drafts_never_appear_in_public_records(
    logged_in, workbench, monkeypatch
):
    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    adapter(monkeypatch, "repair_invalid")
    run = execute(case)
    client, app, headers, _ = case
    for path in [
        "/api/v1/runs/" + run["id"],
        "/api/v1/runs/" + run["id"] + "/events",
        "/api/v1/conversations/" + run["conversation_id"] + "/messages",
    ]:
        result = client.get(path, headers=headers())
        assert result.status_code == 200
        assert "PRIVATE_BAD" not in result.text
    with app.state.database.sessions() as session:
        actions = session.scalars(select(FlowAction).where(FlowAction.run_id == run["id"])).all()
        assert any("PRIVATE_BAD" in json.dumps(row.body) for row in actions)
        assert all(
            "PRIVATE_BAD" not in json.dumps(row.payload)
            for row in session.scalars(select(RunEvent).where(RunEvent.run_id == run["id"]))
        )
        assert all(
            "PRIVATE_BAD" not in row.content
            for row in session.scalars(select(Message).where(Message.run_id == run["id"]))
        )


def test_server_does_not_allow_profile_to_enable_repair(logged_in, workbench, monkeypatch):
    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed")
    response = case[0].post(
        "/api/v1/runtime/config/versions",
        headers=case[2]("admin"),
        json={"model_alias": "server_default", "policy_answer_mode": "reviewed_repair"},
    )
    assert response.status_code == 422


def test_quota_failure_does_not_dispatch_repair(logged_in, workbench, monkeypatch):
    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    calls = []

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        if phase == "generate":
            data = {
                "claims": [
                    {"text": "PRIVATE_BAD", "evidence_ids": [context["allowed_evidence"][0]["id"]]}
                ]
            }
        else:
            data = {"approved": False, "codes": ["unsupported_claim"]}
            with case[1].state.database.sessions() as session:
                row = session.scalar(select(RunUsage).join(Run).where(Run.status == "running"))
                row.max_model_attempts = 2
                session.commit()
        return {"raw": json.dumps(data), "usage": {"total_tokens": 20}}

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    run = execute(case)
    assert calls == ["generate", "review"]
    assert run["output"]["answer_gate"]["status"] == "repair_unavailable"


def test_cancellation_during_repair_keeps_draft_private(logged_in, workbench, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    case = setup_gate(logged_in, workbench, monkeypatch, "reviewed_repair")
    client, app, headers, _ = case
    entered, release = threading.Event(), threading.Event()
    calls = []

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        if phase == "repair":
            entered.set()
            assert release.wait(15)
        data = (
            {"approved": False, "codes": ["unsupported_claim"]}
            if phase == "review"
            else {
                "claims": [
                    {
                        "text": "PRIVATE_BAD_CANCEL_DRAFT",
                        "evidence_ids": [context["allowed_evidence"][0]["id"]],
                    }
                ]
            }
        )
        return {"raw": json.dumps(data), "usage": {"total_tokens": 20}}

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute, case)
        try:
            assert entered.wait(15)
            with app.state.database.sessions() as session:
                run = session.scalar(select(Run).where(Run.status == "running"))
                identifier, conversation = run.id, run.conversation_id
                assert run.output is None
                assert all(
                    "PRIVATE_BAD" not in json.dumps(r.payload)
                    for r in session.scalars(select(RunEvent).where(RunEvent.run_id == identifier))
                )
            assert (
                "PRIVATE_BAD"
                not in client.get("/api/v1/runs/" + identifier, headers=headers()).text
            )
            response = client.post(
                "/api/v1/runs/" + identifier + "/cancel", headers=headers(), json={}
            )
            assert response.status_code == 200
        finally:
            release.set()
        done = pending.result(timeout=20)
    assert done["status"] == "cancelled"
    assert calls == ["generate", "review", "repair"]
    assert (
        "PRIVATE_BAD"
        not in client.get(
            "/api/v1/conversations/" + conversation + "/messages", headers=headers()
        ).text
    )


def test_explicit_task_constraint_can_be_repaired_without_reviewing_invalid_initial_task(
    purpose_flow, monkeypatch
):
    from pydantic import SecretStr

    client, app, headers, _, _ = purpose_flow
    app.state.settings.policy_answer_mode = "reviewed_repair"
    app.state.settings.agent_router_mode = "model"
    app.state.settings.agent_router_url = "https://api.deepseek.com/chat/completions"
    app.state.settings.agent_router_model = "deepseek-flash"
    app.state.settings.agent_router_key = SecretStr("test-only-no-dispatch")
    head = client.get("/api/v1/runtime/config", headers=headers("admin")).json()
    draft = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "model_alias": "server_default",
            "policy_answer_mode": "reviewed_repair",
            "return_flow_version": "guided_v3",
            "return_retrieval_mode": "clause_bound",
        },
    )
    assert draft.status_code == 201, draft.text
    client.post(
        "/api/v1/runtime/config/versions/" + draft.json()["id"] + "/publish",
        headers=headers("admin"),
        json={"expected_revision": head["revision"]},
    ).raise_for_status()
    calls = []

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        if phase in {"generate", "repair"}:
            data = {
                "claims": [
                    {
                        "text": context["allowed_evidence"][0]["text"],
                        "evidence_ids": [context["allowed_evidence"][0]["id"]],
                    }
                ]
            }
            if phase == "generate":
                data["missing_conditions"] = ["PRIVATE_BAD_APPLICATION_CHECKLIST"]
        else:
            data = {"approved": True, "codes": []}
        return {"raw": json.dumps(data), "usage": {"total_tokens": 20}}

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    run = start(
        purpose_flow, query="只查照片能否代替缺陷确认，不申请。", return_condition="unknown"
    )
    assert run["status"] == "completed", run
    assert calls == ["generate", "repair", "review_repair"]
    assert run["output"]["answer_gate"]["repair"]["outcome"] == "approved"
    assert "PRIVATE_BAD" not in json.dumps(run["output"])
