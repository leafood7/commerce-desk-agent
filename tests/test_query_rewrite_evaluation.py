import sys
import unittest
from pathlib import Path
from collections import Counter
from langchain_core.messages import AIMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_query_rewrite import raw_query, evidence_metrics, extract_turn, load_cases, summary, parse_audit_json


class QueryRewriteTests(unittest.TestCase):
    def test_audit_only_repairs_missing_root_brace(self):
        self.assertEqual(parse_audit_json('{"answers":{}}'), ({'answers': {}}, ''))
        self.assertEqual(parse_audit_json('{"answers":{}'), ({'answers': {}}, '}'))
        with self.assertRaises(ValueError):
            parse_audit_json('{"a":true "b":false}')

    def test_history_and_latest_correction_are_retained(self):
        query = raw_query(['我是黑金会员', '刚才说错了，我是普通会员'])
        self.assertLess(query.index('黑金'), query.index('普通'))
        self.assertIn('以当前为准', query)
        self.assertEqual(raw_query(['问题']), '问题')

    def test_required_groups_and_unknown_are_distinct(self):
        case = {'answerable': True, 'evidence_groups': [['a', 'b'], ['c']]}
        self.assertEqual(evidence_metrics(case, [{'id': 'b'}])['coverage'], 0.5)
        self.assertTrue(evidence_metrics(case, [{'id': 'b'}, {'id': 'c'}])['all_required'])
        self.assertEqual(evidence_metrics({'answerable': False}, []), {'empty': True})

    def test_tool_call_ids_preserve_first_query_and_union(self):
        calls = [
            {'id': 'one', 'name': 'search_knowledge_base', 'args': {'query': '手机'}},
            {'id': 'two', 'name': 'search_knowledge_base', 'args': {'query': '退货'}},
            {'id': 'three', 'name': 'request_shipping', 'args': {'order_id': 'A1001'}},
        ]
        result = extract_turn([
            AIMessage(content='', tool_calls=calls),
            ToolMessage(content='{"matches":[{"id":"b"}]}', tool_call_id='two'),
            ToolMessage(content='{"status":"error","matches":[]}', tool_call_id='one'),
        ])
        self.assertEqual(result['queries'][0]['query'], '手机')
        self.assertEqual(result['query_matches']['one'], [])
        self.assertEqual(result['matches'], [{'id': 'b'}])
        self.assertEqual(len(result['tool_errors']), 1)
        self.assertEqual(result['other_tools'][0]['name'], 'request_shipping')

    def test_dataset_distribution_and_valid_references(self):
        cases = load_cases()
        self.assertEqual(len(cases), 30)
        self.assertEqual(sum(c['answerable'] for c in cases), 24)
        self.assertEqual(sorted(Counter(c['category'] for c in cases).values()), [6] * 5)

    def test_union_cannot_be_counted_as_first_query_gain(self):
        grade = {'correct': True, 'grounded': True, 'refusal': False}
        row = {
            'case': {'id': 'x', 'answerable': True},
            'raw': {'evidence_metric': {'all_required': True, 'coverage': 1}},
            'rewrite': {'first_query_metric': {'all_required': False, 'coverage': 0},
                        'evidence_metric': {'all_required': True}, 'queries': [{}, {}],
                        'other_tools': [], 'tool_errors': []},
            'audit': {'condition_checks': [{'preserved': True}],
                      'answers': {name: grade for name in ['actual_agent', 'raw_controlled', 'rewrite_controlled']}},
            'diagnostics': [],
        }
        result = summary([row])
        self.assertEqual(result['rewrite_losses'], ['x'])
        self.assertEqual(result['rewrite_gains'], [])
        self.assertEqual(result['queried_answerable_comparison']['losses'], ['x'])
        row['rewrite']['queries'] = []
        self.assertEqual(summary([row])['queried_answerable_comparison']['count'], 0)
        self.assertEqual(result['actual_agent_union_all_evidence_count'], 1)
        self.assertEqual(result['rewrite_first_query_all_evidence_count'], 0)


if __name__ == '__main__':
    unittest.main()
