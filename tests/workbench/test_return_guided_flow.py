"""Guided v3 checks real MCP and persistent graph; LLM/vector are explicit substitutes."""

import json
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_return_graph import current, resume, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.return_models import ReturnRequest


@pytest.fixture
def guided_flow(return_flow, monkeypatch):  # noqa: F811
    case = return_flow
    client, app, headers, _, _ = case
    settings = app.state.settings
    settings.agent_router_mode, settings.return_decision_mode = "model", "model"
    settings.agent_router_url, settings.agent_router_model = (
        "https://model.invalid/chat/completions",
        "substitute",
    )
    settings.agent_router_key = SecretStr("test-only-key")
    calls = []

    def model(settings, observation, **kwargs):
        calls.append(observation)
        return {
            "proposal": json.dumps({"action": observation["allowed_actions"][0]}),
            "usage": {"total_tokens": 20},
        }

    monkeypatch.setattr("workbench.return_decisions.model_decision", model)
    response = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "model_alias": "server_default",
            "return_decisions": "model",
            "return_flow_version": "guided_v3",
        },
    )
    assert response.status_code == 201, response.text
    assert (
        client.post(
            f"/api/v1/runtime/config/versions/{response.json()['id']}/publish",
            headers=headers("admin"),
            json={"expected_revision": 0},
        ).status_code
        == 200
    )
    return case, calls


def accept(case, run, **extra):
    return case[0].post(
        f"/api/v1/runs/{run['id']}/resume",
        headers={**case[2](), "Idempotency-Key": str(uuid4())},
        json={
            "action": "accept_condition",
            "pause_id": run["output"]["pause_id"],
            "condition_proposal_hash": run["output"]["condition_proposal"]["proposal_hash"],
            **extra,
        },
    )


def test_candidate_needs_confirmation_then_can_reach_application_preview(guided_flow):
    case, calls = guided_flow
    run = start(case, return_condition="unknown", return_description="没用过，只拆了外包装。")
    assert run["status"] == "awaiting_input", run
    assert run["output"]["pause_kind"] == "condition_confirmation"
    assert run["output"]["condition_proposal"]["source"]["quote"] == "没用过"
    with case[1].state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ReturnRequest)) == 0
    assert accept(case, run).status_code == 202
    run = current(case, run["id"])
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["condition_origin"]["kind"] == "confirmed_description"
    assert run["output"]["proposal"]["return_condition"] == "unused"
    assert len(calls) == 4  # Two actual choices per observation cycle, forced steps skipped.
    assert all(len(c["allowed_actions"]) > 1 for c in calls)


def test_stale_or_mutating_acceptance_cannot_confirm_new_description(guided_flow):
    case, _ = guided_flow
    first = start(case, return_condition="unknown", return_description="未使用。")
    assert accept(case, first, return_condition="used").status_code == 422
    changed = resume(
        case, first, "supply", return_description="更正：已经使用过。", return_condition="unknown"
    )
    assert changed["status"] == "awaiting_input", changed
    assert changed["output"]["condition_proposal"]["candidate"] == "used"
    assert accept(case, first).status_code == 409
    assert accept(case, changed).status_code == 202
    done = current(case, changed["id"])
    assert done["status"] == "completed" and not done["output"].get("request")
    assert "configured_unused_condition_not_met" in done["output"]["business_checks"]["blocked"]


def test_uncertain_or_conflicting_description_does_not_become_unused(guided_flow):
    case, _ = guided_flow
    run = start(case, return_condition="unused", return_description="好像没用过，记不清了。")
    assert run["status"] == "awaiting_input", run
    assert run["output"]["pause_kind"] == "condition_clarification"
    assert run["output"]["known"]["return_condition"] == "unknown"
    run = resume(case, run, "supply", return_condition="unused")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["condition_origin"]["kind"] == "explicit_clarification"


def test_explicit_read_only_and_missing_fact_do_not_prompt_application(guided_flow):
    case, _ = guided_flow
    run = start(
        case,
        query="只想知道实际退款金额是多少，不要提交。",
        return_condition="unknown",
        return_description="如果没用过呢？",
    )
    assert run["status"] == "completed", run
    assert not run["output"].get("request")
    assert "未进入申请确认" in run["output"]["answer"]
    facts = run["output"]["fact_assessment"]
    assert facts["read_only"] and facts["items"][0]["field"] == "refund_amount"
    assert facts["items"][0]["status"] == "unknown"
    assert facts["related_policy_sources"]  # Related retrieval is not factual proof.


def test_missing_unrelated_bank_fact_does_not_refuse_valid_return(guided_flow):
    case, _ = guided_flow
    run = start(case, query="申请退货，顺便查一下银行是否到账。")
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["fact_assessment"]["items"][0]["status"] == "unknown"
    assert "当前工具不提供" in run["output"]["answer"]


def test_policy_required_unknown_fact_cannot_be_filled_by_user_claim(guided_flow):
    case, _ = guided_flow
    client, app, headers, body, policy = case
    response = client.post(
        "/api/v1/returns/policies",
        headers=headers("admin"),
        json={**policy, "required_facts": ["activation_status"]},
    )
    assert response.status_code == 201
    run = start(
        case, return_policy_id=response.json()["id"], return_description="没用过，没有激活。"
    )
    assert run["status"] == "completed", run
    assert "activation_status" in run["output"]["business_checks"]["missing"]
    assert not run["output"].get("request")


@pytest.mark.parametrize("field", ["activation_status", "member_entitlement", "quality_verdict"])
def test_required_fact_cannot_be_bypassed_through_direct_business_api(return_case, field):  # noqa: F811
    from test_returns import confirm, draft

    client, _, headers, body, policy = return_case
    response = client.post(
        "/api/v1/returns/policies",
        headers=headers("admin"),
        json={**policy, "required_facts": [field]},
    )
    assert response.status_code == 201
    body["policy_id"] = response.json()["id"]
    row = draft(return_case)
    result = confirm(return_case, row)
    assert result.status_code == 409, result.text
    assert field in str(result.json())


def test_scoped_guided_flow_with_no_choices_makes_zero_model_attempts(guided_flow):
    case, calls = guided_flow
    client, _, headers, _, _ = case
    head = client.get("/api/v1/runtime/config", headers=headers("admin")).json()
    version = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "model_alias": "server_default",
            "return_decisions": "model",
            "return_retrieval_mode": "policy_scoped",
        },
    ).json()
    assert (
        client.post(
            f"/api/v1/runtime/config/versions/{version['id']}/publish",
            headers=headers("admin"),
            json={"expected_revision": head["revision"]},
        ).status_code
        == 200
    )
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    assert not calls
    usage = client.get(f"/api/v1/runs/{run['id']}/usage", headers=headers()).json()
    assert usage["model_attempts"] == usage["model_reserved_tokens"] == 0
    assert all(row["mode"] == "fixed_action" for row in run["output"]["decisions"])
