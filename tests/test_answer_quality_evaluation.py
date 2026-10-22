import sys
import hashlib
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate_answer_quality import read_cases,variant_cost,cost_summary


class AnswerQualityEvaluationTests(unittest.TestCase):
    def test_frozen_case_manifest_and_distribution(self):
        manifest,cases=read_cases()
        self.assertEqual(len(cases),18)
        self.assertEqual(sum(c['group']=='known' for c in cases),3)
        self.assertEqual(sum(c['group']=='unknown' for c in cases),2)
        self.assertTrue(all(c['turns'] and c['expectation'] for c in cases))
        self.assertIn('work/answer-quality-baseline/supportpilot_agent.py',manifest)

    def test_published_baseline_bytes_match_frozen_manifest(self):
        manifest,_=read_cases()
        directory=Path(__file__).resolve().parents[1]/"data/evaluation/baselines/answer_quality"
        for filename,expected in manifest.items():
            if filename.startswith("work/"):
                with self.subTest(filename=filename):
                    self.assertEqual(hashlib.sha256((directory/Path(filename).name).read_bytes()).hexdigest(),expected)

    def test_skipped_decision_is_not_counted_as_model_call(self):
        turn={'policy_check':{'status':'skipped','checker_called':False,'usage':{}},
            'queries':[],'tool_errors':[],'other_tools':[],'answer_calls':[{'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}}], 'elapsed_ms':100}
        cost=variant_cost({'turns':[turn]})
        self.assertEqual(cost['checker_calls'],0)
        self.assertEqual(cost['answer_calls'],1)
        self.assertEqual(cost['total_tokens_reported'],12)
        self.assertEqual(cost['missing_usage_calls'],0)
        turn['policy_check']={'status':'checked','usage':{'total_tokens':5}}
        self.assertEqual(variant_cost({'turns':[turn]})['checker_calls'],1)
        self.assertEqual(variant_cost({'turns':[turn]})['total_tokens_reported'],17)

    def test_cost_ablation_only_compares_same_cases(self):
        def v(milliseconds):
            return {'turns':[{'policy_check':{},'answer_calls':[],'elapsed_ms':milliseconds}]}
        r={'rows':[{'case':{'id':'greet'},'variants':{'before':v(50),'after':v(10),'after_always':v(30)}},
                   {'case':{'id':'policy'},'variants':{'before':v(50),'after':v(40)}}]}
        s=cost_summary(r)
        self.assertEqual(s['variants']['after']['cases'],2)
        self.assertEqual(s['same_prompt_cost_subset']['case_ids'],['greet'])
        self.assertEqual(s['same_prompt_cost_subset']['auto']['elapsed_ms'],10)
        self.assertEqual(s['same_prompt_cost_subset']['always']['elapsed_ms'],30)
        r['rows'][0]['variants']['after_always']['turns'][0]['policy_check']={'status':'error','checker_called':True}
        clean=cost_summary(r)['clean_same_prompt_cost_subset']
        self.assertEqual(clean['case_ids'],[])
        self.assertEqual(clean['excluded_ids'],['greet'])
