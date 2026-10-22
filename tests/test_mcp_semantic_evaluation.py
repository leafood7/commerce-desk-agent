import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from evaluate_mcp_semantics import validate_row

class SemanticTraceTests(unittest.TestCase):
 def test_missing_mcp_trace_is_not_success(self):
  self.assertIn('turn1: MCP trace count mismatch',validate_row({'turns':[{'queries':[{}]}]}))
 def test_citation_requires_actual_evidence(self):
  self.assertIn('turn1: citation without evidence',validate_row({'turns':[{'answer':'来源：invented_policy.txt'}]}))
 def test_reuse_cannot_reference_unknown_id(self):
  self.assertIn('turn1: invalid reuse lineage',validate_row({'turns':[{'policy_check':{'mode':'reuse','evidence_ids':['fake']}}]}))
 def test_write_call_is_flagged_even_when_blocked(self):
  self.assertIn('turn1: unexpected write tool',validate_row({'turns':[{'other_tools':[{'name':'request_shipping'}]}]}))
 def test_completed_mcp_and_cross_turn_evidence_are_valid(self):
  row={'turns':[{'queries':[{}],'matches':[{'id':'c-0','source':'coupon.txt'}], 'tool_trace':[{'event':'start','run_id':'r','metadata':{'server':'commerce-desk-readonly'}},{'event':'end','run_id':'r'}]}, {'policy_check':{'mode':'reuse','evidence_ids':['c-0']},'answer':'来源coupon.txt'}]}
  self.assertEqual(validate_row(row),[])
