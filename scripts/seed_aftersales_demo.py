"""Add labelled, isolated demo orders for repeatable browser acceptance."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.models import Order, OrderItem, Organization, now


def main():
    settings = WorkbenchSettings()
    if settings.environment != "development":
        raise RuntimeError("Demo acceptance data is development-only")
    database = Database(settings)
    database.check_schema()
    prefix = "AF" + uuid4().hex[:10].upper()
    manifest = {}
    with database.sessions() as session:
        organization = session.scalar(select(Organization).where(Organization.slug == "demo-a"))
        if not organization:
            raise RuntimeError("Seed demo organizations first")
        for purpose in ("normal", "expedite", "correction", "missing"):
            order = Order(
                organization_id=organization.id,
                number=prefix + purpose[0].upper(),
                customer_name="售后浏览器验收",
                payment_status="已付款",
                shipping_status="等待仓库发货",
                estimated_delivery=None,
                paid_at=now() - timedelta(hours=72),
                promised_ship_at=None if purpose == "missing" else now() - timedelta(hours=24),
                data_source="aftersales_acceptance",
            )
            session.add(order)
            session.flush()
            session.add(
                OrderItem(
                    organization_id=organization.id,
                    order_id=order.id,
                    sku=prefix + purpose,
                    product_name="售后验收商品",
                    quantity=1,
                    unit_price_cents=10000,
                )
            )
            manifest[purpose] = order.number
        session.commit()
    path = Path("work/fullstack/aftersales-demo.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    database.close()
    print("Four labelled browser acceptance orders created; original orders untouched.")


if __name__ == "__main__":
    main()
