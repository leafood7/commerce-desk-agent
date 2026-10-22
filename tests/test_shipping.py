import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from business.order_repository import initialize_demo_orders
from business.shipping import execute_decision, list_requests, run_approval, submit_request


class ShippingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "orders.sqlite3"
        initialize_demo_orders(self.database)
        self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        self.update(promised_ship_at=(self.now - timedelta(hours=6)).isoformat())

    def update(self, **values):
        with closing(sqlite3.connect(self.database)) as db, db:
            for key, value in values.items():
                db.execute(f"UPDATE orders SET {key}=? WHERE order_id='A1004'", (value,))

    def submit(self, kind="expedite", order_id="A1004"):
        return submit_request(order_id, kind, database=self.database, now=self.now)

    def test_normal_registration_is_immediate_and_idempotent(self):
        first, second = self.submit("normal"), self.submit("normal")
        self.assertEqual(first["status"], "completed")
        self.assertTrue(first["ticket_id"])
        self.assertEqual(first["ticket_id"], second["ticket_id"])
        self.assertEqual(len(list_requests(self.database)), 1)

    def test_future_deadline_allows_normal_but_not_expedite(self):
        self.update(promised_ship_at=(self.now + timedelta(hours=1)).isoformat())
        self.assertEqual(self.submit()["status"], "ineligible")
        self.assertEqual(self.submit("normal")["status"], "completed")

    def test_unknown_shipped_unpaid_and_missing_deadline_are_rejected(self):
        self.assertEqual(self.submit(order_id="A9999")["status"], "ineligible")
        self.assertEqual(self.submit(order_id="A1002")["status"], "ineligible")
        self.update(payment_status="待付款")
        self.assertEqual(self.submit()["status"], "ineligible")
        self.update(payment_status="已付款", promised_ship_at=None)
        self.assertEqual(self.submit()["status"], "ineligible")

    def test_pending_request_creates_no_ticket(self):
        result = self.submit()
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["ticket_id"])

    def test_reject_never_creates_ticket(self):
        row = self.submit()
        result = execute_decision(row["request_id"], {"action": "reject", "reason": "资料不完整"}, self.database)
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["ticket_id"])
        self.assertEqual(result["decision_reason"], "资料不完整")

    def test_approve_rechecks_latest_order(self):
        row = self.submit()
        self.update(shipping_status="运输中")
        result = execute_decision(row["request_id"], {"action": "approve"}, self.database)
        self.assertEqual(result["status"], "invalidated")
        self.assertIsNone(result["ticket_id"])

    def test_duplicate_concurrent_approval_has_one_result(self):
        row = self.submit()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: execute_decision(row["request_id"], {"action": "approve"}, self.database), range(4)))
        self.assertEqual({r["status"] for r in results}, {"completed"})
        self.assertEqual(len({r["ticket_id"] for r in results}), 1)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM shipping_tickets").fetchone()[0], 1)

    def test_checkpoint_survives_graph_and_connection_recreation(self):
        row = self.submit()
        pending = run_approval(row["request_id"], database=self.database)
        self.assertEqual(pending["status"], "pending")
        result = run_approval(row["request_id"], {"action": "approve"}, self.database)
        self.assertEqual(result["status"], "completed")
        repeated = run_approval(row["request_id"], {"action": "reject"}, self.database)
        self.assertEqual(result["ticket_id"], repeated["ticket_id"])
        self.assertEqual(repeated["status"], "completed")

    def test_database_commit_before_checkpoint_can_be_recovered(self):
        row = self.submit()
        result = run_approval(row["request_id"], {"action": "reject"}, self.database)
        self.assertEqual(result["status"], "rejected")

    def test_invalid_action_has_no_side_effect(self):
        row = self.submit()
        with self.assertRaises(ValueError):
            execute_decision(row["request_id"], {"action": "yes"}, self.database)
        self.assertEqual(list_requests(self.database)[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
