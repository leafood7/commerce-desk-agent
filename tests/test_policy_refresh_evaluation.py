import sys
import unittest
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_policy_refresh import load_cases, summarize


class PolicyRefreshEvaluationTests(unittest.TestCase):
    def test_new_cases_have_valid_evidence_and_frozen_categories(self):
        _, cases = load_cases("holdout")
        self.assertEqual(len(cases), 20)
        self.assertEqual(Counter(c["category"] for c in cases), {"条件更正": 8, "多轮补充": 4, "复用证据": 4, "无答案": 4})
        self.assertEqual(sum(not c["answerable"] for c in cases), 4)

    def test_truthy_skipped_check_is_not_a_model_call(self):
        def variant(check):
            return {"turns":[{"queries":[],"policy_check":check,"other_tools":[],"tool_errors":[]}],
                    "evidence_metric":{"all_required":True}}
        grade={"correct":True,"grounded":True,"refusal":False}
        row={"case":{"id":"greeting","answerable":True},
             "before":variant({"status":"checked"}),
             "after":variant({"status":"skipped","checker_called":False}),
             "audit":{"before":grade,"after":grade}}
        result=summarize([row])
        self.assertEqual(result["variants"]["before"]["checker_calls"],1)
        self.assertEqual(result["variants"]["after"]["checker_calls"],0)

    def test_gain_does_not_hide_loss_or_tool_error(self):
        def row(identifier, before, after):
            turn = {"queries": [], "policy_check": {}, "other_tools": [], "tool_errors": []}
            variant = {"turns": [turn], "evidence_metric": {"all_required": True}}
            return {"case": {"id": identifier, "answerable": True}, "before": variant, "after": variant,
                    "audit": {"before": {"correct": before, "grounded": True, "refusal": False},
                              "after": {"correct": after, "grounded": True, "refusal": False}}}
        result = summarize([row("gain", False, True), row("loss", True, False)])
        self.assertEqual(result["answer_gains"], ["gain"])
        self.assertEqual(result["answer_losses"], ["loss"])
        self.assertEqual(result["variants"]["after"]["correct"], 1)


if __name__ == "__main__":
    unittest.main()
