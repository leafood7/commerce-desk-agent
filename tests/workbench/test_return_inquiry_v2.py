"""Development contracts: real HTTP MCP/persistent graph; vector/model doubles explicit."""

# ruff: noqa: F811,F401
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_return_graph import current, resume, return_flow, start
from test_returns import return_case
from test_scoped_mcp import scoped_mcp
from test_source_reading import source_flow

from workbench.knowledge_models import DocumentVersion, KnowledgeChunk
from workbench.models import identifier
from workbench.parent_context import related_sections
from workbench.return_models import ReturnRequest
from workbench.source_reading import reference_gaps


def publish(case, **flags):
    client, _, headers, _, _ = case
    head = client.get("/api/v1/runtime/config", headers=headers("admin")).json()
    response = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={"return_flow_version": "guided_v3", "return_retrieval_mode": "clause_bound", **flags},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        f"/api/v1/runtime/config/versions/{response.json()['id']}/publish",
        headers=headers("admin"),
        json={"expected_revision": head["revision"]},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def inquiry_flow(return_flow, monkeypatch):
    calls = []
    actual = return_flow[1].state.gateway.call

    async def call(name, args, token):
        calls.append(name)
        return await actual(name, args, token)

    monkeypatch.setattr(return_flow[1].state.gateway, "call", call)
    publish(return_flow, return_task_clarification=True)
    return return_flow, calls


def choose(case, run, task):
    return resume(case, run, "choose_task", return_task=task)


def test_ambiguous_goal_precedes_all_tools_and_application_material(inquiry_flow):
    case, calls = inquiry_flow
    run = start(
        case,
        query="这个退货你帮我看看。",
        order_number=None,
        return_item_id=None,
        return_quantity=None,
        reason=None,
    )
    assert run["status"] == "awaiting_input", run
    assert run["output"]["pause_kind"] == "task_clarification"
    assert calls == []
    done = choose(case, run, "policy")
    assert done["status"] == "completed", done
    assert "get_return_policy" in calls
    assert not set(calls) & {"get_order_status", "evaluate_return", "create_return_draft"}
    assert done["output"]["task_frame"]["origin"] == "explicit_user_selection"


def test_material_is_required_only_after_application_choice(inquiry_flow):
    case, calls = inquiry_flow
    run = start(
        case,
        query="帮我看看退货。",
        order_number=None,
        return_item_id=None,
        return_quantity=None,
        reason=None,
    )
    done = choose(case, run, "application")
    assert done["status"] == "awaiting_input", done
    assert "order_number" in done["output"]["missing"]
    assert calls == []


def test_fact_choice_then_field_choice_stays_in_same_flow(inquiry_flow):
    case, calls = inquiry_flow
    run = start(
        case,
        query="帮我看看退货。",
        order_number=None,
        return_item_id=None,
        return_quantity=None,
        reason=None,
    )
    selected = choose(case, run, "facts")
    assert selected["output"]["pause_kind"] == "fact_clarification", selected
    done = resume(case, selected, "choose_fact", return_fact="bank_received")
    assert done["status"] == "completed", done
    assert "不提供" in done["output"]["answer"]
    assert calls == []
    assert done["id"] == run["id"]


def test_eligibility_can_collect_condition_but_never_offer_write_confirmation(inquiry_flow):
    case, calls = inquiry_flow
    run = start(case, query="帮我看看退货。", return_task="eligibility", return_condition="unknown")
    assert run["status"] == "awaiting_input", run
    assert run["output"]["missing"] == ["return_condition"]
    done = resume(case, run, "supply", return_condition="unused")
    assert done["status"] == "completed", done
    assert "尚未申请" in done["output"]["answer"]
    assert not done["output"].get("request")
    assert not set(calls) & {"create_return_draft", "confirm_return_application"}


def test_explicit_application_still_pauses_for_preview_confirmation(inquiry_flow):
    case, _ = inquiry_flow
    run = start(case, query="先了解政策", return_task="application")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"].get("proposal") and not run["output"].get("request")


def test_task_choice_rejects_wrong_pause_fields_and_stale_pause(inquiry_flow):
    case, _ = inquiry_flow
    client, _, headers, _, _ = case
    run = start(case, query="帮我看看退货。")
    url = f"/api/v1/runs/{run['id']}/resume"
    response = client.post(
        url,
        headers={**headers(), "Idempotency-Key": uuid4().hex},
        json={
            "action": "choose_task",
            "pause_id": run["output"]["pause_id"],
            "return_task": "application",
            "return_quantity": 999,
        },
    )
    assert response.status_code == 422
    fact = choose(case, run, "facts")
    response = client.post(
        url,
        headers={**headers(), "Idempotency-Key": uuid4().hex},
        json={
            "action": "choose_task",
            "pause_id": run["output"]["pause_id"],
            "return_task": "application",
        },
    )
    assert response.status_code == 409
    assert fact["output"]["pause_kind"] == "fact_clarification"


def test_paused_choice_uses_pinned_profile_after_active_configuration_changes(inquiry_flow):
    case, _ = inquiry_flow
    first = start(case, query="帮我看看退货。")
    publish(case, return_task_clarification=False)
    done = choose(case, first, "policy")
    assert done["status"] == "completed", done
    denied = case[0].post(
        "/api/v1/runs",
        headers=case[2](),
        json={
            "kind": "aftersales",
            "query": "规则",
            "return_policy_id": case[3]["policy_id"],
            "return_task": "policy",
        },
    )
    assert denied.status_code == 422


def test_explicit_mentions_are_bidirectional_but_not_inferred_exceptions():
    parents = [
        {"id": "p0", "section": "第三条", "links": []},
        {"id": "p1", "section": "第八条", "links": ["p0"]},
    ]
    assert related_sections(parents, "p0") == [
        {"parent_id": "p1", "section": "第八条", "direction": "incoming"}
    ]
    assert related_sections(parents, "p1")[0]["direction"] == "outgoing"
    assert related_sections(parents, "p0")[0].get("exception") is None


def test_partial_windows_only_cover_parent_when_union_has_no_gap():
    def window(start, content):
        return {
            "id": str(start),
            "version_id": "v",
            "parent_id": "p1",
            "source_kind": "source_read",
            "offset": start,
            "content": content,
            "total_chars": 4,
        }

    anchor = {
        "id": "a",
        "version_id": "v",
        "parent_id": "p0",
        "parent_complete": True,
        "related_sections": [{"parent_id": "p1"}],
    }
    state = {
        "context_purpose": "policy_question",
        "retrieved_evidence": [anchor, window(0, "ab"), window(2, "cd")],
    }
    assert reference_gaps(state)["unread_related_sections"] == 0
    state["retrieved_evidence"][-1] = window(3, "d")
    assert reference_gaps(state)["unread_related_sections"] == 1


@pytest.fixture
def incoming_flow(source_flow):
    case, calls = source_flow
    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, case[4]["version_id"])
        structure = dict(version.parse_metadata)
        pc = {
            **structure["parent_child"],
            "parents": [dict(p) for p in structure["parent_child"]["parents"]],
            "children": dict(structure["parent_child"]["children"]),
        }
        chunk = KnowledgeChunk(
            id=identifier(),
            organization_id=version.organization_id,
            version_id=version.id,
            position=2,
            section="第八条",
            content="第三条涉及刻字商品时，不支持无理由退货。",
        )
        session.add(chunk)
        pc["children"]["2"] = "p1"
        pc["parents"].append(
            {
                "id": "p1",
                "section": "第八条",
                "text": chunk.content,
                "positions": [2],
                "links": ["p0"],
                "unresolved_articles": [],
                "block_start": 2,
                "block_end": 3,
            }
        )
        structure["parent_child"] = pc
        version.parse_metadata = structure
        session.commit()
    publish(
        case,
        return_source_reading=True,
        return_reference_check=True,
        return_task_clarification=True,
    )
    return case, calls


def test_incoming_reference_is_read_even_when_current_mapping_is_covered(incoming_flow):
    case, calls = incoming_flow
    run = start(case, query="解释规则，不办理退货。", return_task="policy")
    assert run["status"] == "completed", run
    parents = [body["parent_id"] for name, body in calls if name == "read_policy_source"]
    assert "p1" in parents, calls
    assert run["output"]["retrieval"]["reference_gaps"]["unread_related_sections"] == 0
    assert "刻字" in "".join(r["content"] for r in run["output"]["evidence"])
    assert run["output"]["source_investigation"]["reads"] <= 2


def test_unmapped_exception_cannot_silently_approve_business_eligibility(incoming_flow):
    case, _ = incoming_flow
    run = start(case, query="帮我核对", return_task="eligibility")
    assert run["status"] == "completed", run
    assert not run["output"].get("proposal") and not run["output"].get("request")
    assert run["output"]["retrieval"]["reference_gaps"]["unmapped_business_reference_sections"] >= 1


def test_unresolved_references_require_partial_answer_contract():
    from workbench.answer_contract import question_context, validate_draft

    ctx = question_context(
        "规则",
        [{"id": "a", "version_id": "v", "content": "见第九条"}],
        retrieval={"reference_gaps": {"unresolved_article_references": 1}},
    )
    data = {"claims": [{"text": "需参见第九条", "evidence_ids": ["a"]}]}
    with pytest.raises(ValueError, match="partial boundary"):
        validate_draft(data, ctx)
    assert validate_draft({**data, "cannot_confirm": True}, ctx).cannot_confirm
