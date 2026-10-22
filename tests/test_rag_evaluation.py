import sys
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_rag import case_metrics, evaluate, load_cases


class RagEvaluationTests(unittest.TestCase):
    def test_alternative_evidence_and_multiple_required_groups(self):
        case = {"answerable": True, "evidence_groups": [["a", "b"], ["c"]]}
        result = case_metrics(case, [{"id": "x"}, {"id": "b"}, {"id": "z"}])
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["mrr"], 0.5)
        self.assertTrue(result["hit"])

    def test_no_answer_empty_is_not_answer_accuracy(self):
        self.assertEqual(case_metrics({"answerable": False}, []), {"empty": True})
        self.assertEqual(case_metrics({"answerable": False}, [{"id": "related"}]), {"empty": False})

    def test_test_set_cannot_select_threshold(self):
        with self.assertRaisesRegex(ValueError, "禁止"):
            evaluate(Namespace(split="test", calibrate=True))

    def test_dataset_references_exist_and_topic_groups_are_separate(self):
        dev, test = load_cases("dev"), load_cases("test")
        self.assertTrue(dev and test)
        self.assertFalse({c["group"] for c in dev} & {c["group"] for c in test})
        self.assertTrue(any(not c["answerable"] for c in test))


if __name__ == "__main__":
    unittest.main()
