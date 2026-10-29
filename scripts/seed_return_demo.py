"""Add fresh, clearly labelled return demo orders and publish DEMO merchant terms.

Never updates existing orders or grants real refund rights. Uses the real ingestion
worker/index pipeline, then registers an explicitly human-authored rule mapping.
"""

import json
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.knowledge_models import DocumentVersion, KnowledgeChunk
from workbench.knowledge_schemas import VersionOptions
from workbench.knowledge_service import create_version, publish
from workbench.models import Order, OrderItem, Organization, User, now
from workbench.return_schemas import ReturnPolicyInput
from workbench.returns import register_policy


def main():
    settings = WorkbenchSettings()
    if settings.environment == "production":
        raise RuntimeError("Demo setup is disabled in production")
    database = Database(settings)
    suffix = uuid4().hex[:10].upper()
    sku = "DEMO-RETURN-" + suffix
    clauses = {
        "scope_clause": f"本演示规则仅适用于商品 {sku}，不适用于其他商品或真实平台。",
        "window_clause": "申请时间不得晚于业务系统实际签收时间之后的7天；预计到达时间不能代替实际签收。",
        "condition_clause": "仅演示未使用商品的无理由退货申请；已使用商品不适用本演示规则，其他原因须人工处理。",
    }
    text = (
        "# 演示商家退货规则\n\n"
        + "\n\n".join(clauses.values())
        + "\n\n受理为本地模拟，不执行真实退款。"
    )
    data = {"sku": sku, "orders": {}, "external_mode": "simulation"}
    with database.sessions() as session:
        org = session.scalar(select(Organization).where(Organization.slug == "demo-a"))
        if not org:
            raise RuntimeError("Initialize demo-a accounts before return setup")
        admin = session.scalar(
            select(User).where(User.organization_id == org.id, User.role == "admin")
        )
        for label, days in (("normal", 1), ("missing_receipt", None), ("expired", 8)):
            order = Order(
                organization_id=org.id,
                number="RET-" + label.upper() + "-" + suffix,
                customer_name="退货隔离演示",
                payment_status="paid",
                shipping_status="delivered",
                signed_at=now() - timedelta(days=days) if days else None,
                estimated_delivery="演示预计信息，非签收事实",
                data_source="demo_return_seed",
            )
            session.add(order)
            session.flush()
            item = OrderItem(
                organization_id=org.id,
                order_id=order.id,
                sku=sku,
                product_name="演示未使用商品",
                quantity=3,
                unit_price_cents=12345,
            )
            session.add(item)
            session.flush()
            data["orders"][label] = {"number": order.number, "item_id": item.id}
        session.commit()
        document, version, _ = create_version(
            session,
            admin,
            settings,
            "demo-return-" + suffix + ".md",
            text.encode("utf-8"),
            VersionOptions(policy_scope="returns"),
            title="退货规则演示 " + suffix,
        )
        version_id = version.id
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with database.sessions() as session:
            version = session.get(DocumentVersion, version_id)
            if version.status == "failed":
                raise RuntimeError("Demo document ingestion failed: " + str(version.error_code))
            if version.status == "ready":
                break
        time.sleep(1)
    else:
        raise RuntimeError("Demo ingestion timed out; document and orders retained")
    with database.sessions() as session:
        admin = session.get(User, admin.id)
        publish(session, admin, version_id)
        chunks = session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.version_id == version_id)
        ).all()
        refs = {}
        for purpose, quote in clauses.items():
            source = next((chunk for chunk in chunks if quote in chunk.content), None)
            if not source:
                raise RuntimeError("Demo clause was split; inspect chunks before mapping")
            refs[purpose] = {"chunk_id": source.id, "quote": quote}
        policy = register_policy(
            session,
            admin,
            ReturnPolicyInput(
                version_id=version_id,
                skus=[sku],
                window_days=7,
                requires_unused=True,
                administrator_verified=True,
                **refs,
            ),
        )
        data.update(
            policy_id=policy.id,
            version_id=version_id,
            document_id=version.document_id,
            clauses=refs,
        )
    target = Path("work/returns-v1/demo.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    database.close()
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
