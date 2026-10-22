import json
import sqlite3
import unittest
from unittest.mock import patch

from agents.supportpilot_agent import get_order_status


class SupportPilotOrderTests(unittest.TestCase):
    def test_existing_order_uses_internal_database_function(self):
        order = {
            "order_id": "A1002", "product_name": "机械键盘", "shipping_status": "运输中"
        }
        with patch("agents.supportpilot_agent.get_order_from_db", return_value=order) as lookup:
            result = json.loads(get_order_status.invoke({"order_id": " a1002 "}))
        lookup.assert_called_once_with("A1002")
        self.assertEqual(result["shipping_status"], "运输中")

    def test_invalid_order_never_reads_database(self):
        with patch("agents.supportpilot_agent.get_order_from_db") as loader:
            result = get_order_status.invoke({"order_id": "A1002 OR 1=1"})
        loader.assert_not_called()
        self.assertIn("格式不正确", result)

    def test_unknown_order_is_reported(self):
        with patch("agents.supportpilot_agent.get_order_from_db", return_value=None):
            self.assertIn("未找到订单 A9999", get_order_status.invoke({"order_id": "A9999"}))

    def test_missing_database_is_reported(self):
        with patch(
            "agents.supportpilot_agent.get_order_from_db",
            side_effect=FileNotFoundError("数据库缺失"),
        ):
            self.assertIn("数据库缺失", get_order_status.invoke({"order_id": "A1002"}))

    def test_database_error_is_reported(self):
        with patch(
            "agents.supportpilot_agent.get_order_from_db",
            side_effect=sqlite3.OperationalError("invalid schema"),
        ):
            self.assertIn("暂时不可用", get_order_status.invoke({"order_id": "A1002"}))


if __name__ == "__main__":
    unittest.main()
