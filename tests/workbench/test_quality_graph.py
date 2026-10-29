"""Persistent graph plus actual HTTP MCP; vector/rerank are explicit substitutes."""
# ruff: noqa: F811

from test_quality import change, completed, create, policy
from test_return_graph import current, resume, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401


def test_agent_waits_reads_completed_inspection_and_preserves_approval(return_flow):
    case = return_flow
    run = start(case, return_policy_id=policy(case, "passed"), query="申请退货并核实质检结果")
    assert run["status"] == "awaiting_input", run
    assert run["output"]["pause_kind"] == "quality_wait"
    assert not run["output"].get("request")
    assert resume(case, run, "refresh")["output"]["pause_kind"] == "quality_wait"
    run = current(case, run["id"])
    row = completed(case)
    run = resume(case, run, "refresh")
    assert run["status"] == "awaiting_confirmation", run
    fact = run["output"]["fact_assessment"]["items"][0]
    assert fact["status"] == "available" and fact["value"] == "passed"
    assert fact["provenance"]["inspection_id"] == row["id"]
    run = resume(case, run, "confirm")
    assert run["status"] == "awaiting_approval", run
    assert run["output"]["request"]["status"] == "pending_approval"


def test_read_only_completed_failed_result_never_applies(return_flow):
    case = return_flow
    row = completed(case, "failed")
    run = start(case, query="只查质检结果，不要提交。")
    assert run["status"] == "completed", run
    assert not run["output"].get("request")
    assert run["output"]["fact_assessment"]["items"][0]["value"] == "failed"
    assert run["output"]["fact_assessment"]["items"][0]["provenance"]["inspection_id"] == row["id"]


def test_new_pending_record_never_reuses_previous_pass(return_flow):
    case = return_flow
    completed(case)
    row = create(case).json()
    run = start(case, return_policy_id=policy(case, "passed"))
    assert run["output"]["pause_kind"] == "quality_wait", run
    assert run["output"]["quality"]["inspection"]["id"] == row["id"]
    row = change(case, row, "start").json()
    change(case, row, "complete", verdict="failed")
    run = resume(case, run, "refresh")
    assert run["status"] == "completed" and not run["output"].get("request"), run
    assert "configured_quality_pass_not_met" in run["output"]["business_checks"]["blocked"]
