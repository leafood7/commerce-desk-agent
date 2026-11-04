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
        "scope_clause": f"本规则适用于商品编码 {sku}；其他商品须依据各自适用规则核对。",
        "window_clause": "申请时间不得晚于业务系统实际签收时间之后的7天；预计到达时间不能代替实际签收。",
        "condition_clause": "无理由退货仅适用于未使用商品；已使用商品的其他退货原因须人工核对。",
        "quality_clause": "仅对管理员登记为需质检的规则分支，要求取得同一订单商品行及申请数量对应的有效质检通过结论；其他分支不增加此要求。",
        "quality_issue_window": "质量问题退货分支限实际签收30天内；不能延长无理由退货的7天窗口。",
        "quality_issue_condition": "质量问题分支允许已使用商品，但须由独立处理人针对同一订单商品行及申请数量登记明确的缺陷结论；质检未通过、用户自述或照片不能替代该结论。",
    }
    text = (
        "# 商家退货规则\n\n"
        + "\n\n".join(clauses.values())
    )
    data = {"sku": sku, "orders": {}, "external_mode": "simulation"}
    with database.sessions() as session:
        org = session.scalar(select(Organization).where(Organization.slug == "demo-a"))
        if not org:
            raise RuntimeError("Initialize demo-a accounts before return setup")
        admin = session.scalar(
            select(User).where(User.organization_id == org.id, User.role == "admin")
        )
        for label, days in (("normal", 1), ("missing_receipt", None), ("expired", 8), ("quality_window", 10)):
            order = Order(
                organization_id=org.id,
                number="RET-" + label.upper() + "-" + suffix,
                customer_name="退货客户",
                payment_status="paid",
                shipping_status="delivered",
                signed_at=now() - timedelta(days=days) if days else None,
                estimated_delivery="预计送达信息，仅供参考",
                data_source="demo_return_seed",
            )
            session.add(order)
            session.flush()
            item = OrderItem(
                organization_id=org.id,
                order_id=order.id,
                sku=sku,
                product_name="待退商品",
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
            "return-policy-" + suffix + ".md",
            text.encode("utf-8"),
            VersionOptions(policy_scope="returns"),
            title="商家退货规则 " + suffix,
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
                **{k: refs[k] for k in ("scope_clause", "window_clause", "condition_clause")},
            ),
        )
        quality_policy = register_policy(
            session,
            admin,
            ReturnPolicyInput(
                version_id=version_id,
                skus=[sku],
                window_days=7,
                requires_unused=True,
                required_facts=["quality_verdict"],
                quality_requirement="passed",
                administrator_verified=True,
                **{
                    k: refs[k]
                    for k in ("scope_clause", "window_clause", "condition_clause", "quality_clause")
                },
            ),
        )
        defect_policy = register_policy(
            session,
            admin,
            ReturnPolicyInput(
                version_id=version_id,
                skus=[sku],
                window_days=30,
                requires_unused=False,
                return_basis="quality_issue",
                required_facts=["quality_verdict"],
                quality_requirement="defect_confirmed",
                administrator_verified=True,
                scope_clause=refs["scope_clause"],
                window_clause=refs["quality_issue_window"],
                condition_clause=refs["quality_issue_condition"],
                quality_clause=refs["quality_issue_condition"],
            ),
        )
        data.update(
            policy_id=policy.id,
            quality_policy_id=quality_policy.id,
            defect_policy_id=defect_policy.id,
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
