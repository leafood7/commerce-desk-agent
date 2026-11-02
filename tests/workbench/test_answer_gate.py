"""Answer gates: real SQL/API boundaries with explicit model and retrieval substitutes."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from helpers import MemoryIndex
from pydantic import SecretStr
from sqlalchemy import select

from workbench.answer_contract import question_context, validate_draft, validate_review
from workbench.ingestion import work_once
from workbench.knowledge_schemas import SearchInput
from workbench.models import Message, Run, RunEvent
from workbench.retrieval import KnowledgeRetriever
from workbench.tool_contracts import verify_grant

SENTINEL = "PRIVATE-DRAFT-MUST-NOT-LEAK"


def context():
    return question_context(
        "商品退货期限",
        [{"id": "e1", "version_id": "v1", "content": "普通商品七天内可退，定制品除外。"}],
    )


def test_contract_rejects_fabricated_reference_and_missing_fields():
    with pytest.raises(ValueError):
        validate_draft({"claims": [{"text": "可退", "evidence_ids": ["invented"]}]}, context())
    with pytest.raises(ValueError):
        validate_draft({"facts": [{"tool": "order", "fields": ["signed_at"]}]}, context())
    for data in [{"approved": True, "codes": ["scope_mismatch"]}, {"approved": False, "codes": []}]:
        with pytest.raises(ValueError):
            validate_review(data)


def setup_gate(logged_in, workbench, monkeypatch, mode="reviewed"):
    client, app, headers = logged_in
    settings = workbench[2]
    settings.policy_answer_mode = "reviewed_repair" if mode == "reviewed_repair" else "reviewed"
    settings.agent_router_mode = "model"
    settings.agent_router_url = "https://api.deepseek.com/chat/completions"
    settings.agent_router_model = "deepseek-flash"
    settings.agent_router_key = SecretStr("isolated-test-key-not-a-real-credential")
    uploaded = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        files={"file": ("policy.md", "普通商品七天内可退，定制品除外。".encode(), "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["version"]["id"]
    index = MemoryIndex()
    assert work_once(app.state.database, settings, index)
    client.post(
        f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
    ).raise_for_status()
    monkeypatch.setattr(
        "workbench.retrieval.rerank", lambda query, rows: [{**r, "rerank_score": 1.0} for r in rows]
    )
    retriever = KnowledgeRetriever(app.state.database, settings, index)

    async def gateway(name, args, token):
        with app.state.database.sessions() as session:
            user, _ = verify_grant(session, token, settings)
        assert name == "search_knowledge_base"
        return retriever.search(user.organization_id, SearchInput.model_validate(args))

    monkeypatch.setattr(app.state.gateway, "call", gateway)
    profile = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={"model_alias": "server_default", "policy_answer_mode": mode},
    )
    assert profile.status_code == 201, profile.text
    result = client.post(
        "/api/v1/runtime/config/versions/" + profile.json()["id"] + "/publish",
        headers=headers("admin"),
        json={"expected_revision": 0},
    )
    assert result.status_code == 200, result.text
    return client, app, headers, version


def test_blocked_draft_absent_from_run_events_history_before_and_after_review(
    logged_in, workbench, monkeypatch
):
    client, app, headers, _ = setup_gate(logged_in, workbench, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def model(settings, phase, ctx, draft=None, **kwargs):
        calls.append(phase)
        if phase == "generate":
            raw = json.dumps(
                {"claims": [{"text": SENTINEL, "evidence_ids": [ctx["allowed_evidence"][0]["id"]]}]}
            )
        else:
            entered.set()
            assert release.wait(10)
            raw = json.dumps({"approved": False, "codes": ["unsupported_claim"]})
        return {
            "raw": raw,
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            "response_model": "substitute",
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    auth = headers()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            lambda: client.post(
                "/api/v1/runs",
                headers={**auth, "Idempotency-Key": "gate-isolation"},
                json={"kind": "knowledge_search", "query": "商品退货期限"},
            )
        )
        try:
            assert entered.wait(15)
            with app.state.database.sessions() as session:
                row = session.scalar(select(Run).where(Run.idempotency_key == "gate-isolation"))
                identifier, conversation = row.id, row.conversation_id
                assert row.output is None
                assert all(
                    SENTINEL not in json.dumps(e.payload)
                    for e in session.scalars(select(RunEvent).where(RunEvent.run_id == identifier))
                )
                assert all(
                    SENTINEL not in m.content
                    for m in session.scalars(select(Message).where(Message.run_id == identifier))
                )
            assert SENTINEL not in client.get("/api/v1/runs/" + identifier, headers=auth).text
            assert (
                SENTINEL
                not in client.get(
                    "/api/v1/conversations/" + conversation + "/messages", headers=auth
                ).text
            )
        finally:
            release.set()
        response = pending.result(timeout=15)
    assert response.status_code == 202, response.text
    result = client.get("/api/v1/runs/" + identifier, headers=auth).json()
    assert result["output"]["answer_gate"]["status"] == "blocked"
    for path in [
        "/api/v1/runs/" + identifier,
        "/api/v1/runs/" + identifier + "/events",
        "/api/v1/conversations/" + conversation + "/messages",
        "/api/v1/conversations/" + conversation + "/runs",
    ]:
        assert SENTINEL not in client.get(path, headers=auth).text
    assert calls == ["generate", "review"]


@pytest.mark.parametrize(
    "failure", ["timeout", "bad_json", "invalid_reference", "approve", "stale_source"]
)
def test_review_failures_and_publish_revalidation(logged_in, workbench, monkeypatch, failure):
    client, app, headers, version = setup_gate(logged_in, workbench, monkeypatch)

    def model(settings, phase, ctx, draft=None, **kwargs):
        if phase == "generate":
            raw = json.dumps(
                {
                    "claims": [
                        {
                            "text": "普通商品七天内可退，定制品除外。",
                            "evidence_ids": [
                                "fabricated"
                                if failure == "invalid_reference"
                                else ctx["allowed_evidence"][0]["id"]
                            ],
                        }
                    ]
                }
            )
        else:
            if failure == "timeout":
                raise TimeoutError("substitute")
            if failure == "bad_json":
                return {"raw": "not JSON", "usage": None}
            if failure == "stale_source":
                from workbench.knowledge_models import Document, DocumentVersion

                with app.state.database.sessions() as session:
                    v = session.get(DocumentVersion, version)
                    session.get(Document, v.document_id).active_version_id = None
                    session.commit()
            raw = json.dumps({"approved": True, "codes": []})
        return {
            "raw": raw,
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            "response_model": "substitute",
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    r = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={"kind": "knowledge_search", "query": "商品退货期限"},
    )
    assert r.status_code == 202, r.text
    result = client.get("/api/v1/runs/" + r.json()["id"], headers=headers()).json()
    status = result["output"]["answer_gate"]["status"]
    assert status == (
        "approved"
        if failure == "approve"
        else "evidence_changed"
        if failure == "stale_source"
        else "invalid_contract_or_review"
        if failure in {"bad_json", "invalid_reference"}
        else "unavailable"
    )
    assert ("普通商品七天内可退" in result["output"]["answer"]) == (failure == "approve")


def test_server_disabled_cannot_be_enabled_by_profile(logged_in):
    client, _, headers = logged_in
    r = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={"policy_answer_mode": "reviewed"},
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "scenario", ["unknown_usage", "empty_evidence", "cancelled", "configuration_changed"]
)
def test_quota_no_replay_empty_and_cancelled_publication(
    logged_in, workbench, monkeypatch, scenario
):
    from workbench.quota_models import RunUsage

    client, app, headers, _ = setup_gate(logged_in, workbench, monkeypatch)
    calls = []
    if scenario == "empty_evidence":

        async def empty(name, args, token):
            return {"status": "no_evidence", "matches": []}

        monkeypatch.setattr(app.state.gateway, "call", empty)

    def model(settings, phase, ctx, draft=None, **kwargs):
        calls.append(phase)
        if scenario == "unknown_usage":
            raise TimeoutError("unknown outcome")
        if phase == "generate":
            raw = json.dumps(
                {
                    "claims": [
                        {
                            "text": "普通商品七天内可退，定制品除外。",
                            "evidence_ids": [ctx["allowed_evidence"][0]["id"]],
                        }
                    ]
                }
            )
        else:
            if scenario == "cancelled":
                with app.state.database.sessions() as session:
                    run = session.scalar(select(Run).where(Run.idempotency_key == "guard-extra"))
                    run.status = "cancelled"
                    session.commit()
            if scenario == "configuration_changed":
                workbench[2].agent_router_model = "different-model"
            raw = json.dumps({"approved": True, "codes": []})
        return {
            "raw": raw,
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    auth = {**headers(), "Idempotency-Key": "guard-extra"}
    body = {"kind": "knowledge_search", "query": "商品退货期限"}
    response = client.post("/api/v1/runs", headers=auth, json=body)
    assert response.status_code == 202
    result = client.get("/api/v1/runs/" + response.json()["id"], headers=headers()).json()
    if scenario == "cancelled":
        assert result["status"] == "cancelled" and result["output"] is None
    else:
        assert (
            result["output"]["answer_gate"]["status"]
            == {
                "unknown_usage": "unavailable",
                "empty_evidence": "no_evidence",
                "configuration_changed": "configuration_changed",
            }[scenario]
        )
    client.post("/api/v1/runs", headers=auth, json=body)
    assert len(calls) == (
        0 if scenario == "empty_evidence" else 1 if scenario == "unknown_usage" else 2
    )
    if scenario == "unknown_usage":
        with app.state.database.sessions() as session:
            usage = session.get(RunUsage, response.json()["id"])
            assert usage.unknown_model_attempts == 1 and usage.model_reserved_tokens > 0


def test_readonly_mixed_graph_uses_real_fields_without_exposing_draft(
    logged_in, workbench, monkeypatch
):
    from workbench.domain import get_order, order_view

    client, app, headers, _ = setup_gate(logged_in, workbench, monkeypatch)
    old_call = app.state.gateway.call

    async def gateway(name, args, token):
        if name == "get_order_status":
            with app.state.database.sessions() as session:
                user, _ = verify_grant(session, token, workbench[2])
                return {
                    "status": "found",
                    "order": order_view(session, get_order(session, user, args["order_number"])),
                }
        return await old_call(name, args, token)

    monkeypatch.setattr(app.state.gateway, "call", gateway)
    monkeypatch.setattr(
        "workbench.intent_router.model_intent",
        lambda settings, query: (
            "knowledge",
            {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "read_plan": {"tools": ["get_order_status", "search_knowledge_base"]},
            },
        ),
    )

    def model(settings, phase, ctx, draft=None, **kwargs):
        assert "customer_name" not in json.dumps(ctx)
        if phase == "generate":
            raw = json.dumps(
                {
                    "claims": [
                        {
                            "text": "普通商品七天内可退，定制品除外。",
                            "evidence_ids": [ctx["allowed_evidence"][0]["id"]],
                        }
                    ],
                    "unknown_facts": ["signed_at"],
                }
            )
        else:
            raw = json.dumps({"approved": True, "codes": []})
        return {
            "raw": raw,
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    reply = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={
            "kind": "aftersales",
            "query": "查询订单A1001的退货政策条件，并核实实际签收时间，只查询",
            "order_number": "A1001",
        },
    )
    assert reply.status_code == 202, reply.text
    result = client.get("/api/v1/runs/" + reply.json()["id"], headers=headers()).json()
    assert result["status"] == "completed", result.get("error_code")
    assert result["output"]["answer_gate"]["status"] == "approved"
    assert "实际签收字段缺失" in result["output"]["answer"]
    assert result["output"]["request"] is None


def test_required_evidence_overflow_stops_before_http(monkeypatch):
    from workbench.answer_gate import GateNotSent, model_call
    from workbench.config import WorkbenchSettings

    settings = WorkbenchSettings(
        _env_file=None,
        agent_router_url="https://api.deepseek.com/chat/completions",
        agent_router_model="deepseek-flash",
        agent_router_key=SecretStr("not-real"),
    )
    ctx = question_context("期限", [{"id": "x", "version_id": "v", "content": "必要条款" * 10000}])

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not be called")

    monkeypatch.setattr("workbench.answer_gate.httpx.post", forbidden)
    with pytest.raises(GateNotSent):
        model_call(settings, "generate", ctx)


def test_private_unknown_phase_is_not_replayed_on_reentry(logged_in, workbench, monkeypatch):
    from workbench.answer_gate import private_call
    from workbench.execution_limits import ExecutionLimits
    from workbench.mcp_gateway import ToolFailure
    from workbench.profiles import effective_settings
    from workbench.runs import create_run
    from workbench.schemas import RunInput

    client, app, headers, _ = setup_gate(logged_in, workbench, monkeypatch)
    token = client.post("/api/v1/mcp/grants", headers=headers()).json()["access_token"]
    with app.state.database.sessions() as session:
        actor, _ = verify_grant(session, token, workbench[2])
        run, _ = create_run(
            session,
            actor,
            RunInput(kind="knowledge_search", query="期限"),
            "unknown-reentry",
            settings=workbench[2],
        )
        run.status = "running"
        session.commit()
        run_id = run.id
    settings, _, _ = effective_settings(app.state.database, run_id)
    calls = []

    def failed(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("unknown")

    monkeypatch.setattr("workbench.answer_gate.model_call", failed)
    limits = ExecutionLimits(settings)
    with pytest.raises(ToolFailure, match="answer_model_failed"):
        private_call(
            app.state.database, run_id, settings, "generate", context(), None, limits, lambda: None
        )
    with pytest.raises(ToolFailure, match="answer_unknown_cached"):
        private_call(
            app.state.database, run_id, settings, "generate", context(), None, limits, lambda: None
        )
    assert len(calls) == 1


def test_structural_evidence_gaps_are_visible_without_claiming_whole_policy_required():
    ctx = question_context(
        "期限",
        [{"id": "e", "version_id": "v", "content": "七天"}],
        retrieval={
            "reference_expansion": {
                "limited_ids": ["not-read"],
                "unresolved_articles": [3],
                "ignored_private": "never expose",
            }
        },
    )
    assert ctx["assembly_gaps"]["omitted_chunks"] == 1
    assert ctx["assembly_gaps"]["unresolved_article_links"] == 1
    assert ctx["evidence_status"] == "known_assembly_gaps_question_sufficiency_not_established"
    assert "ignored_private" not in json.dumps(ctx)
