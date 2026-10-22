import json
import unittest
from unittest.mock import patch

from agents.supportpilot_agent import search_knowledge_base


class PolicyToolTests(unittest.TestCase):
    def test_returns_evidence_and_real_source(self):
        matches = [{"id": "refund-0", "content": "3 至 7 个工作日", "source": "refund_policy.txt",
                    "distance": 0.2}]
        with patch("agents.supportpilot_agent.search_policies", return_value=matches):
            result = json.loads(search_knowledge_base.invoke({"query": "退款多久"}))
        self.assertEqual(result, {"status": "found", "matches": matches})

    def test_no_match_is_explicit(self):
        with patch("agents.supportpilot_agent.search_policies", return_value=[]):
            result = json.loads(search_knowledge_base.invoke({"query": "公司创始人"}))
        self.assertEqual(result["status"], "no_evidence")
        self.assertEqual(result["matches"], [])

    def test_retrieval_error_is_not_reported_as_a_valid_policy(self):
        with patch("agents.supportpilot_agent.search_policies", side_effect=RuntimeError("offline")), \
             self.assertLogs("agents.supportpilot_agent", level="ERROR"):
            result = json.loads(search_knowledge_base.invoke({"query": "退款多久"}))
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
