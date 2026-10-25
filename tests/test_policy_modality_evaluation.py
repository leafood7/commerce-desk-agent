import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluate_policy_modality import load_cases, paired_prompts, select_cases


class PolicyModalityEvaluationTests(unittest.TestCase):
    def test_published_cases_match_real_policy_text(self):
        cases = load_cases()
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case['id'] for case in cases}), 20)
        self.assertEqual(sum(case['id'].startswith('modal-') for case in cases), 12)
        self.assertEqual(sum(case['id'].startswith('fresh-') for case in cases), 6)

    def test_tampered_evidence_is_rejected(self):
        cases = load_cases()
        cases[0]['evidence'][0]['content'] = '编造的政策'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cases.json'
            path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_cases(path)

    def test_duplicate_id_is_rejected(self):
        cases = load_cases()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cases.json'
            path.write_text(json.dumps([cases[0], cases[0]]), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_cases(path)

    def test_invalid_selection_cannot_silently_shrink_evaluation(self):
        cases = load_cases()
        self.assertEqual([case['id'] for case in select_cases(cases, 'known-015')], ['known-015'])
        with self.assertRaises(ValueError):
            select_cases(cases, 'known-015,unknown-id')

    def test_pair_reuses_exact_base_template(self):
        prompts = paired_prompts()
        self.assertNotEqual(prompts['before'], prompts['after'])
        template = prompts['before'].split('\n回答前区分三件事：', 1)[0]
        self.assertTrue(prompts['after'].startswith(template + '\n回答前区分三件事：'))


if __name__ == '__main__':
    unittest.main()
