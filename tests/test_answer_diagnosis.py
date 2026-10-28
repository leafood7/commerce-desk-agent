"""诊断资源边界及冻结输入测试，不调用真实模型。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.answer_diagnosis import (
    ANSWER_LIMIT,
    PHASE,
    DiagnosticLedger,
    prepare,
    validate,
)
from evaluation.answer_trust_budget import BudgetStop


class DiagnosisBudgetTests(unittest.TestCase):
    def test_stage_limit_and_unknown_usage_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "budget.json"
            ledger = DiagnosticLedger(path)
            for _ in range(9):
                ledger.settle(ledger.reserve(), None)
            restarted = DiagnosticLedger(path)
            self.assertEqual(restarted.stage_snapshot()["unknown_or_pending_calls"], 9)
            with self.assertRaises(BudgetStop):
                restarted.reserve()
            self.assertLessEqual(restarted.stage_snapshot()["stage_committed_cny"], 20)

    def test_global_spend_and_stage_spend_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = DiagnosticLedger(Path(temp) / "budget.json")
            with ledger.transaction() as state:
                state["entries"] = [
                    {
                        "id": "earlier",
                        "charged_cny": 99,
                        "status": "settled",
                        "metadata": {"phase": "earlier-project-work"},
                    }
                ]
                from evaluation.answer_trust_data import write_json

                write_json(ledger.path, state)
            self.assertEqual(ledger.stage_snapshot()["stage_committed_cny"], 0)
            with self.assertRaises(BudgetStop):
                ledger.reserve()

    def test_answer_limit_is_durable_even_if_attempt_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "budget.json"
            ledger = DiagnosticLedger(path)
            for _ in range(ANSWER_LIMIT):
                ledger.answer_slot({"phase": PHASE})
            with self.assertRaises(BudgetStop):
                DiagnosticLedger(path).answer_slot({"phase": PHASE})


class DiagnosisDataTests(unittest.TestCase):
    def test_prepare_freezes_ten_cases_without_opening_holdout(self):
        original = Path.read_text

        def safe_read(path, *args, **kwargs):
            self.assertNotIn("holdout", str(path))
            return original(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(Path, "read_text", safe_read):
            manifest = prepare(Path(temp))
            self.assertEqual((manifest["case_count"], manifest["initial_turns"]), (10, 12))
            self.assertEqual(validate(Path(temp)), manifest)
            case_path = Path(temp) / "cases.json"
            cases = json.loads(case_path.read_text(encoding="utf-8"))
            self.assertEqual(
                next(c for c in cases if c["id"] == "new-name")["fault_injection"],
                "empty_policy_after_real_mcp",
            )
            case_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "样本被修改"):
                validate(Path(temp))


class DiagnosisInterventionTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {"id": "p-0", "source": "p.txt", "content": "真实原文"}
        self.extra = {"id": "q-0", "source": "q.txt", "content": "另一条真实原文"}
        self.messages = [
            {"type": "system", "content": "固定系统提示"},
            {"type": "human", "content": "固定用户条件"},
            {
                "type": "ai",
                "content": "",
                "tool_calls": [{"id": "lookup", "name": "search_knowledge_base"}],
            },
            {
                "type": "tool",
                "tool_call_id": "lookup",
                "content": json.dumps({"status": "found", "matches": [self.evidence, self.extra]}),
            },
        ]

    def test_filter_changes_only_evidence_and_preserves_source_request(self):
        from evaluation.answer_diagnosis_pairs import transform_request

        before = json.dumps(self.messages)
        changed = transform_request(
            self.messages, {"kind": "filter_sources", "sources": ["p.txt"]}, {}
        )
        self.assertEqual(json.dumps(self.messages), before)
        self.assertEqual(changed[:-1], self.messages[:-1])
        self.assertEqual(json.loads(changed[-1]["content"])["matches"], [self.evidence])

    def test_filter_cannot_add_evidence_and_addition_must_be_new(self):
        from evaluation.answer_diagnosis_pairs import transform_request

        with self.assertRaisesRegex(ValueError, "不能新增"):
            transform_request(self.messages, {"kind": "filter_ids", "ids": ["missing"]}, {})
        with self.assertRaisesRegex(ValueError, "已存在"):
            transform_request(
                self.messages, {"kind": "add_missing", "ids": ["p-0"]}, {"p-0": self.evidence}
            )


if __name__ == "__main__":
    unittest.main()
