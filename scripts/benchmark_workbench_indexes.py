"""Reproducible, isolated MySQL index experiment; no LLM calls."""

import argparse
import json
import os
import statistics
import time
from datetime import timedelta
from pathlib import Path

from alembic import command
from pydantic import SecretStr
from sqlalchemy import insert, select, text

from workbench.cli import migration_config
from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.models import (
    Conversation,
    Order,
    Organization,
    ShippingRequest,
    ShippingTicket,
    User,
    identifier,
    now,
)


def benchmark(database, output):
    if database.engine.dialect.name != "mysql" or not database.engine.url.database.startswith(
        "commercedesk_index_"
    ):
        raise ValueError("Use a dedicated commercedesk_index_* MySQL database")
    with database.sessions() as session:
        organization = session.scalar(select(Organization).where(Organization.slug == "bench-000"))
    if organization is None:
        with database.engine.begin() as connection:
            instant = now()
            organizations = [
                {"id": identifier(), "slug": f"bench-{i:03}", "name": f"Benchmark {i}"}
                for i in range(50)
            ]
            connection.execute(insert(Organization), organizations)
            users = [
                {
                    "id": identifier(),
                    "organization_id": org["id"],
                    "email": "index@demo.test",
                    "display_name": "Benchmark",
                    "role": "support",
                    "password_hash": "disabled-benchmark-account",
                    "active": False,
                }
                for org in organizations
            ]
            connection.execute(insert(User), users)
            orders, conversations, requests, tickets = [], [], [], []
            for i in range(10000):
                org, user = organizations[i % 50], users[i % 50]
                created = instant - timedelta(seconds=i * 11)
                order_id, request_id = identifier(), identifier()
                orders.append(
                    {
                        "id": order_id,
                        "organization_id": org["id"],
                        "number": f"BENCH-{i:05}",
                        "customer_name": "Synthetic",
                        "payment_status": "已付款",
                        "shipping_status": "等待仓库发货",
                        "currency": "CNY",
                        "data_source": "synthetic_capacity",
                        "created_at": created,
                    }
                )
                conversations.append(
                    {
                        "id": identifier(),
                        "organization_id": org["id"],
                        "owner_id": user["id"],
                        "title": f"Case {i}",
                        "created_at": created,
                        "updated_at": created,
                    }
                )
                pending = i % 7 == 0
                requests.append(
                    {
                        "id": request_id,
                        "organization_id": org["id"],
                        "order_id": order_id,
                        "kind": "expedite" if pending else "normal",
                        "reason": "capacity test",
                        "status": "pending" if pending else "completed",
                        "created_by": user["id"],
                        "result_message": "synthetic only",
                        "input_hash": str(i).zfill(64),
                        "created_at": created,
                    }
                )
                if not pending:
                    tickets.append(
                        {
                            "id": identifier(),
                            "organization_id": org["id"],
                            "order_id": order_id,
                            "request_id": request_id,
                            "kind": "normal",
                            "status": "open",
                            "created_at": created,
                        }
                    )
            for model, rows in [
                (Order, orders),
                (Conversation, conversations),
                (ShippingRequest, requests),
                (ShippingTicket, tickets),
            ]:
                for offset in range(0, len(rows), 500):
                    connection.execute(insert(model), rows[offset : offset + 500])
    with database.engine.connect() as connection:
        org = connection.execute(
            text("SELECT id FROM wb_organizations WHERE slug='bench-000'")
        ).scalar()
        owner = connection.execute(
            text("SELECT id FROM wb_users WHERE organization_id=:org"), {"org": org}
        ).scalar()
        order = connection.execute(
            text("SELECT order_id FROM wb_shipping_tickets WHERE organization_id=:org LIMIT 1"),
            {"org": org},
        ).scalar()
        samples = [
            (
                "recent_conversations",
                "wb_conversations",
                "ix_wb_conversation_recent",
                "WHERE organization_id=:org AND owner_id=:owner ORDER BY updated_at DESC, id DESC LIMIT 20",
            ),
            (
                "pending_approvals",
                "wb_shipping_requests",
                "ix_wb_shipping_pending",
                "WHERE organization_id=:org AND status='pending' ORDER BY created_at DESC, id DESC LIMIT 20",
            ),
            (
                "order_ticket_history",
                "wb_shipping_tickets",
                "ix_wb_ticket_history",
                "WHERE organization_id=:org AND order_id=:order ORDER BY created_at DESC, id DESC LIMIT 20",
            ),
        ]
        results = []
        for label, table, index, suffix in samples:
            record = {"name": label, "index": index}
            for arm, hint in [
                ("without_target_index", f"IGNORE INDEX ({index})"),
                ("with_available_indexes", ""),
            ]:
                sql = f"SELECT * FROM {table} {hint} {suffix}"
                parameters = {"org": org, "owner": owner, "order": order}
                plan = [
                    dict(row)
                    for row in connection.execute(text("EXPLAIN " + sql), parameters).mappings()
                ]
                times = []
                for repeat in range(6):
                    start = time.perf_counter()
                    rows = connection.execute(text(sql), parameters).fetchall()
                    elapsed = (time.perf_counter() - start) * 1000
                    if repeat:
                        times.append(elapsed)
                record[arm] = {
                    "plan": plan,
                    "median_ms": statistics.median(times),
                    "rows_returned": len(rows),
                    "timings_ms": times,
                }
            results.append(record)
        report = {
            "mysql_version": connection.scalar(text("SELECT VERSION()")),
            "synthetic": True,
            "orders": 10000,
            "organizations": 50,
            "queries": results,
            "limitations": "Local warm runs, five timings per arm; IGNORE INDEX excludes only the target index. No production latency or statistical significance claim.",
        }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url-env", default="WORKBENCH_BENCHMARK_DATABASE_URL")
    parser.add_argument("--output", default="docs/fullstack/index-benchmark.json")
    args = parser.parse_args()
    url = os.environ.get(args.url_env)
    if not url:
        parser.error("Set the database URL environment variable")
    settings = WorkbenchSettings(environment="test", database_url=SecretStr(url))
    database = Database(settings)
    if database.engine.dialect.name != "mysql" or not database.engine.url.database.startswith(
        "commercedesk_index_"
    ):
        parser.error("Refusing to seed a non-benchmark database")
    command.upgrade(migration_config(settings), "head")
    try:
        result = benchmark(database, args.output)
        print(
            json.dumps(
                {
                    "mysql": result["mysql_version"],
                    "orders": result["orders"],
                    "queries": len(result["queries"]),
                    "output": args.output,
                }
            )
        )
    finally:
        database.close()


if __name__ == "__main__":
    main()
