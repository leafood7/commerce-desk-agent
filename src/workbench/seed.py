"""Explicit, idempotent demonstration data; never a startup-created backdoor."""

import json
from datetime import timedelta

from sqlalchemy import select

from workbench.config import ROOT
from workbench.models import Order, OrderItem, Organization, User, now
from workbench.security import password_hash


def seed_demo(database, password):
    if len(password) < 12:
        raise ValueError("Demo password must have at least 12 characters")
    rows = json.loads((ROOT / "data/demo_orders.json").read_text(encoding="utf-8"))
    result = []
    with database.sessions() as session:
        for slug in ("demo-a", "demo-b"):
            organization = session.scalar(select(Organization).where(Organization.slug == slug))
            if organization is None:
                organization = Organization(slug=slug, name="演示商家 " + slug[-1].upper())
                session.add(organization)
                session.flush()
            for role in ("admin", "support", "approver"):
                email = role + "@demo.test"
                user = session.scalar(
                    select(User).where(User.organization_id == organization.id, User.email == email)
                )
                if user is None:
                    user = User(
                        organization_id=organization.id,
                        email=email,
                        display_name=role,
                        role=role,
                        password_hash=password_hash(password),
                    )
                    session.add(user)
                result.append({"organization": slug, "email": email, "role": role})
            session.flush()
            for index, row in enumerate(rows, 1):
                number = row["order_id"] if slug == "demo-a" else f"B{2000 + index}"
                order = session.scalar(
                    select(Order).where(
                        Order.organization_id == organization.id, Order.number == number
                    )
                )
                if order:
                    continue
                paid_at = now() - timedelta(hours=row["hours_since_payment"])
                order = Order(
                    organization_id=organization.id,
                    number=number,
                    customer_name=row["customer_name"] + " (" + slug + ")",
                    payment_status=row["payment_status"],
                    shipping_status=row["shipping_status"],
                    estimated_delivery=row["estimated_delivery"],
                    paid_at=paid_at,
                    promised_ship_at=paid_at + timedelta(hours=48),
                    # Explicit simulated receipt fact, not inferred from the ETA.
                    signed_at=now() - timedelta(hours=24) if index == 3 else None,
                    data_source="demo_seed",
                )
                session.add(order)
                session.flush()
                session.add(
                    OrderItem(
                        organization_id=organization.id,
                        order_id=order.id,
                        sku="DEMO-" + str(index),
                        product_name=row["product_name"],
                        quantity=1,
                        unit_price_cents=19900 + index * 100,
                    )
                )
            session.commit()
    return result
