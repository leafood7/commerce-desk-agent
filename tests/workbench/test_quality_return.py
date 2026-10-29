"""Explicit demo branch gates; no statutory policy or automatic defect inference."""
# ruff: noqa: F811

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_quality import change, completed, create
from test_return_graph import current, resume, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.models import Order, now
from workbench.return_context import project_policy_context
from workbench.return_observations import decision_observation
from workbench.return_schemas import ReturnInput


def quality_policy(case):
    response = case[0].post(
        "/api/v1/returns/policies",
        headers=case[2]("admin"),
        json={
            **case[4],
            "return_basis": "quality_issue",
            "window_days": 30,
            "requires_unused": False,
            "required_facts": ["quality_verdict"],
            "quality_requirement": "defect_confirmed",
            "quality_clause": case[4]["condition_clause"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def inspect(case, finding="defect_confirmed"):
    row = create(case).json()
    row = change(case, row, "start").json()
    verdict = {"defect_confirmed": "failed", "no_defect": "passed", "unclear": "inconclusive"}[
        finding
    ]
    response = change(case, row, "complete", verdict=verdict, finding=finding)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "days,condition,basis,finding,expected",
    [
        (10, "used", "quality_issue", "defect_confirmed", "eligible"),
        (31, "used", "quality_issue", "defect_confirmed", "blocked"),
        (2, "used", "quality_issue", "no_defect", "blocked"),
        (2, "used", "quality_issue", "unclear", "needs_information"),
        (2, "used", "no_reason", "defect_confirmed", "blocked"),
    ],
)
def test_branch_window_usage_and_finding(return_case, days, condition, basis, finding, expected):  # noqa: F811
    case = return_case
    with case[1].state.database.sessions() as session:
        order = session.scalar(select(Order).where(Order.number == case[3]["order_number"]))
        order.signed_at = now() - timedelta(days=days)
        session.commit()
    inspect(case, finding)
    body = {
        **case[3],
        "policy_id": quality_policy(case),
        "return_basis": basis,
        "item_condition": condition,
    }
    response = case[0].post("/api/v1/returns/evaluate", headers=case[2](), json=body)
    assert response.status_code == 200, response.text
    assert response.json()["eligibility"] == expected, response.json()


def test_legacy_failed_is_not_confirmed_defect(return_case):  # noqa: F811
    case = return_case
    completed(case, "failed")
    preview = (
        case[0]
        .post(
            "/api/v1/returns/evaluate",
            headers=case[2](),
            json={**case[3], "policy_id": quality_policy(case), "return_basis": "quality_issue"},
        )
        .json()
    )
    assert "quality_defect_finding" in preview["missing"]
    assert preview["quality"]["inspection"]["finding"] is None


def test_conflicting_finding_is_rejected(return_case):  # noqa: F811
    row = create(return_case).json()
    row = change(return_case, row, "start").json()
    assert (
        change(
            return_case, row, "complete", verdict="passed", finding="defect_confirmed"
        ).status_code
        == 422
    )


def test_projection_does_not_invent_retrieval_or_admit_other_branch():
    match = {"id": "same", "version_id": "doc", "content": "无理由7天。质量问题30天。"}
    clauses = [{"chunk_id": "same", "purpose": "window", "quote": "质量问题30天。"}]
    assert project_policy_context([], clauses) == []
    assert (
        project_policy_context([match], [{"chunk_id": "missing", "quote": "质量问题30天。"}]) == []
    )
    context = project_policy_context([match], clauses)
    assert context[0]["content"] == "质量问题30天。"
    assert (
        match["content"][context[0]["excerpts"][0]["start"] : context[0]["excerpts"][0]["end"]]
        == context[0]["content"]
    )
    observation = decision_observation(
        {"context_bound": True, "policy_context": [], "evidence": [match]}, ["stop"]
    )
    assert observation["evidence"] == []


def test_default_payload_keeps_legacy_shape():
    from workbench.return_fact_checks import return_reasons

    assert "无理由" not in return_reasons(["outside_configured_return_window"])
    body = ReturnInput(order_number="A1", item_id="i", policy_id="p", quantity=1, reason="demo")
    assert "return_basis" not in body.model_dump(mode="json")
    assert (
        body.model_copy(update={"return_basis": "quality_issue"}).model_dump()["return_basis"]
        == "quality_issue"
    )


def test_agent_correction_rechecks_branch_and_does_not_reuse_exceptions(return_flow):
    case = return_flow
    inspect(case)
    run = start(
        case,
        return_policy_id=quality_policy(case),
        return_basis="quality_issue",
        return_condition="used",
    )
    assert run["status"] == "awaiting_confirmation", run
    assert run["output"]["proposal"]["return_basis"] == "quality_issue"
    run = resume(
        case, run, "correct", return_basis="no_reason", return_policy_id=case[3]["policy_id"]
    )
    assert run["status"] == "completed", run
    assert "configured_unused_condition_not_met" in run["output"]["business_checks"]["blocked"]
    assert not run["output"].get("request")


def test_agent_waits_for_explicit_finding_then_submits_correct_basis(return_flow):
    case = return_flow
    completed(case, "failed")
    run = start(
        case,
        return_policy_id=quality_policy(case),
        return_basis="quality_issue",
        return_condition="used",
    )
    assert run["output"]["pause_kind"] == "quality_wait", run
    inspect(case)
    run = resume(case, run, "refresh")
    assert run["status"] == "awaiting_confirmation", run
    run = resume(case, run, "confirm")
    assert run["status"] == "awaiting_approval", run
    assert run["output"]["request"]["return_basis"] == "quality_issue"
