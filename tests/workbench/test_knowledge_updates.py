"""Policy change -> maintenance -> independent FAQ review -> publication, no model calls."""

# ruff: noqa: F401,F811
import pytest
from test_knowledge_revisions import revision_case, faq, process, propose, publish, review
from workbench.ingestion import work_once


def pending(case):
    r = case[0].get("/api/v1/knowledge/updates?pending_only=true", headers=case[2]("admin"))
    assert r.status_code == 200, r.text
    return [
        t
        for row in r.json()["items"]
        for t in row["tasks"]
        if t["status"] in {"pending", "prepared"}
    ]


def change(case):
    version = process(case, propose(case))
    assert review(case, version).status_code == 200
    assert publish(case, version).status_code == 200
    return version


def prepare(case, task):
    client, _, headers, *_ = case
    r = client.get(
        "/api/v1/knowledge/update-tasks/" + task["id"] + "/faq-editor", headers=headers("admin")
    )
    assert r.status_code == 200, r.text
    edit = r.json()
    r = client.post(
        "/api/v1/knowledge/update-tasks/" + task["id"] + "/prepare-faq",
        headers=headers("admin"),
        json={
            "expected_revision": edit["task"]["revision"],
            "binding_hash": edit["binding_hash"],
            "content": "问：普通商品退货期限？答：签收七天内，定制商品除外。",
            "reason": "核对新政策并重新补全定制商品例外",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_complete_source_update_and_faq_maintenance(revision_case):
    case = revision_case
    client, app, headers, settings, index, info, *_ = case
    old_faq = faq(case, info["version"]["id"])
    target = process(case, propose(case))
    preview = client.get(
        "/api/v1/knowledge/versions/" + target["id"] + "/update-impact", headers=headers("admin")
    ).json()
    assert preview["faqs"][0]["id"] == old_faq["version"]["id"] and preview["changes"]
    assert review(case, target).status_code == 200
    assert publish(case, target).status_code == 200
    task = next(t for t in pending(case) if t["resource_kind"] == "faq")
    result = prepare(case, task)
    assert work_once(app.state.database, settings, index)
    version = client.get(
        "/api/v1/knowledge/versions/" + result["version"]["id"], headers=headers("admin")
    ).json()
    assert version["parse_metadata"]["faq_update_review"]["status"] == "pending"
    assert publish(case, version).status_code == 409
    body = {
        "action": "approve",
        "reason": "独立核对新政策与FAQ答案一致",
        "config_hash": version["config_hash"],
    }
    assert (
        client.post(
            "/api/v1/knowledge/versions/" + version["id"] + "/faq-review",
            headers=headers("admin"),
            json=body,
        ).status_code
        == 403
    )
    response = client.post(
        "/api/v1/knowledge/versions/" + version["id"] + "/faq-review",
        headers=headers("approver"),
        json=body,
    )
    assert response.status_code == 200, response.text
    assert publish(case, version).status_code == 200
    assert not [t for t in pending(case) if t["resource_id"] == old_faq["version"]["id"]]
    old = client.get(
        "/api/v1/knowledge/versions/" + old_faq["version"]["id"], headers=headers("admin")
    ).json()
    current = client.get(
        "/api/v1/knowledge/versions/" + version["id"], headers=headers("admin")
    ).json()
    assert old["source_review_required"] and not current["source_review_required"]
    assert current["published"] and current["faq_sources"] == [target["id"]]


def test_rollback_does_not_revive_old_faq_or_obsolete_draft(revision_case):
    case = revision_case
    client, app, headers, settings, index, info, *_ = case
    old_faq = faq(case, info["version"]["id"])
    target = change(case)
    task = next(t for t in pending(case) if t["resource_kind"] == "faq")
    draft = prepare(case, task)
    assert work_once(app.state.database, settings, index)
    response = client.post(
        "/api/v1/knowledge/documents/" + info["version"]["document_id"] + "/rollback",
        headers=headers("admin"),
        json={
            "version_id": info["version"]["id"],
            "expected_active_version_id": target["id"],
            "reason": "明确回退到先前已发布政策版本",
        },
    )
    assert response.status_code == 200, response.text
    assert publish(case, draft["version"]).status_code == 409
    old = client.get(
        "/api/v1/knowledge/versions/" + old_faq["version"]["id"], headers=headers("admin")
    ).json()
    assert old["source_review_required"]
    assert next(t for t in pending(case) if t["resource_kind"] == "faq")["id"] != task["id"]


def test_policy_annotation_preparation_closes_on_reviewed_publication(revision_case):
    case = revision_case
    client, _, headers, _, _, info, *_ = case
    source = info["version"]["id"]
    r = client.post(
        "/api/v1/knowledge/versions/" + source + "/policy-extractions",
        headers=headers("admin"),
        json={},
    )
    assert r.status_code == 201, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/submit",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "cards": row["payload"]["cards"],
            "note": "核对范围期限与原始政策来源",
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/review",
        headers=headers("approver"),
        json={
            "expected_revision": row["revision"],
            "action": "approve",
            "note": "独立核对原文与候选内容",
            "acknowledge_warnings": True,
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert (
        client.post(
            "/api/v1/knowledge/policy-extractions/" + row["id"] + "/publish",
            headers=headers("admin"),
            json={
                "expected_revision": row["revision"],
                "note": "按独立审核发布政策整理",
                "acknowledge_warnings": True,
            },
        ).status_code
        == 200
    )
    target = change(case)
    task = next(t for t in pending(case) if t["resource_kind"] == "extraction")
    r = client.post(
        "/api/v1/knowledge/update-tasks/" + task["id"] + "/prepare-extraction",
        headers=headers("admin"),
        json={"expected_revision": task["revision"], "reason": "根据新版本重新准备政策整理"},
    )
    assert r.status_code == 200, r.text
    row = r.json()["candidate"]
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/submit",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "cards": row["payload"]["cards"],
            "note": "已按新版本补足相应政策例外",
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/review",
        headers=headers("approver"),
        json={
            "expected_revision": row["revision"],
            "action": "approve",
            "note": "独立核对新版本的范围与例外",
            "acknowledge_warnings": True,
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/publish",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "note": "发布新版本的审核整理",
            "acknowledge_warnings": True,
        },
    )
    assert r.status_code == 200, r.text
    assert not [t for t in pending(case) if t["id"] == task["id"]]


def test_worklist_permissions_stale_edits_and_dismissal(revision_case):
    case = revision_case
    client, _, headers, _, _, info, *_ = case
    faq(case, info["version"]["id"])
    change(case)
    task = next(t for t in pending(case) if t["resource_kind"] == "faq")
    assert client.get("/api/v1/knowledge/updates", headers=headers()).status_code == 403
    assert (
        client.get(
            "/api/v1/knowledge/update-tasks/" + task["id"] + "/faq-editor",
            headers=headers("admin", "demo-b"),
        ).status_code
        == 404
    )
    r = client.post(
        "/api/v1/knowledge/update-tasks/" + task["id"] + "/dismiss",
        headers=headers("admin"),
        json={"expected_revision": task["revision"], "reason": "此旧FAQ不再使用，保留失效状态"},
    )
    assert r.status_code == 200, r.text
    assert not [t for t in pending(case) if t["id"] == task["id"]]
    assert (
        client.post(
            "/api/v1/knowledge/update-tasks/" + task["id"] + "/dismiss",
            headers=headers("admin"),
            json={"expected_revision": task["revision"], "reason": "旧页面不能覆盖已处理的待办"},
        ).status_code
        == 409
    )


def test_annotation_change_supersedes_faq_draft_and_survives_indexing(revision_case):
    case = revision_case
    client, app, headers, settings, index, info, *_ = case
    faq(case, info["version"]["id"])
    target = change(case)
    task = next(t for t in pending(case) if t["resource_kind"] == "faq")
    draft = prepare(case, task)
    row = client.post(
        "/api/v1/knowledge/versions/" + target["id"] + "/policy-extractions",
        headers=headers("admin"),
        json={},
    ).json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/submit",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "cards": row["payload"]["cards"],
            "note": "核对新版本政策范围条件与例外",
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/review",
        headers=headers("approver"),
        json={
            "expected_revision": row["revision"],
            "action": "approve",
            "note": "独立核对本版整理的原文依据",
            "acknowledge_warnings": True,
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    r = client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/publish",
        headers=headers("admin"),
        json={
            "expected_revision": row["revision"],
            "note": "发布同版本的新审核整理",
            "acknowledge_warnings": True,
        },
    )
    assert r.status_code == 200, r.text
    assert work_once(app.state.database, settings, index)
    version = client.get(
        "/api/v1/knowledge/versions/" + draft["version"]["id"], headers=headers("admin")
    ).json()
    assert version["source_review_required"]
    assert publish(case, version).status_code == 409
    assert next(t for t in pending(case) if t["resource_kind"] == "faq")["id"] != task["id"]


def test_replacement_rule_requires_current_policy_same_products_and_independent_confirmation(
    revision_case,
):
    from sqlalchemy import select
    from workbench.knowledge_models import KnowledgeChunk

    case = revision_case
    client, app, headers, settings, index, _, *_ = case

    def upload(text, doc=None):
        r = client.post(
            "/api/v1/knowledge/documents" + ("/" + doc + "/versions" if doc else ""),
            headers=headers("admin"),
            data={"title": "规则更新联动演示", "policy_scope": "returns"},
            files={"file": ("rule.md", text.encode(), "text/markdown")},
        )
        assert r.status_code == 201, r.text
        assert work_once(app.state.database, settings, index)
        version = r.json()["version"]
        assert publish(case, version).status_code == 200
        return version

    def register(version, sku, days):
        with app.state.database.sessions() as session:
            chunk = session.scalar(
                select(KnowledgeChunk).where(KnowledgeChunk.version_id == version["id"])
            )
            ref = {"chunk_id": chunk.id, "quote": chunk.content}
        r = client.post(
            "/api/v1/returns/policies",
            headers=headers("admin"),
            json={
                "version_id": version["id"],
                "skus": [sku],
                "window_days": days,
                "requires_unused": True,
                "administrator_verified": True,
                "scope_clause": ref,
                "window_clause": ref,
                "condition_clause": ref,
            },
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    first = upload("本规则仅适用于DEMO-LINK商品。实际签收7天内可申请，商品须未使用。")
    old = register(first, "DEMO-LINK", 7)
    second = upload(
        "本规则仅适用于DEMO-LINK商品。实际签收9天内可申请，商品须未使用。", first["document_id"]
    )
    task = next(
        t for t in pending(case) if t["resource_kind"] == "return_rule" and t["resource_id"] == old
    )
    wrong = register(second, "OTHER-LINK", 9)
    body = {
        "expected_revision": task["revision"],
        "replacement_id": wrong,
        "reason": "核对当前来源与原商品对应关系",
    }
    assert (
        client.post(
            "/api/v1/knowledge/update-tasks/" + task["id"] + "/resolve-rule",
            headers=headers("approver"),
            json=body,
        ).status_code
        == 409
    )
    valid = register(second, "DEMO-LINK", 9)
    body["replacement_id"] = valid
    assert (
        client.post(
            "/api/v1/knowledge/update-tasks/" + task["id"] + "/resolve-rule",
            headers=headers("admin"),
            json=body,
        ).status_code
        == 403
    )
    r = client.post(
        "/api/v1/knowledge/update-tasks/" + task["id"] + "/resolve-rule",
        headers=headers("approver"),
        json=body,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
