"""Actual HTTP MCP + persisted LangGraph. Vector/rerank are explicit test doubles."""

import uuid

import pytest
from sqlalchemy import select
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.knowledge_models import KnowledgeChunk


@pytest.fixture
def return_flow(return_case, scoped_mcp, monkeypatch):  # noqa: F811
    client, app, headers, body, policy = return_case
    _, _, _, gateway, index = scoped_mcp
    monkeypatch.setattr(app.state.gateway, "call", gateway.call)
    monkeypatch.setattr(
        "workbench.retrieval.rerank", lambda query, rows: [{**r, "rerank_score": 1.0} for r in rows]
    )
    with app.state.database.sessions() as session:
        chunk = session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == policy["version_id"])
        )
        index.upsert(
            chunk.organization_id,
            chunk.version_id,
            [{"id": chunk.id, "content": chunk.content}],
            lambda: None,
        )
    return return_case


def current(case, run_id):
    return case[0].get("/api/v1/runs/" + run_id, headers=case[2]()).json()


def resume(case, run, action, **changes):
    response = case[0].post(
        f"/api/v1/runs/{run['id']}/resume",
        headers={**case[2](), "Idempotency-Key": str(uuid.uuid4())},
        json={"action": action, "pause_id": run["output"].get("pause_id"), **changes},
    )
    assert response.status_code == 202, response.text
    return current(case, run["id"])


def start(case, **changes):
    body = case[3]
    response = case[0].post(
        "/api/v1/runs",
        headers=case[2](),
        json={
            "kind": "aftersales",
            "query": "DEMO return unused item actual receipt window",
            "order_number": body["order_number"],
            "return_item_id": body["item_id"],
            "return_policy_id": body["policy_id"],
            "return_quantity": body["quantity"],
            "return_condition": body["item_condition"],
            "reason": body["reason"],
            **changes,
        },
    )
    assert response.status_code == 202, response.text
    return current(case, response.json()["id"])


def test_return_graph_actual_mcp_pause_correction_approval_and_simulation(return_flow):
    case = return_flow
    run = start(case, return_condition="unknown")
    assert run["status"] == "awaiting_input", run
    run = resume(case, run, "supply", return_condition="unused")
    assert run["status"] == "awaiting_confirmation", run
    assert run["id"] not in {
        r["id"] for r in case[0].get("/api/v1/aftersales/runs", headers=case[2]()).json()["items"]
    }
    assert run["id"] in {
        r["id"]
        for r in case[0]
        .get("/api/v1/aftersales/runs?workflow=returns", headers=case[2]())
        .json()["items"]
    }
    assert not run["output"].get("request")
    run = resume(case, run, "correct", return_quantity=2)
    assert run["status"] == "awaiting_confirmation", run
    run = resume(case, run, "confirm")
    assert run["status"] == "awaiting_approval", run
    request = run["output"]["request"]
    assert request["quantity"] == 2
    response = case[0].post(
        f"/api/v1/returns/requests/{request['id']}/decision",
        headers=case[2]("approver"),
        json={"action": "approve", "reason": "reviewed", "expected_revision": request["revision"]},
    )
    assert response.status_code == 200, response.text
    # Read-only reconciliation bypasses tool quotas and returns the same business ID.
    verified = case[0].get(f"/api/v1/runs/{run['id']}/business-status", headers=case[2]()).json()
    assert verified["request"]["id"] == request["id"]
    assert verified["request"]["status"] == "approved"
    run = resume(case, run, "refresh")
    assert run["status"] == "awaiting_confirmation", run
    run = resume(case, run, "confirm")
    assert run["status"] == "completed", run
    assert run["output"]["request"]["status"] == "accepted_simulated"
    assert run["output"]["request"]["actual_refund_executed"] is False
    assert "模拟" in run["output"]["answer"]


def test_return_graph_empty_retrieval_stops_before_write(return_flow, monkeypatch):
    case = return_flow
    original = case[1].state.gateway.call
    searches = []

    async def empty(name, arguments, token):
        if name == "search_knowledge_base":
            searches.append(arguments)
            return {"status": "no_evidence", "matches": [], "trace": {}}
        return await original(name, arguments, token)

    monkeypatch.setattr(case[1].state.gateway, "call", empty)
    run = start(case)
    assert run["status"] == "completed", run
    assert len(searches) == 2  # One bounded supplemental search, no reflection loop.
    assert "未提交" in run["output"]["answer"]
    assert not run["output"].get("request")


def test_return_graph_declined_approval_is_not_success(return_flow):
    case = return_flow
    run = resume(case, start(case), "confirm")
    request = run["output"]["request"]
    response = case[0].post(
        f"/api/v1/returns/requests/{request['id']}/decision",
        headers=case[2]("approver"),
        json={"action": "reject", "reason": "needs manual review", "expected_revision": 1},
    )
    assert response.status_code == 200
    run = resume(case, run, "refresh")
    assert run["status"] == "completed"
    assert run["output"]["request"]["status"] == "rejected"
    assert run["output"]["request"]["receipt_id"] is None


def test_return_graph_lost_simulation_response_reconciles_without_duplicate(
    return_flow, monkeypatch
):
    from workbench.mcp_gateway import ToolFailure

    case = return_flow
    run = resume(case, start(case), "confirm")
    request = run["output"]["request"]
    response = case[0].post(
        f"/api/v1/returns/requests/{request['id']}/decision",
        headers=case[2]("approver"),
        json={"action": "approve", "reason": "reviewed", "expected_revision": 1},
    )
    assert response.status_code == 200
    run = resume(case, run, "refresh")
    original = case[1].state.gateway.call
    failed = False

    async def lose_receipt(name, arguments, token):
        nonlocal failed
        value = await original(name, arguments, token)
        if name == "submit_simulated_return" and not failed:
            failed = True
            raise ToolFailure("timeout")
        return value

    monkeypatch.setattr(case[1].state.gateway, "call", lose_receipt)
    run = resume(case, run, "confirm")
    assert run["status"] == "interrupted"
    verified = case[0].get(f"/api/v1/runs/{run['id']}/business-status", headers=case[2]()).json()
    assert verified["request"]["status"] == "accepted_simulated"
    run = resume(case, run, "retry")
    assert run["status"] == "completed", run
    assert run["output"]["request"]["id"] == request["id"]
    assert [e["action"] for e in run["output"]["request"]["events"]].count(
        "accepted_simulated"
    ) == 1


def test_return_graph_supplement_covers_separate_required_clauses(return_flow, monkeypatch):
    case = return_flow
    client, app, headers, body, policy = case
    from workbench.models import identifier

    refs = {}
    with app.state.database.sessions() as session:
        base = session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == policy["version_id"])
        )
        for position, purpose in enumerate(("window_clause", "condition_clause"), 1):
            chunk = KnowledgeChunk(
                id="separate-" + identifier(),
                organization_id=base.organization_id,
                version_id=base.version_id,
                position=position,
                content=policy[purpose]["quote"],
                section=purpose,
            )
            session.add(chunk)
            refs[purpose] = {"chunk_id": chunk.id, "quote": chunk.content}
        session.commit()
    registered = client.post(
        "/api/v1/returns/policies", headers=headers("admin"), json={**policy, **refs}
    )
    assert registered.status_code == 201
    body["policy_id"] = registered.json()["id"]
    all_sources = registered.json()["evidence"]["sources"]
    original = app.state.gateway.call
    searches = []

    async def fragments(name, arguments, token):
        if name == "search_knowledge_base":
            searches.append(arguments)
            sources = (
                [s for s in all_sources if s["id"] == policy["scope_clause"]["chunk_id"]]
                if len(searches) == 1
                else all_sources
            )
            return {"status": "found", "matches": sources, "trace": {"test_double": True}}
        return await original(name, arguments, token)

    monkeypatch.setattr(app.state.gateway, "call", fragments)
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    assert len(searches) == 2
    assert len(run["output"]["evidence"]) == 3
