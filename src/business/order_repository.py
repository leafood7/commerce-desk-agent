"""Local order storage independent of the original SupportPilot checkout."""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_DATA = PROJECT_ROOT / "data" / "demo_orders.json"


def get_database_path(database: Path | None = None) -> Path:
    path = database or Path(
        os.environ.get("SUPPORTPILOT_DB_PATH", "data/supportpilot/orders.sqlite3")
    )
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def initialize_demo_orders(database: Path | None = None) -> int:
    path = get_database_path(database)
    orders = json.loads(DEMO_DATA.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                product_name TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                shipping_status TEXT NOT NULL,
                estimated_delivery TEXT NOT NULL,
                hours_since_payment INTEGER NOT NULL
            )
        """)
        connection.executemany("""
            INSERT OR IGNORE INTO orders (
                order_id, customer_name, product_name, payment_status,
                shipping_status, estimated_delivery, hours_since_payment
            ) VALUES (
                :order_id, :customer_name, :product_name, :payment_status,
                :shipping_status, :estimated_delivery, :hours_since_payment
            )
        """, orders)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
        for name in ("paid_at", "promised_ship_at"):
            if name not in columns:
                connection.execute(f"ALTER TABLE orders ADD COLUMN {name} TEXT")
        now = datetime.now(timezone.utc)
        for order in orders:
            paid_at = now - timedelta(hours=order["hours_since_payment"])
            promised = paid_at + timedelta(hours=48)
            connection.execute(
                "UPDATE orders SET paid_at = ?, promised_ship_at = ? "
                "WHERE order_id = ? AND promised_ship_at IS NULL",
                (paid_at.isoformat(), promised.isoformat(), order["order_id"]),
            )
        connection.execute("""
            CREATE TABLE IF NOT EXISTS shipping_requests (
                request_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                kind TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, decided_at TEXT, decision_reason TEXT,
                ticket_id TEXT UNIQUE, result_message TEXT, decided_by TEXT,
                UNIQUE(order_id, kind)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS shipping_tickets (
                ticket_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                order_id TEXT NOT NULL, kind TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                FOREIGN KEY(request_id) REFERENCES shipping_requests(request_id)
            )
        """)
        return connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def get_order_from_db(order_id: str, database: Path | None = None) -> dict | None:
    path = get_database_path(database)
    if not path.is_file():
        raise FileNotFoundError(
            "订单数据库尚未初始化，请先运行 python scripts/init_demo_orders.py。"
        )
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("""
            SELECT order_id, customer_name, product_name, payment_status,
                   shipping_status, estimated_delivery, hours_since_payment,
                   paid_at, promised_ship_at
            FROM orders WHERE order_id = ?
        """, (order_id,)).fetchone()
    return dict(row) if row is not None else None
