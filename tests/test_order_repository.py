import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from business.order_repository import (
    PROJECT_ROOT,
    get_database_path,
    get_order_from_db,
    initialize_demo_orders,
)


class OrderRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "orders.sqlite3"

    def test_creates_demo_database_and_queries_orders(self):
        self.assertEqual(initialize_demo_orders(self.database), 4)
        order = get_order_from_db("A1002", self.database)
        self.assertEqual(order["product_name"], "机械键盘")
        self.assertEqual(order["shipping_status"], "运输中")
        self.assertIsNone(get_order_from_db("A9999", self.database))

    def test_repeated_initialization_preserves_changes(self):
        initialize_demo_orders(self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE orders SET shipping_status = ? WHERE order_id = ?",
                ("已签收", "A1002"),
            )
        self.assertEqual(initialize_demo_orders(self.database), 4)
        self.assertEqual(get_order_from_db("A1002", self.database)["shipping_status"], "已签收")

    def test_missing_database_is_not_created_by_query(self):
        with self.assertRaises(FileNotFoundError):
            get_order_from_db("A1002", self.database)
        self.assertFalse(self.database.exists())

    def test_query_does_not_interpret_sql_in_order_id(self):
        initialize_demo_orders(self.database)
        self.assertIsNone(get_order_from_db("A1002' OR 1=1 --", self.database))

    def test_external_checkout_configuration_is_not_used(self):
        initialize_demo_orders(self.database)
        with patch.dict("os.environ", {"SUPPORTPILOT_ROOT": "Z:/does-not-exist"}):
            self.assertEqual(get_order_from_db("A1002", self.database)["order_id"], "A1002")

    def test_relative_database_path_is_relative_to_project(self):
        with patch.dict("os.environ", {"SUPPORTPILOT_DB_PATH": "data/test/orders.sqlite3"}):
            self.assertEqual(get_database_path(), PROJECT_ROOT / "data/test/orders.sqlite3")


if __name__ == "__main__":
    unittest.main()
