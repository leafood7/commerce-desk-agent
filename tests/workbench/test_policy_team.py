"""Task-board functionality with actual HTTP MCP, explicit local model substitutes."""

# ruff: noqa: F401,F811
import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from test_knowledge_collaboration import crew
from test_return_graph import return_flow
from test_return_inquiry_v2 import publish
from test_returns import return_case
from test_scoped_mcp import scoped_mcp
from test_source_reading import source_flow

from workbench.answer_contract import DraftContractIssue, parse_draft, validate_draft
from workbench.flow_models import GraphCheckpoint
from workbench.models import RunEvent
from workbench.policy_team import board, plan_question


@pytest.fixture
def task_team(crew, monkeypatch):
    case, tools, calls = crew
    contexts = []

    def model(settings, phase, context, draft=None, **kwargs):
        calls.append(phase)
        contexts.append(context)
        if phase in {"generate", "repair"}:
            claims = []
            answers = []
            for item in context["policy_task_board"]["tasks"]:
                source = next(
                    (r for r in context["allowed_evidence"] if r["id"] in item["evidence_ids"]),
                    None,
                )
                if source:
                    answers.append(
                        {
                            "task_id": item["id"],
                            "status": "answered",
                            "claim_indexes": [len(claims)],
                        }
                    )
                    claims.append({"text": source["text"][:1100], "evidence_ids": [source["id"]]})
                else:
                    answers.append(
                        {"task_id": item["id"], "status": "not_confirmed", "claim_indexes": []}
                    )
            result = {
                "claims": claims,
                "task_answers": answers,
                "cannot_confirm": bool(context.get("requires_partial_boundary"))
                or any(a["status"] == "not_confirmed" for a in answers),
            }
        else:
            result = {"approved": True, "codes": []}
        return {
            "raw": json.dumps(result),
            "usage": {"prompt_tokens": 40, "completion_tokens": 40, "total_tokens": 80},
            "response_model": "explicit-functional-double",
        }

    def decision(settings, observation, **kwargs):
        calls.append("decision")
        action = observation["allowed_actions"][0]
        proposal = {"action": action}
        if action == "read_source":
            proposal["target_id"] = observation["read_targets"][0]["id"]
        return {
            "proposal": json.dumps(proposal),
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
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
        knowledge_collaboration_version="task_board_v2",
    )
    return case, tools, calls, contexts


def execute(case, query="DEMO 退货期限和未使用条件，只解释政策"):
    client, _, headers, _, _ = case
    r = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={"kind": "knowledge_search", "query": query, "policy_scope": "returns"},
    )
    assert r.status_code == 202, r.text
    return client.get("/api/v1/runs/" + r.json()["id"], headers=headers()).json()


def test_subquestions_have_source_handoffs_and_reviewed_dispositions(task_team):
    case, tools, calls, contexts = task_team
    run = execute(case)
    assert run["status"] == "completed", run
    gate = run["output"]["answer_gate"]
    assert gate["status"] == "approved", gate
    assert gate["collaboration_version"] == "task_board_v2"
    assert {t["category"] for t in gate["collaboration"]["tasks"]} == {"window", "condition"}
    assert all(t["answer_status"] == "answered" for t in gate["collaboration"]["tasks"])
    assert not any(
        name in {"create_return_draft", "confirm_return_application"} for name, _ in tools
    )
    with case[1].state.database.sessions() as session:
        names = {
            e.payload.get("agent")
            for e in session.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == run["id"], RunEvent.type == "agent.started"
                )
            )
        }
        assert {
            "task_planner",
            "evidence_coordinator",
            "retrieval_agent",
            "answer_agent",
            "review_agent",
        } <= names
        assert session.scalar(
            select(GraphCheckpoint).where(
                GraphCheckpoint.run_id == run["id"],
                GraphCheckpoint.namespace == "knowledge_roles_v2",
            )
        )
    assert contexts[-1]["policy_task_board"]["tasks"]


def test_missing_fee_rule_is_reported_as_unconfirmed_not_order_question(task_team):
    case, tools, _, _ = task_team
    run = execute(case, "DEMO 退货期限和运费由谁承担，只问政策，不申请")
    gate = run["output"]["answer_gate"]
    assert gate["status"] == "approved", gate
    fee = next(t for t in gate["collaboration"]["tasks"] if t["category"] == "responsibility")
    assert fee["answer_status"] == "not_confirmed"
    assert "不能确认" in run["output"]["answer"]
    assert "待核实" not in run["output"]["answer"]
    assert sum(n == "search_knowledge_base" for n, _ in tools) <= 2
    assert sum(n == "read_policy_source" for n, _ in tools) <= 2


def test_contract_rejects_omitted_subquestion(task_team):
    case, _, _, contexts = task_team
    assert execute(case)["output"]["answer_gate"]["status"] == "approved"
    context = contexts[0]
    source = context["allowed_evidence"][0]
    draft = {
        "claims": [{"text": source["text"], "evidence_ids": [source["id"]]}],
        "task_answers": [
            {
                "task_id": context["policy_task_board"]["tasks"][0]["id"],
                "status": "answered",
                "claim_indexes": [0],
            }
        ],
        "cannot_confirm": bool(context.get("requires_partial_boundary")),
    }
    with pytest.raises(DraftContractIssue):
        validate_draft(draft, context)


def test_completed_team_can_resume_without_repeating_tools(task_team, monkeypatch):
    case, tools, calls, _ = task_team
    from workbench import knowledge_collaboration as module

    actual = module.run_collaboration

    def repeat(*args, **kwargs):
        first = actual(*args, **kwargs)
        before = len(tools), len(calls)
        second = actual(*args, **kwargs)
        assert (len(tools), len(calls)) == before
        assert first == second
        return second

    monkeypatch.setattr(module, "run_collaboration", repeat)
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "approved", run


def test_two_windows_are_review_cue_not_automatic_contradiction():
    tasks = plan_question("无理由退货期限和质量退货期限")
    value = board(
        tasks, [{"id": "x", "version_id": "v", "content": "无理由7天；质量问题30天。"}], [], {}
    )
    assert value["review_cues"]
    assert value["scope_conflicts"] == []
    assert not value["unresolved"]


def test_reviewed_categories_guide_reading_without_becoming_evidence(task_team, monkeypatch):
    case, _, _, contexts = task_team
    client, _, headers, _, policy = case
    # This return fixture originally uses SQL-only synthetic chunks. Supply the
    # immutable original file too before exercising the real maintenance API.
    import hashlib
    from workbench.knowledge_models import DocumentVersion, KnowledgeChunk

    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, policy["version_id"])
        chunks = session.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.version_id == version.id)
            .order_by(KnowledgeChunk.position)
        ).all()
        text = "\n\n".join(c.content for c in chunks)
        storage = Path(case[1].state.settings.storage_root)
        storage.mkdir(parents=True, exist_ok=True)
        version.storage_key = "team-functional-policy.md"
        (storage / version.storage_key).write_bytes(text.encode("utf-8"))
        version.content_hash = hashlib.sha256(text.encode()).hexdigest()
        version.parse_metadata = {**version.parse_metadata, "source_sha256": version.content_hash}
        session.commit()
    r = client.post(
        "/api/v1/knowledge/versions/" + policy["version_id"] + "/policy-extractions",
        headers=headers("admin"),
        json={},
    )
    assert r.status_code == 201, r.text
    row = r.json()
    cards = copy.deepcopy(row["payload"]["cards"])
    cards[0]["statement"] = "ANNOTATION-ONLY-NOT-ORIGINAL-EVIDENCE"
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/submit",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "cards": cards,
            "note": "完整核对原文范围期限及条件",
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/review",
        headers=headers("approver"),
        json={
            "expected_revision": row["revision"],
            "action": "approve",
            "acknowledge_warnings": True,
            "note": "独立核对候选所引用的原文",
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/publish",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "acknowledge_warnings": True,
            "note": "按独立审核结果发布本版本",
        },
    )
    assert r.status_code == 200, r.text
    observed = []

    def decision(settings, observation, **kwargs):
        observed.append(observation)
        action = observation["allowed_actions"][0]
        proposal = {"action": action}
        if action == "read_source":
            proposal["target_id"] = observation["read_targets"][0]["id"]
        return {
            "proposal": json.dumps(proposal),
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    monkeypatch.setattr("workbench.return_decisions.model_decision", decision)
    run = execute(case)
    assert run["output"]["answer_gate"]["status"] == "approved", run
    assert any(
        t["reason"] == "reviewed_requested_clause" for o in observed for t in o["read_targets"]
    )
    assert "ANNOTATION-ONLY-NOT-ORIGINAL-EVIDENCE" not in json.dumps(contexts)
