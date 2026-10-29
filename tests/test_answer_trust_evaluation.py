"""Offline checks for the saved experiment's real data and accounting boundaries."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.answer_trust_budget import BudgetLedger, BudgetStop, cost
from evaluation.answer_trust_data import load_cases, variant_order


class AnswerTrustEvaluationTests(unittest.TestCase):
    def test_dev_selection_rejects_unknown_and_duplicate_ids(self):
        # Never load the sealed holdout content.
        self.assertEqual(len(load_cases("dev")), 24)
        with self.assertRaises(ValueError):
            load_cases("dev", ids="not-a-case")
        with self.assertRaises(ValueError):
            load_cases("dev", ids="trust-dev-001,trust-dev-001")

    def test_orders_rotate_without_omitting_a_variant(self):
        rows = [variant_order(i) for i in range(3)]
        self.assertEqual({row[0] for row in rows}, {"baseline", "contract", "guarded"})
        for row in rows:
            self.assertEqual(set(row), {"baseline", "contract", "guarded"})

    def test_unknown_usage_keeps_reservation_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            ledger = BudgetLedger(path)
            token = ledger.reserve()
            before = ledger.snapshot()["committed_cny"]
            ledger.settle(token, None, error_type="TimeoutError")
            reopened = BudgetLedger(path)
            self.assertEqual(reopened.snapshot()["committed_cny"], before)
            self.assertEqual(reopened.snapshot()["unknown_or_pending_calls"], 1)
            with self.assertRaises(BudgetStop):
                reopened.settle(token, {"input_tokens": 1, "output_tokens": 1})

    def test_settlement_returns_unused_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = BudgetLedger(Path(directory) / "budget.json")
            token = ledger.reserve()
            ledger.settle(token, {"input_tokens": 100, "output_tokens": 20})
            self.assertAlmostEqual(ledger.snapshot()["committed_cny"], cost(100, 20))

    def test_deleted_ledger_cannot_reset_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "budget.json"
            ledger = BudgetLedger(path)
            ledger.reserve()
            path.unlink()
            with self.assertRaises(BudgetStop):
                BudgetLedger(path)

    def test_invalid_split_is_rejected_before_reading(self):
        with patch("evaluation.answer_trust_data.validate_frozen") as validate:
            with self.assertRaises(ValueError):
                load_cases("invented")
            validate.assert_not_called()
