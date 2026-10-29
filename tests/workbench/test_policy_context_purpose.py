"""Task purpose comes from program routes; retrieval and projections stay separate."""

# ruff: noqa: F811
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_return_graph import current, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.knowledge_models import KnowledgeChunk
from workbench.return_context import select_policy_context
from workbench.return_models import ReturnRequest


def test_purpose_preserves_question_sources_but_application_stays_strict():
    matches = [
        {"id": "normal", "version_id": "v", "content": "普通分支限7天。"},
        {"id": "photo", "version_id": "v", "content": "照片本身不等于确认缺陷。"},
    ]
    clauses = [{"chunk_id": "normal", "quote": "普通分支限7天。"}]
    original = copy.deepcopy(matches)
    assert [
        r["id"] for r in select_policy_context(matches, clauses, purpose="application")["context"]
    ] == ["normal"]
    assert select_policy_context(matches, clauses, purpose="policy_question")["context"] == matches
    assert matches == original
    assert select_policy_context(matches, [], purpose="application")["empty"]
    assert select_policy_context([], clauses, purpose="policy_question")["empty"]
    with pytest.raises(ValueError):
        select_policy_context(matches, clauses, purpose="invented")


@pytest.fixture
def purpose_flow(return_flow, scoped_mcp):
    case = return_flow
    client, app, headers, _, policy = case
    _, _, _, _, index = scoped_mcp
    # Explicit fixture corpus setup before any run, not a production edit path.
    with app.state.database.sessions() as session:
        source = session.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == policy["version_id"])
        )
        row = KnowledgeChunk(
            id="photo-" + str(uuid4()),
            organization_id=source.organization_id,
            version_id=source.version_id,
            position=1,
            content="照片本身不等于确认缺陷；质量问题仍需独立缺陷结论。",
            section="照片规则",
        )
        session.add(row)
        session.commit()
        index.upsert(
            row.organization_id,
            row.version_id,
            [{"id": row.id, "content": row.content}],
            lambda: None,
        )
    head = client.get("/api/v1/runtime/config", headers=headers("admin")).json()
    draft = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={"return_flow_version": "guided_v3", "return_retrieval_mode": "clause_bound"},
    )
    assert draft.status_code == 201, draft.text
    assert (
        client.post(
            f"/api/v1/runtime/config/versions/{draft.json()['id']}/publish",
            headers=headers("admin"),
            json={"expected_revision": head["revision"]},
        ).status_code
        == 200
    )
    return case


def count_requests(case):
    with case[1].state.database.sessions() as session:
        return session.scalar(select(func.count()).select_from(ReturnRequest))


def test_rule_question_entry_needs_no_order_and_does_not_create_application(
    purpose_flow, monkeypatch
):
    case = purpose_flow
    client, app, headers, _, _ = case
    original_call = app.state.gateway.call

    async def misleading_metadata(*args, **kwargs):
        payload = await original_call(*args, **kwargs)
        return {**payload, "context_purpose": "application"}

    monkeypatch.setattr(app.state.gateway, "call", misleading_metadata)
    response = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={
            "kind": "knowledge_search",
            "query": "照片能自动确认缺陷吗？",
            "policy_scope": "returns",
        },
    )
    assert response.status_code == 202, response.text
    run = current(case, response.json()["id"])
    assert run["status"] == "completed", run
    assert run["output"]["context_purpose"] == "policy_question"
    assert "照片本身不等于确认缺陷" in "\n".join(e["content"] for e in run["output"]["evidence"])
    assert count_requests(case) == 0
    assert (
        client.post(
            "/api/v1/runs",
            headers=headers(),
            json={
                "kind": "knowledge_search",
                "query": "照片规则",
                "context_purpose": "application",
            },
        ).status_code
        == 422
    )


def test_explicit_read_only_graph_keeps_retrieved_cross_branch_question_evidence(purpose_flow):
    case = purpose_flow
    run = start(case, query="只查照片能自动证明缺陷吗？不要申请。", return_condition="unknown")
    assert run["status"] == "completed", run
    assert run["output"]["context_purpose"] == "policy_question"
    assert "照片本身不等于确认缺陷" in "\n".join(
        e["content"] for e in run["output"]["policy_context"]
    )
    assert "照片本身不等于确认缺陷" in "\n".join(e["content"] for e in run["output"]["evidence"])
    assert "当前检索未覆盖全部必要政策条款" not in run["output"]["answer"]
    assert count_requests(case) == 0
    empty = start(case, query="只查 zxqv9876", return_condition="unknown")
    assert empty["status"] == "completed", empty
    assert empty["output"]["context_purpose"] == "policy_question"
    assert empty["output"]["policy_context"] == []
    assert empty["output"]["evidence"] == []
    assert "当前检索没有可引用的政策原文" in empty["output"]["answer"]


def test_application_keeps_current_branch_and_unknown_purpose_cannot_override(purpose_flow):
    case = purpose_flow
    run = start(case, query="申请退货并核对照片规则。")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["context_purpose"] == "application"
    assert "照片本身" not in "\n".join(e["content"] for e in run["output"]["policy_context"])
    assert count_requests(case) == 0


def test_withdrawn_question_source_is_not_published(purpose_flow):
    case = purpose_flow
    client, app, headers, _, policy = case
    from workbench.knowledge_models import DocumentVersion

    with app.state.database.sessions() as session:
        document_id = session.get(DocumentVersion, policy["version_id"]).document_id
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{document_id}/withdraw", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={
            "kind": "knowledge_search",
            "query": "照片能自动证明缺陷吗？",
            "policy_scope": "returns",
        },
    )
    assert response.status_code == 202
    run = current(case, response.json()["id"])
    assert run["status"] == "completed" and not run["output"]["evidence"]
    assert count_requests(case) == 0
