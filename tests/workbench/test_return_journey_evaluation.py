import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from evaluate_return_journey import score
from return_journey_budget import AuditBudget, BatchStop


def sample():
    return (
        {
            "task": "information",
            "needed": ["scope"],
            "condition": "unknown",
            "expected_status": "completed",
        },
        {"terms": {"scope": "演示适用原句"}},
        [
            {
                "turn": 0,
                "run": {"status": "completed", "output": {"answer_gate": {"status": "approved"}}},
                "tools": [],
            }
        ],
    )


def test_evaluation_result_cannot_masquerade_as_retrieval():
    case, fixture, turns = sample()
    turns[0]["tools"] = [
        {
            "name": "evaluate_return",
            "result": {"preview": {"evidence": {"sources": [{"content": "演示适用原句"}]}}},
        }
    ]
    assert not score(case, fixture, turns)["question_evidence_complete"]
    turns[0]["tools"].append(
        {"name": "search_knowledge_base", "result": {"matches": [{"content": "演示适用原句"}]}}
    )
    assert score(case, fixture, turns)["question_evidence_complete"]


def test_information_pause_is_judged_by_oracle_not_runtime_keywords():
    case, fixture, turns = sample()
    turns[0]["run"] = {"status": "awaiting_input", "output": {"missing": ["return_condition"]}}
    assert score(case, fixture, turns)["unnecessary_pause"]
    case.update(task="application", expected_status="awaiting_input")
    assert not score(case, fixture, turns)["unnecessary_pause"]


def test_repeat_and_sufficient_evidence_reading_are_separate():
    case, fixture, turns = sample()
    result = {"version_id": "v", "parent_id": "p0", "offset": 0, "content": "演示适用原句"}
    turns[0]["tools"] = [
        {"name": "read_policy_source", "result": result},
        {"name": "read_policy_source", "result": result},
    ]
    value = score(case, fixture, turns)
    assert value["repeated_windows"] == 1
    assert len(value["unnecessary_reads"]) == 1
    assert value["question_evidence_complete"]


def test_correction_not_reached_cannot_be_reported_complete():
    case, fixture, turns = sample()
    case["continuation"] = {"return_quantity": 2}
    turns[0]["tools"] = [
        {"name": "search_knowledge_base", "result": {"matches": [{"content": "演示适用原句"}]}}
    ]
    assert not score(case, fixture, turns)["target_achieved"]


def budget(tmp_path):
    from evaluation.answer_trust_budget import BudgetLedger

    BudgetLedger(tmp_path / "ledger.json")
    return AuditBudget(tmp_path / "ledger.json", tmp_path / "out")


def test_budget_reserves_before_callback_and_keeps_unknown_usage(tmp_path):
    audit = budget(tmp_path)

    def callback():
        state = json.loads(audit.ledger.path.read_text("utf-8"))
        assert state["entries"][-1]["status"] == "reserved"
        return {"raw": "{}", "usage": None}

    audit.call("generate", [], 1024, callback)
    result = audit.summary()
    assert result["requests"] == 1 and result["unknown_calls"] == 1
    assert result["charged_cny"] > 0
    with pytest.raises(ValueError, match="refusing automatic replay"):
        AuditBudget(audit.ledger.path, tmp_path / "other")


def test_budget_hard_call_cap_stops_before_callback(tmp_path):
    from return_journey_budget import PHASE

    from evaluation.answer_trust_data import write_json

    audit = budget(tmp_path)
    with audit.ledger.transaction() as state:
        state["entries"] = [
            {"id": str(n), "metadata": {"phase": PHASE}, "charged_cny": 0, "status": "settled"}
            for n in range(128)
        ]
        write_json(audit.ledger.path, state)
    called = []
    with pytest.raises(BatchStop):
        audit.call("decision", [], 256, lambda: called.append(True))
    assert not called


def test_new_exploration_cases_share_cap_but_cannot_replay_prior_case(tmp_path):
    from return_journey_budget import PHASE

    from evaluation.answer_trust_data import write_json

    audit = budget(tmp_path)
    with audit.ledger.transaction() as state:
        state["entries"].append(
            {
                "id": "prior",
                "metadata": {"phase": PHASE, "case_id": "old"},
                "charged_cny": 0.1,
                "status": "settled",
            }
        )
        write_json(audit.ledger.path, state)
    with pytest.raises(ValueError):
        AuditBudget(audit.ledger.path, tmp_path / "repeat", allowed_cases={"old"})
    fresh = AuditBudget(audit.ledger.path, tmp_path / "fresh", allowed_cases={"new"})
    fresh.current = {"case_id": "old"}
    with pytest.raises(BatchStop):
        fresh.call("decision", [], 256, lambda: None)
    fresh.current = {"case_id": "new"}
    fresh.call(
        "decision", [], 256, lambda: {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    )
    assert fresh.summary()["requests"] == 2
    assert fresh.summary()["charged_cny"] > 0.1


def test_corrected_turn_cannot_count_removed_earlier_evidence():
    case, fixture, turns = sample()
    case["continuation"] = {"return_quantity": 2}
    turns[0]["tools"] = [
        {"name": "search_knowledge_base", "result": {"matches": [{"content": "演示适用原句"}]}}
    ]
    turns.append(
        {
            "turn": 1,
            "run": {"status": "completed", "output": {"answer_gate": {"status": "approved"}}},
            "tools": [],
        }
    )
    result = score(case, fixture, turns)
    assert result["ever_seen_quotes"] == 1
    assert result["covered_quotes"] == 0
    assert not result["target_achieved"]


def test_money_cap_stops_before_network_and_preserves_previous_spend(tmp_path):
    from return_journey_budget import PHASE

    from evaluation.answer_trust_data import write_json

    audit = budget(tmp_path)
    with audit.ledger.transaction() as state:
        state["entries"] = [
            {"id": "prior", "metadata": {"phase": PHASE}, "charged_cny": 0.999, "status": "settled"}
        ]
        write_json(audit.ledger.path, state)
    called = []
    with pytest.raises(BatchStop):
        audit.call("generate", [], 1024, lambda: called.append(True))
    assert not called
    assert audit.summary()["charged_cny"] == 0.999
