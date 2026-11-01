"""Offline constrained decisions with real persistent graph and HTTP MCP reads."""

import json

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_return_graph import current, resume, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.knowledge_models import DocumentVersion, KnowledgeChunk
from workbench.models import identifier
from workbench.return_decisions import validate_decision
from workbench.source_reading import available_targets, inquiry_actions, required_ids, target


@pytest.fixture
def source_flow(return_flow, monkeypatch):  # noqa: F811
    client, app, headers, body, policy = return_flow
    settings = app.state.settings
    settings.return_source_reading_enabled = True
    with app.state.database.sessions() as session:
        version = session.get(DocumentVersion, policy["version_id"])
        first = session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == version.id)
        )
        first.content = first.content.replace("Item must be unused.", "See second section.")
        second = KnowledgeChunk(
            id=identifier(),
            organization_id=first.organization_id,
            version_id=version.id,
            position=1,
            content="Item must be unused.",
            section="conditions",
        )
        session.add(second)
        session.flush()
        version.parse_metadata = {
            "source_sha256": version.content_hash,
            "parent_child": {
                "children": {"0": "p0", "1": "p0"},
                "parents": [
                    {
                        "id": "p0",
                        "section": "returns",
                        "text": first.content + "\n\n" + second.content,
                        "positions": [0, 1],
                        "block_start": 0,
                        "block_end": 2,
                        "links": [],
                        "unresolved_articles": [],
                    }
                ],
            },
        }
        session.commit()
        second_id, first_id = second.id, first.id
    changed = {
        **policy,
        "condition_clause": {"chunk_id": second_id, "quote": "Item must be unused."},
    }
    response = client.post("/api/v1/returns/policies", headers=headers("admin"), json=changed)
    assert response.status_code == 201, response.text
    body = {**body, "policy_id": response.json()["id"]}
    calls = []
    actual = app.state.gateway.call

    async def call(name, arguments, token):
        calls.append((name, arguments))
        result = await actual(name, arguments, token)
        if name == "search_knowledge_base":
            result["matches"] = [r for r in result.get("matches", []) if r["id"] == first_id]
        return result

    monkeypatch.setattr(app.state.gateway, "call", call)
    response = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "return_source_reading": True,
            "return_retrieval_mode": "clause_bound",
            "return_flow_version": "guided_v3",
        },
    )
    assert response.status_code == 201, response.text
    assert (
        client.post(
            "/api/v1/runtime/config/versions/" + response.json()["id"] + "/publish",
            headers=headers("admin"),
            json={"expected_revision": 0},
        ).status_code
        == 200
    )
    return (client, app, headers, body, changed), calls


def test_read_missing_clause_then_ask_actual_condition(source_flow):
    case, calls = source_flow
    run = start(case, return_condition="unknown")
    assert run["status"] == "awaiting_input", run
    assert run["output"]["missing"] == ["return_condition"]
    assert sum(n == "read_policy_source" for n, _ in calls) == 1
    assert sum(n == "search_knowledge_base" for n, _ in calls) == 1
    run = resume(case, run, "supply", return_condition="unused")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["source_investigation"]["reads"] == 1
    assert not run["output"].get("request")


def test_read_result_can_reach_preview_without_extra_search(source_flow):
    case, calls = source_flow
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["retrieval"]["complete"]
    assert run["output"]["source_investigation"]["reads"] == 1
    assert not any(n in ("create_return_draft", "confirm_return_application") for n, _ in calls)


def test_read_only_fact_question_never_reads_to_invent_fact(source_flow):
    case, calls = source_flow
    run = start(case, query="只查实际退款金额，不要申请", return_condition="unknown")
    assert run["status"] == "completed", run
    assert not any(n == "read_policy_source" for n, _ in calls)
    assert run["output"]["fact_assessment"]["items"][0]["status"] == "unknown"
    assert not run["output"].get("request")


def test_current_original_window_revalidated_and_tamper_rejected(source_flow):
    case, _ = source_flow
    from fastapi import HTTPException

    from workbench.knowledge_schemas import ReadInput
    from workbench.knowledge_service import read_parent, validated_evidence
    from workbench.models import User
    from workbench.source_reading import reading_evidence

    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, case[4]["version_id"])
        actor = session.scalar(
            select(User).where(
                User.organization_id == version.organization_id, User.role == "support"
            )
        )
        body = ReadInput(version_id=version.id, parent_id="p0")
        artifact = reading_evidence(read_parent(session, actor, body), body)
        assert validated_evidence(session, actor, [artifact])[0]["content"] == artifact["content"]
        with pytest.raises(HTTPException):
            validated_evidence(session, actor, [{**artifact, "content": "Invented exception"}])
        from workbench.knowledge_models import Document

        session.get(Document, version.document_id).active_version_id = None
        session.flush()
        with pytest.raises(HTTPException):
            validated_evidence(session, actor, [artifact])


def test_correction_does_not_reset_read_or_supplement_budget(source_flow):
    case, calls = source_flow
    run = start(case)
    run = resume(case, run, "correct", return_quantity=2)
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["source_investigation"]["reads"] == 1
    run = resume(case, run, "correct", return_quantity=1)
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["source_investigation"]["reads"] == 1
    assert run["output"]["source_investigation"]["supplemental_searches"] == 0
    assert sum(n == "read_policy_source" for n, _ in calls) == 1


def test_illegal_target_and_write_actions_rejected():
    observation = {"allowed_actions": ["read_source"], "read_targets": [{"id": "allowed"}]}
    assert (
        validate_decision({"action": "read_source", "target_id": "allowed"}, observation)
        == "read_source"
    )
    for proposal in (
        {"action": "read_source", "target_id": "invented"},
        {"action": "read_source"},
        {"action": "approve"},
        {"action": "read_source", "target_id": "allowed", "path": "C:/secret"},
    ):
        with pytest.raises(ValueError):
            validate_decision(proposal, observation)


def test_exhausted_budget_stops_and_complete_parent_is_not_read():
    row = target("v", "p0", "terms")
    state = {
        "source_targets": [row],
        "source_read_count": 2,
        "order_read": True,
        "return_checked": True,
        "policy_searched": True,
        "source_search_count": 1,
        "preview": {"evidence": {"sources": [{"id": "missing"}]}},
    }
    assert inquiry_actions(state, ["supplement"]) == ["stop"]
    state["source_read_count"] = 0
    state["retrieved_evidence"] = [{"version_id": "v", "parent_id": "p0", "parent_complete": True}]
    assert available_targets(state) == []


def test_question_sources_do_not_require_whole_application():
    state = {
        "context_purpose": "policy_question",
        "query": "只想知道退货期限几天",
        "preview": {
            "evidence": {
                "clauses": [
                    {"purpose": "window_clause", "chunk_id": "window"},
                    {"purpose": "condition_clause", "chunk_id": "condition"},
                ]
            }
        },
    }
    assert required_ids(state) == {"window"}


def test_complete_evidence_skips_reading(source_flow, monkeypatch):
    case, calls = source_flow
    app = case[1]
    actual = app.state.gateway.call
    with app.state.database.sessions() as session:
        second = session.scalar(
            select(KnowledgeChunk).where(
                KnowledgeChunk.version_id == case[4]["version_id"], KnowledgeChunk.position == 1
            )
        )
        extra = {"id": second.id, "version_id": second.version_id, "content": second.content}

    async def all_sources(name, args, token):
        result = await actual(name, args, token)
        if name == "search_knowledge_base":
            result["matches"].append(extra)
        return result

    monkeypatch.setattr(app.state.gateway, "call", all_sources)
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["source_investigation"]["reads"] == 0
    assert not any(n == "read_policy_source" for n, _ in calls)


def test_explicit_related_section_read_without_repeating_complete_anchor(source_flow):
    case, calls = source_flow
    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, case[4]["version_id"])
        meta = dict(version.parse_metadata)
        p0 = meta["parent_child"]["parents"][0]
        first_text, second_text = p0["text"].split("\n\n")
        meta["parent_child"] = {
            "children": {"0": "p0", "1": "p1"},
            "parents": [
                {**p0, "text": first_text, "positions": [0], "links": ["p1"]},
                {
                    **p0,
                    "id": "p1",
                    "section": "明确关联的使用条件",
                    "text": second_text,
                    "positions": [1],
                    "links": [],
                },
            ],
        }
        version.parse_metadata = meta
        session.commit()
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    reads = [args for name, args in calls if name == "read_policy_source"]
    assert [r["parent_id"] for r in reads] == ["p1"]


def test_long_parent_reads_two_pages_then_stops_with_gap(source_flow):
    case, calls = source_flow
    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, case[4]["version_id"])
        meta = dict(version.parse_metadata)
        old = meta["parent_child"]["parents"][0]
        meta["parent_child"] = {
            **meta["parent_child"],
            "parents": [
                {
                    **old,
                    "text": old["text"].split("\n\n")[0]
                    + "\n"
                    + "背景说明。" * 1000
                    + "\nItem must be unused.",
                }
            ],
        }
        version.parse_metadata = meta
        session.commit()
    run = start(case)
    assert run["status"] == "completed", run
    reads = [args for name, args in calls if name == "read_policy_source"]
    assert [r["offset"] for r in reads] == [0, 2000]
    meta = run["output"]["source_investigation"]
    assert meta["reads"] == 2 and meta["chars"] == 4000
    assert not run["output"]["retrieval"]["complete"]
    assert run["output"]["retrieval"]["source_reading"]["stop_reason"] == "read_budget_exhausted"
    assert not run["output"].get("request")


def test_failed_read_has_no_automatic_retry(source_flow, monkeypatch):
    case, _ = source_flow
    app = case[1]
    actual = app.state.gateway.call
    from workbench.mcp_gateway import ToolFailure

    attempts = []

    async def failed(name, args, token):
        if name == "read_policy_source":
            attempts.append(args)
            raise ToolFailure("unavailable")
        return await actual(name, args, token)

    monkeypatch.setattr(app.state.gateway, "call", failed)
    run = start(case)
    assert run["status"] == "completed", run
    assert len(attempts) == 1
    assert run["output"]["source_investigation"]["stop_reason"] == "read_failed"
    assert not run["output"].get("request")


@pytest.mark.parametrize("invalid", [False, True])
def test_model_selects_only_allowlisted_target_or_falls_back(source_flow, monkeypatch, invalid):
    case, calls = source_flow
    client, app, headers, _, _ = case
    settings = app.state.settings
    settings.agent_router_mode = settings.return_decision_mode = "model"
    settings.agent_router_url = "https://model.invalid/chat/completions"
    settings.agent_router_model = "offline-only"
    settings.agent_router_key = SecretStr("offline-key")
    observations = []

    def model(settings, observation, **kwargs):
        observations.append(observation)
        action = observation["allowed_actions"][0]
        proposal = {"action": action}
        if action == "read_source":
            proposal["target_id"] = (
                "forged-target" if invalid else observation["read_targets"][0]["id"]
            )
        return {"proposal": json.dumps(proposal), "usage": {"total_tokens": 20}}

    monkeypatch.setattr("workbench.return_decisions.model_decision", model)
    version = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "return_source_reading": True,
            "return_retrieval_mode": "clause_bound",
            "return_decisions": "model",
            "model_alias": "server_default",
        },
    )
    assert version.status_code == 201, version.text
    assert (
        client.post(
            "/api/v1/runtime/config/versions/" + version.json()["id"] + "/publish",
            headers=headers("admin"),
            json={"expected_revision": 1},
        ).status_code
        == 200
    )
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    assert len(observations) == 1 and "read_targets" in observations[0]
    assert sum(n == "read_policy_source" for n, _ in calls) == 1
    assert "forged-target" not in json.dumps(run["output"])
    assert any(
        d["mode"] == ("model_invalid_or_unavailable_fallback" if invalid else "model")
        for d in run["output"]["decisions"]
    )


def test_server_must_authorize_source_mode(return_flow):  # noqa: F811
    client, _, headers, _, _ = return_flow
    response = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={"return_source_reading": True, "return_retrieval_mode": "clause_bound"},
    )
    assert response.status_code == 422


def test_same_fixture_baseline_supplement_vs_source_reading(source_flow):
    case, calls = source_flow
    client, _, headers, _, _ = case

    def configure(enabled, revision):
        response = client.post(
            "/api/v1/runtime/config/versions",
            headers=headers("admin"),
            json={"return_source_reading": enabled, "return_retrieval_mode": "clause_bound"},
        )
        assert response.status_code == 201
        assert (
            client.post(
                "/api/v1/runtime/config/versions/" + response.json()["id"] + "/publish",
                headers=headers("admin"),
                json={"expected_revision": revision},
            ).status_code
            == 200
        )

    configure(False, 1)
    baseline = start(case)
    baseline_calls = len(calls)
    configure(True, 2)
    reading = start(case)
    assert baseline["status"] == "completed" and not baseline["output"]["retrieval"]["complete"]
    assert (
        reading["status"] == "awaiting_confirmation" and reading["output"]["retrieval"]["complete"]
    )
    assert sum(n == "search_knowledge_base" for n, _ in calls[:baseline_calls]) == 2
    assert sum(n == "search_knowledge_base" for n, _ in calls[baseline_calls:]) == 1
    assert sum(n == "read_policy_source" for n, _ in calls[baseline_calls:]) == 1
    assert not baseline["output"].get("request") and not reading["output"].get("request")


def test_read_reservation_survives_checkpoint_loss(source_flow):
    case, _ = source_flow
    run = start(case)
    from workbench.flow_models import FlowAction
    from workbench.mcp_gateway import ToolFailure
    from workbench.models import Run
    from workbench.source_reading import reserve_source_io

    db = case[1].state.database
    with db.sessions() as session:
        session.get(Run, run["id"]).status = "running"
        session.commit()
    reserve_source_io(db, run["id"], "reads", lambda session, run: None)
    with pytest.raises(ToolFailure, match="source_budget_exhausted"):
        reserve_source_io(db, run["id"], "reads", lambda session, run: None)
    with db.sessions() as session:
        assert session.get(FlowAction, (run["id"], "__source_budget__")).body["reads"] == 2


def test_missing_fact_does_not_suppress_separate_policy_question():
    state = {
        "context_purpose": "policy_question",
        "query": "只想知道实际退款金额和退货期限几天",
        "preview": {
            "evidence": {
                "clauses": [
                    {"purpose": "window_clause", "chunk_id": "window"},
                    {"purpose": "condition_clause", "chunk_id": "condition"},
                ]
            }
        },
    }
    assert required_ids(state) == {"window"}
