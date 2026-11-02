"""Actual HTTP MCP and SQL role graph; generation/decision doubles are explicit."""

# ruff: noqa: F401,F811
import json

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_return_graph import return_flow
from test_return_inquiry_v2 import publish
from test_returns import return_case
from test_scoped_mcp import scoped_mcp
from test_source_reading import source_flow

from workbench.checkpoints import SQLCheckpointSaver
from workbench.flow_models import GraphCheckpoint
from workbench.knowledge_collaboration import RetrievalRole
from workbench.mcp_gateway import ToolFailure
from workbench.models import RunEvent


@pytest.fixture
def crew(source_flow, monkeypatch):
    case, tools = source_flow
    settings = case[1].state.settings
    settings.agent_router_mode = "model"
    settings.agent_router_url = "https://api.deepseek.com/chat/completions"
    settings.agent_router_model = "deepseek-flash"
    settings.agent_router_key = SecretStr("explicit-test-double")
    settings.return_decision_mode = "model"
    settings.policy_answer_mode = "reviewed_repair"
    calls = []

    def decision(settings, observation, **kwargs):
        calls.append("decision")
        return {
            "proposal": json.dumps(
                {"action": "read_source", "target_id": observation["read_targets"][0]["id"]}
            ),
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            "response_model": "explicit-double",
        }

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        source = context["allowed_evidence"][-1]
        body = (
            {"claims": [{"text": source["text"], "evidence_ids": [source["id"]]}]}
            if phase in {"generate", "repair"}
            else {"approved": True, "codes": []}
        )
        return {
            "raw": json.dumps(body),
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
            "response_model": "explicit-double",
        }

    monkeypatch.setattr("workbench.return_decisions.model_decision", decision)
    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    publish(
        case,
        model_alias="server_default",
        return_decisions="model",
        return_source_reading=True,
        policy_answer_mode="reviewed_repair",
        knowledge_agent_mode="collaborative",
    )
    return case, tools, calls


def execute(case):
    client, _, headers, _, _ = case
    response = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={
            "kind": "knowledge_search",
            "query": "DEMO return unused item actual receipt window",
            "policy_scope": "returns",
        },
    )
    assert response.status_code == 202, response.text
    return client.get("/api/v1/runs/" + response.json()["id"], headers=headers()).json()


def test_roles_read_actual_source_and_only_publish_reviewed_result(crew):
    case, tools, calls = crew
    run = execute(case)
    assert run["status"] == "completed", run
    assert run["output"]["answer_gate"]["status"] == "approved", run
    assert run["output"]["answer_gate"]["agent_mode"] == "collaborative", run
    assert calls == ["decision", "generate", "review"], calls
    assert sum(name == "read_policy_source" for name, _ in tools) == 1
    assert not set(name for name, _ in tools) & {
        "create_return_draft",
        "confirm_return_application",
    }
    with case[1].state.database.sessions() as session:
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == run["id"])).all()
        assert {e.payload.get("agent") for e in events if e.type == "agent.started"} == {
            "retrieval_agent",
            "answer_agent",
            "review_agent",
        }
        assert session.scalar(
            select(GraphCheckpoint).where(
                GraphCheckpoint.run_id == run["id"],
                GraphCheckpoint.namespace == "knowledge_roles_v1",
            )
        )
    public = json.dumps(run)
    assert "context_digest" not in public
    assert "allowed_evidence" not in public


def test_failed_reviewer_does_not_publish_private_draft(crew, monkeypatch):
    case, _, calls = crew

    def broken(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        if phase == "review":
            raise TimeoutError("injected")
        return {
            "raw": json.dumps(
                {
                    "claims": [
                        {
                            "text": "PRIVATE-CREW-DRAFT",
                            "evidence_ids": [context["allowed_evidence"][0]["id"]],
                        }
                    ]
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", broken)
    run = execute(case)
    assert run["status"] == "completed", run
    assert run["output"]["answer_gate"]["status"] == "collaboration_unavailable", run
    assert "PRIVATE-CREW-DRAFT" not in json.dumps(run)
    response = case[0].get("/api/v1/runs/" + run["id"] + "/events", headers=case[2]())
    assert "PRIVATE-CREW-DRAFT" not in response.text


def test_namespace_cannot_cross_read_default_graph(crew):
    case, _, _ = crew
    run = execute(case)
    db = case[1].state.database
    default = SQLCheckpointSaver(db, run["id"])
    private = SQLCheckpointSaver(db, run["id"], namespace="knowledge_roles_v1")
    config = {"configurable": {"thread_id": private.thread_scope}}
    assert private.get_tuple(config)
    with pytest.raises(ValueError):
        default.get_tuple(config)
    assert default.get_tuple({"configurable": {"thread_id": run["id"]}}) is None
    with pytest.raises(ValueError):
        SQLCheckpointSaver(db, run["id"], namespace="arbitrary")


def test_role_whitelist_rejects_business_write_before_io():
    reader = RetrievalRole(None, None, None, None, None, None, None, None, None)
    with pytest.raises(ToolFailure) as error:
        reader.tool("create_return_draft", {})
    assert error.value.code == "role_tool_forbidden"


def test_completed_role_graph_reuses_verified_handoff_without_more_io(crew, monkeypatch):
    case, tools, calls = crew
    from workbench import knowledge_collaboration as module

    actual = module.run_collaboration

    def twice(*args, **kwargs):
        first = actual(*args, **kwargs)
        before = len(tools), len(calls)
        second = actual(*args, **kwargs)
        assert (len(tools), len(calls)) == before
        assert second == first
        return second

    monkeypatch.setattr(module, "run_collaboration", twice)
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "approved", run


def test_failed_retrieval_cannot_continue_as_if_evidence_were_read(crew, monkeypatch):
    case, _, calls = crew
    original = case[1].state.gateway.call

    async def broken(name, arguments, token):
        if name == "read_policy_source":
            raise ToolFailure("injected_source_timeout")
        return await original(name, arguments, token)

    monkeypatch.setattr(case[1].state.gateway, "call", broken)
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "collaboration_unavailable", run
    assert calls == ["decision"]
    with case[1].state.database.sessions() as session:
        failed = session.scalars(
            select(RunEvent).where(RunEvent.run_id == run["id"], RunEvent.type == "agent.failed")
        ).all()
        assert failed[-1].payload["agent"] == "retrieval_agent"


def test_repair_role_is_single_pass_and_rechecked(crew, monkeypatch):
    case, _, calls = crew

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        if phase in {"generate", "repair"}:
            source = context["allowed_evidence"][-1]
            data = {
                "claims": [
                    {
                        "text": "PRIVATE-INITIAL" if phase == "generate" else source["text"],
                        "evidence_ids": [source["id"]],
                    }
                ]
            }
        else:
            data = {
                "approved": phase == "review_repair",
                "codes": [] if phase == "review_repair" else ["scope_mismatch"],
            }
        return {
            "raw": json.dumps(data),
            "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    run = execute(case)
    assert calls == ["decision", "generate", "review", "repair", "review_repair"]
    assert run["output"]["answer_gate"]["repair"]["attempted"], run
    assert "PRIVATE-INITIAL" not in json.dumps(run)


def test_server_switch_closes_collaboration_even_with_pinned_profile(crew):
    case, _, calls = crew
    case[1].state.settings.return_decision_mode = "rules"
    run = execute(case)
    assert run["output"]["answer_gate"]["agent_mode"] == "single_server_fallback", run
    assert calls == ["generate", "review"]


def test_retrieval_restart_reuses_finished_source_window(crew, monkeypatch):
    case, tools, calls = crew
    actual = RetrievalRole.run

    def restarted(self, arguments, initial):
        first = actual(self, arguments, initial)
        before = len(tools), len(calls)
        second = actual(self, arguments, initial)
        assert len(tools) == before[0]
        assert len(calls) == before[1]
        assert first[0] == second[0]
        assert self.spent()["reads"] == 1
        return second

    monkeypatch.setattr(RetrievalRole, "run", restarted)
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "approved", run


def test_unknown_read_is_not_reissued_after_restart(crew, monkeypatch):
    case, tools, _ = crew
    actual = RetrievalRole.run
    original = case[1].state.gateway.call
    attempts = []

    async def broken(name, arguments, token):
        if name == "read_policy_source":
            attempts.append(name)
            raise ToolFailure("timeout")
        return await original(name, arguments, token)

    monkeypatch.setattr(case[1].state.gateway, "call", broken)

    def restarted(self, arguments, initial):
        with pytest.raises(ToolFailure):
            actual(self, arguments, initial)
        result = actual(self, arguments, initial)
        assert attempts == ["read_policy_source"]
        assert self.spent()["reads"] == 1
        return result

    monkeypatch.setattr(RetrievalRole, "run", restarted)
    run = execute(case)
    assert run["status"] == "completed"
    assert attempts == ["read_policy_source"]


def test_withdrawn_source_cannot_reuse_completed_role_result(crew, monkeypatch):
    case, _, _ = crew
    from workbench import knowledge_collaboration as module
    from workbench.knowledge_models import Document, DocumentVersion

    actual = module.run_collaboration
    observed = []

    def withdraw_between(*args, **kwargs):
        first = actual(*args, **kwargs)
        with case[1].state.database.sessions() as session:
            version = session.get(DocumentVersion, case[4]["version_id"])
            session.get(Document, version.document_id).active_version_id = None
            session.commit()
        second = actual(*args, **kwargs)
        observed.append(second[1]["answer_gate"]["status"])
        assert second[1]["answer_gate"]["status"] == "collaboration_invalid"
        assert second[1]["answer"] != first[1]["answer"]
        return second

    monkeypatch.setattr(module, "run_collaboration", withdraw_between)
    execute(case)
    assert observed == ["collaboration_invalid"]


def test_approved_corrected_parse_can_be_read_over_actual_mcp(crew):
    case, tools, _ = crew
    from workbench.knowledge_models import DocumentVersion

    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, case[4]["version_id"])
        version.parse_metadata = {**version.parse_metadata, "text_origin": "human_corrected_parse"}
        session.commit()
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "approved", run
    assert any(name == "read_policy_source" for name, _ in tools)
    assert any(r.get("text_origin") == "human_corrected_parse" for r in run["output"]["evidence"])


def test_illegal_retrieval_proposal_never_calls_business_tool(crew, monkeypatch):
    case, tools, calls = crew
    monkeypatch.setattr(
        "workbench.knowledge_collaboration.choose_return_action",
        lambda *a, **k: {"action": "create_return_draft"},
    )
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "collaboration_unavailable", run
    assert not set(name for name, _ in tools) & {"create_return_draft", "evaluate_return"}
    assert calls == []


def test_cancellation_during_generation_does_not_publish_role_draft(crew, monkeypatch):
    case, _, _ = crew
    from workbench.models import Run

    def cancel(settings, phase, context, draft=None, **kwargs):
        with case[1].state.database.sessions() as session:
            run = session.scalar(select(Run).where(Run.status == "running"))
            run.status = "cancelled"
            session.commit()
        return {
            "raw": json.dumps(
                {
                    "claims": [
                        {
                            "text": "PRIVATE-CANCELLED-CREW",
                            "evidence_ids": [context["allowed_evidence"][0]["id"]],
                        }
                    ]
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", cancel)
    run = execute(case)
    assert run["status"] == "cancelled", run
    assert "PRIVATE-CANCELLED-CREW" not in json.dumps(run)
