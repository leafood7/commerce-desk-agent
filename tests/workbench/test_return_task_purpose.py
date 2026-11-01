"""New purpose contracts plus real HTTP MCP/persistent graph regressions."""

# ruff: noqa: F811, F401
import json
from pathlib import Path

import pytest
from test_policy_context_purpose import purpose_flow
from test_return_graph import current, return_flow, start
from test_returns import return_case
from test_scoped_mcp import scoped_mcp
from test_source_reading import source_flow

from workbench.answer_contract import question_context
from workbench.return_context import deduplicate_policy_sources
from workbench.return_fact_checks import question_requirements
from workbench.source_reading import search_targets

CASES = json.loads(
    (Path(__file__).parents[2] / "docs/fullstack/return-purpose-v1/new-cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES[:8], ids=lambda r: r["id"])
def test_prefrozen_task_contracts(case):
    plan = question_requirements(case["question"])
    assert plan["task"]["purpose"] == case["expected"]
    assert plan["task"]["does_not_authorize_write"]
    if plan["task"]["quote"]:
        assert (
            case["question"][plan["task"]["start"] : plan["task"]["end"]] == plan["task"]["quote"]
        )


@pytest.mark.parametrize("question", [r["question"] for r in CASES[:4]])
def test_policy_questions_need_no_order_or_application_fields(purpose_flow, monkeypatch, question):
    case = purpose_flow
    calls = []
    actual = case[1].state.gateway.call

    async def traced(name, arguments, token):
        calls.append(name)
        return await actual(name, arguments, token)

    monkeypatch.setattr(case[1].state.gateway, "call", traced)
    result = start(
        case,
        query=question,
        order_number=None,
        return_item_id=None,
        return_quantity=None,
        reason=None,
        return_condition="unknown",
    )
    assert result["status"] == "completed", result
    assert "get_return_policy" in calls
    assert not set(calls) & {
        "evaluate_return",
        "get_order_status",
        "get_quality_inspection",
        "create_return_draft",
    }
    assert result["output"]["context_purpose"] == "policy_question"
    assert not result["output"].get("request")


def test_explicit_application_still_requires_condition(purpose_flow):
    result = start(purpose_flow, query=CASES[5]["question"], return_condition="unknown")
    assert result["status"] == "awaiting_input", result
    assert "return_condition" in result["output"]["missing"]


def test_requested_actual_fact_is_rendered_once_without_model_or_eta(purpose_flow, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Fact-only tasks must not call a model")

    monkeypatch.setattr("workbench.answer_gate.model_call", forbidden)
    result = start(purpose_flow, query=CASES[6]["question"], return_quantity=None, reason=None)
    assert result["status"] == "completed", result
    output = result["output"]
    assert set(output["order"]) <= {"signed_at"}
    assert "预计到达" not in output["answer"]
    assert output["answer"].count("实际签收时间") == 1


def test_unavailable_bank_capability_needs_no_order(purpose_flow, monkeypatch):
    actual = purpose_flow[1].state.gateway.call

    async def traced(name, arguments, token):
        assert name != "get_order_status"
        return await actual(name, arguments, token)

    monkeypatch.setattr(purpose_flow[1].state.gateway, "call", traced)
    result = start(
        purpose_flow,
        query=CASES[7]["question"],
        order_number=None,
        return_quantity=None,
        reason=None,
    )
    assert result["status"] == "completed", result
    assert "不提供" in result["output"]["answer"]


def test_dedup_requires_proven_same_version_and_full_text():
    chunk = {"id": "c", "version_id": "v", "content": "条款。"}
    read = {
        "id": "r",
        "version_id": "v",
        "source_kind": "source_read",
        "covered_chunk_ids": ["c"],
        "content": "条款。例外仍保留。",
    }
    assert deduplicate_policy_sources([chunk, read]) == [read]
    assert len(deduplicate_policy_sources([chunk, {**read, "version_id": "v2"}])) == 2
    assert len(deduplicate_policy_sources([chunk, {**read, "content": "例外。"}])) == 2


def test_original_read_starts_near_canonical_hit():
    targets = search_targets(
        [{"id": "c", "version_id": "v", "parent_id": "p0", "parent_match_offset": 4653}]
    )
    assert targets[0]["request"]["offset"] == 4403
    assert targets[0]["request"]["max_chars"] == 2000


def test_available_but_unrequested_order_fields_are_not_model_context():
    context = question_context(
        "只核对实际签收",
        [],
        order={"signed_at": "2026-01-01", "estimated_delivery": "2026-01-02", "number": "X"},
    )
    assert context["tools"]["order"] == {"signed_at": "2026-01-01"}
    assert question_context("解释规则", [], order={"number": "X"})["tools"]["order"] == {}


def test_quantity_correction_revalidates_policy_without_repeated_read(source_flow):
    from test_return_graph import resume

    case, calls = source_flow
    first = start(case)
    assert first["status"] == "awaiting_confirmation", first
    done = resume(case, first, "correct", return_quantity=2)
    assert done["status"] == "awaiting_confirmation", done
    assert done["output"]["retrieval"]["revalidated_policy_ids"]
    assert done["output"]["source_investigation"]["reads"] == 1
    assert sum(n == "read_policy_source" for n, _ in calls) == 1
    assert sum(n == "get_order_status" for n, _ in calls) == 2
    assert sum(n == "evaluate_return" for n, _ in calls) == 2
    assert done["output"]["proposal"]["return_quantity"] == 2
    assert not done["output"].get("request")


def test_withdrawn_source_after_correction_is_not_reused(source_flow):
    from test_return_graph import resume

    from workbench.knowledge_models import Document, DocumentVersion

    case, _ = source_flow
    first = start(case)
    with case[1].state.database.sessions() as session:
        version = session.get(DocumentVersion, case[4]["version_id"])
        session.get(Document, version.document_id).active_version_id = None
        session.commit()
    done = resume(case, first, "correct", return_quantity=2)
    assert done["status"] == "interrupted", done
    assert not done["output"].get("request")
    assert not done["output"].get("evidence")


@pytest.mark.parametrize(
    "text",
    ["我想了解如何申请退货的规则", "先说说规则，不用帮我申请退货", "怎么申请退货？解释规则就行"],
)
def test_describing_an_application_is_not_requesting_one(text):
    assert question_requirements(text)["task"]["purpose"] == "policy"


def test_fact_only_quality_uses_actual_lookup_not_eligibility(purpose_flow, monkeypatch):
    calls = []
    actual = purpose_flow[1].state.gateway.call

    async def traced(name, arguments, token):
        calls.append(name)
        return await actual(name, arguments, token)

    monkeypatch.setattr(purpose_flow[1].state.gateway, "call", traced)
    result = start(
        purpose_flow, query="仅查实际质检结果。", reason=None, return_condition="unknown"
    )
    assert result["status"] == "completed", result
    assert calls == ["get_quality_inspection"]
    assert result["output"]["fact_assessment"]["items"][0]["status"] == "unknown"
    assert not result["output"].get("request")


def test_wire_groups_preserve_all_source_text_and_scope_without_truncation():
    from workbench.answer_contract import grouped_context

    rows = [
        {
            "id": str(i),
            "version_id": "v",
            "audience": {"region": "A"},
            "section": "conditions",
            "text_origin": "parsed_original",
            "text": "完整条款及例外" + str(i),
        }
        for i in range(4)
    ]
    original = {
        "allowed_evidence": rows,
        "question": "完整问题",
        "task": {"purpose": "policy", "quote": "完整问题"},
    }
    grouped = grouped_context(original)
    rebuilt = []
    for group in grouped["allowed_evidence"]:
        metadata = {k: v for k, v in group.items() if k != "sources"}
        rebuilt.extend({**metadata, **row} for row in group["sources"])
    assert rebuilt == rows
    assert original["task"]["quote"] == "完整问题"
    assert grouped["question"] == original["question"]


def test_selected_policy_question_disallows_application_condition_checklist():
    from workbench.answer_contract import messages, validate_draft

    context = question_context(
        "解释拆封规则",
        [{"id": "c", "version_id": "v", "content": "包装打开不等于使用。"}],
        policy_only=True,
    )
    good = {"claims": [{"text": "包装打开不等于使用。", "evidence_ids": ["c"]}]}
    assert validate_draft(good, context).claims
    with pytest.raises(ValueError, match="Application conditions"):
        validate_draft({**good, "missing_conditions": ["商品是否使用"]}, context)
    system = messages("generate", context)[0]["content"]
    schema = json.loads(system.split("JSON Schema:", 1)[1])
    assert schema["properties"]["missing_conditions"]["maxItems"] == 0
    # Application/ambiguous policy scope contexts keep their existing contract.
    context.pop("task_constraints")
    assert validate_draft(
        {**good, "missing_conditions": ["确认适用地区"]}, context
    ).missing_conditions


def test_disallowed_condition_draft_is_private_across_public_outputs(purpose_flow, monkeypatch):
    from pydantic import SecretStr

    case = purpose_flow
    client, app, headers, _, _ = case
    settings = app.state.settings
    settings.policy_answer_mode = "reviewed"
    settings.agent_router_mode = "model"
    settings.agent_router_url = "https://api.deepseek.com/chat/completions"
    settings.agent_router_model = "deepseek-flash"
    settings.agent_router_key = SecretStr("test-only-never-dispatched")
    head = client.get("/api/v1/runtime/config", headers=headers("admin")).json()
    draft = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "model_alias": "server_default",
            "policy_answer_mode": "reviewed",
            "return_flow_version": "guided_v3",
            "return_retrieval_mode": "clause_bound",
        },
    )
    assert draft.status_code == 201, draft.text
    client.post(
        f"/api/v1/runtime/config/versions/{draft.json()['id']}/publish",
        headers=headers("admin"),
        json={"expected_revision": head["revision"]},
    ).raise_for_status()
    calls = []
    sentinel = "PRIVATE_UNNECESSARY_APPLICATION_CHECKLIST"

    def model(config, phase, context, draft=None, **kwargs):
        calls.append(phase)
        assert phase == "generate"  # Program failure must not spend a review call.
        assert context["task_constraints"]["no_application_conditions"]
        return {
            "raw": json.dumps(
                {
                    "claims": [
                        {"text": "政策说明", "evidence_ids": [context["allowed_evidence"][0]["id"]]}
                    ],
                    "missing_conditions": [sentinel],
                }
            ),
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    monkeypatch.setattr("workbench.answer_gate.model_call", model)
    result = start(case, query="先解释拆封规则，暂不申请。", return_condition="unknown")
    assert result["status"] == "completed", result
    assert result["output"]["answer_gate"]["status"] == "invalid_contract_or_review"
    assert calls == ["generate"]
    urls = [
        f"/api/v1/runs/{result['id']}",
        f"/api/v1/runs/{result['id']}/events",
        f"/api/v1/conversations/{result['conversation_id']}/messages",
    ]
    for url in urls:
        response = client.get(url, headers=headers())
        assert response.status_code == 200
        assert sentinel not in response.text
