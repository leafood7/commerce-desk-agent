"""Parse loss -> immutable revision -> independent review -> publication -> FAQ refresh."""

import hashlib
import json
from dataclasses import replace

import pytest
from helpers import MemoryIndex

from workbench.ingestion import work_once
from workbench.knowledge_models import DocumentVersion
from workbench.knowledge_schemas import SearchInput
from workbench.retrieval import KnowledgeRetriever


@pytest.fixture
def revision_case(logged_in, workbench, monkeypatch):
    client, app, headers = logged_in
    settings = workbench[2]
    index = MemoryIndex()
    text = "# 退货规则\n\n普通商品签收七天内可申请退货。定制商品除外。"
    result = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={"title": "解析修订演示", "policy_scope": "returns", "chunk_strategy": "parent_child"},
        files={"file": ("policy.md", text.encode(), "text/markdown")},
    )
    assert result.status_code == 201, result.text
    info = result.json()
    version = info["version"]["id"]
    from workbench import knowledge_revisions

    original = knowledge_revisions.parse_document

    def lost(path, extension):
        blocks, meta = original(path, extension)
        return [replace(b, text=b.text.replace("定制商品除外。", "")) for b in blocks], meta

    with monkeypatch.context() as m:
        m.setattr(knowledge_revisions, "parse_document", lost)
        assert work_once(app.state.database, settings, index)
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    parsed = client.get(
        f"/api/v1/knowledge/versions/{version}/parse", headers=headers("admin")
    ).json()
    assert parsed["revision"] is None
    block = parsed["blocks"][0]
    assert (
        "定制商品除外" not in block["text"]
    )  # Stable parser snapshot, despite parser now restored.
    body = {
        "expected_active_version_id": version,
        "reason": "修复原文件中被解析遗漏的定制商品例外",
        "patches": [
            {
                "block_index": block["index"],
                "before_sha256": block["sha256"],
                "text": block["text"] + "定制商品除外。",
            }
        ],
    }
    return client, app, headers, settings, index, info, body, text


def propose(case):
    client, _, headers, _, _, info, body, _ = case
    r = client.post(
        f"/api/v1/knowledge/versions/{info['version']['id']}/revisions",
        headers=headers("admin"),
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()


def process(case, version):
    client, app, headers, settings, index, *_ = case
    assert work_once(app.state.database, settings, index)
    r = client.get(f"/api/v1/knowledge/versions/{version['id']}", headers=headers("admin"))
    assert r.json()["status"] == "ready", r.text
    return r.json()


def review(case, version, action="approve", role="approver"):
    return case[0].post(
        f"/api/v1/knowledge/versions/{version['id']}/review",
        headers=case[2](role),
        json={
            "action": action,
            "reason": "已逐段核对原文件与解析修订差异",
            "config_hash": version["config_hash"],
        },
    )


def publish(case, version):
    return case[0].post(
        f"/api/v1/knowledge/versions/{version['id']}/publish", headers=case[2]("admin"), json={}
    )


def faq(case, source, target=None):
    client, app, headers, settings, index, *_ = case
    url = "/api/v1/knowledge/documents" + (f"/{target}/versions" if target else "")
    r = client.post(
        url,
        headers=headers("admin"),
        data={
            "title": "退货FAQ",
            "knowledge_kind": "faq",
            "faq_sources": json.dumps([source]),
            "policy_scope": "returns",
        },
        files={
            "file": (
                "faq.md",
                "问：普通商品退货期限？答：签收七天内，定制商品除外。".encode(),
                "text/markdown",
            )
        },
    )
    assert r.status_code == 201, r.text
    assert work_once(app.state.database, settings, index)
    assert publish(case, r.json()["version"]).status_code == 200
    return r.json()


def test_revision_review_publish_reindexes_without_touching_original(revision_case):
    case = revision_case
    client, app, headers, settings, index, info, _, original = case
    old = info["version"]["id"]
    before = client.get(f"/api/v1/knowledge/versions/{old}/file", headers=headers()).content
    created = propose(case)
    assert (
        created["status"] == "queued"
        and created["parse_metadata"]["revision"]["review_status"] == "pending"
    )
    assert publish(case, created).status_code == 409
    version = process(case, created)
    assert publish(case, version).status_code == 409
    assert review(case, version, role="admin").status_code == 403
    assert review(case, version).status_code == 200
    assert not client.get(f"/api/v1/knowledge/versions/{version['id']}", headers=headers()).json()[
        "published"
    ]
    assert publish(case, version).status_code == 200
    after = client.get(
        f"/api/v1/knowledge/versions/{version['id']}/file", headers=headers()
    ).content
    assert before == after == original.encode()
    assert hashlib.sha256(after).hexdigest() == version["parse_metadata"]["source_sha256"]
    parsed = client.get(
        f"/api/v1/knowledge/versions/{version['id']}/parse", headers=headers("approver")
    ).json()
    assert parsed["text_origin"] == "human_corrected_parse"
    assert parsed["revision"]["changes"][0]["before"] != parsed["revision"]["changes"][0]["after"]
    org = client.get("/api/v1/auth/me", headers=headers()).json()["organization_id"]
    found = KnowledgeRetriever(app.state.database, settings, index).search(
        org, SearchInput(query="定制商品退货", strategy="hybrid", policy_scope="returns")
    )
    assert found["matches"] and all(r["version_id"] == version["id"] for r in found["matches"])
    assert any("定制商品除外" in r["content"] for r in found["matches"])
    reading = client.post(
        "/api/v1/knowledge/read",
        headers=headers(),
        json={"version_id": version["id"], "parent_id": "p0"},
    )
    assert reading.json()["source_kind"] == "human_corrected_parse"
    assert "定制商品除外" in reading.json()["content"]
    assert (
        client.post(
            "/api/v1/knowledge/read", headers=headers(), json={"version_id": old, "parent_id": "p0"}
        ).status_code
        == 409
    )


def test_faq_invalidated_until_new_reviewed_content_version_even_after_rollback(revision_case):
    case = revision_case
    client, _, headers, _, _, info, *_ = case
    old = info["version"]["id"]
    original_faq = faq(case, old)
    version = process(case, propose(case))
    assert review(case, version).status_code == 200
    assert publish(case, version).status_code == 200
    faq_id = original_faq["version"]["id"]
    assert client.get(f"/api/v1/knowledge/versions/{faq_id}", headers=headers()).json()[
        "source_review_required"
    ]
    refreshed = faq(case, version["id"], original_faq["document_id"])
    assert not client.get(
        f"/api/v1/knowledge/versions/{refreshed['version']['id']}", headers=headers()
    ).json()["source_review_required"]
    rollback = client.post(
        f"/api/v1/knowledge/documents/{info['document_id']}/rollback",
        headers=headers("admin"),
        json={
            "version_id": old,
            "expected_active_version_id": version["id"],
            "reason": "演示回退并保留FAQ复核要求",
        },
    )
    assert rollback.status_code == 200, rollback.text
    for identifier in (faq_id, refreshed["version"]["id"]):
        assert client.get(f"/api/v1/knowledge/versions/{identifier}", headers=headers()).json()[
            "source_review_required"
        ]
    assert publish(case, original_faq["version"]).status_code == 409
    reissued = faq(case, old, original_faq["document_id"])
    assert reissued["version"]["id"] != faq_id
    assert not client.get(
        f"/api/v1/knowledge/versions/{reissued['version']['id']}", headers=headers()
    ).json()["source_review_required"]


@pytest.mark.parametrize("change", ["wrong_hash", "duplicate_block", "blank", "wrong_base"])
def test_stale_or_invalid_patch_rejected(revision_case, change):
    client, _, headers, _, _, info, body, _ = revision_case
    body = json.loads(json.dumps(body))
    if change == "wrong_hash":
        body["patches"][0]["before_sha256"] = "0" * 64
    elif change == "duplicate_block":
        body["patches"] *= 2
    elif change == "blank":
        body["patches"][0]["text"] = "  "
    else:
        body["expected_active_version_id"] = "stale"
    r = client.post(
        f"/api/v1/knowledge/versions/{info['version']['id']}/revisions",
        headers=headers("admin"),
        json=body,
    )
    assert r.status_code in (409, 422), r.text


def test_duplicate_proposal_is_not_another_version_and_rejection_is_terminal(revision_case):
    case = revision_case
    first = propose(case)
    assert propose(case)["id"] == first["id"]
    version = process(case, first)
    assert review(case, version, "reject").status_code == 200
    assert review(case, version, "approve").status_code == 409
    assert publish(case, version).status_code == 409


def test_failed_index_cannot_publish_or_change_active_source(revision_case, monkeypatch):
    case = revision_case
    client, app, headers, settings, index, info, *_ = case
    version = propose(case)

    def broken(*args, **kwargs):
        raise RuntimeError("explicit index failure")

    monkeypatch.setattr(index, "upsert", broken)
    assert work_once(app.state.database, settings, index)
    assert publish(case, version).status_code == 409
    assert client.get(
        f"/api/v1/knowledge/versions/{info['version']['id']}", headers=headers()
    ).json()["published"]


def test_review_and_publication_reject_changed_source(revision_case):
    case = revision_case
    client, _, headers, _, _, info, *_ = case
    version = process(case, propose(case))
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{info['document_id']}/withdraw", headers=headers("admin")
        ).status_code
        == 200
    )
    assert review(case, version).status_code == 409
    assert publish(case, version).status_code == 409
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{info['document_id']}/rollback",
            headers=headers("admin"),
            json={
                "version_id": info["version"]["id"],
                "expected_active_version_id": version["id"],
                "reason": "不能通过回退恢复已撤回文档",
            },
        ).status_code
        == 409
    )


def test_support_and_other_org_cannot_edit_or_review(revision_case):
    case = revision_case
    client, _, headers, _, _, info, body, _ = case
    base = info["version"]["id"]
    assert (
        client.get(f"/api/v1/knowledge/versions/{base}/parse", headers=headers()).status_code == 403
    )
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{base}/revisions", headers=headers(), json=body
        ).status_code
        == 403
    )
    other_headers = headers("admin", "demo-b")
    assert (
        client.get(f"/api/v1/knowledge/versions/{base}/parse", headers=other_headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{base}/revisions", headers=other_headers, json=body
        ).status_code
        == 404
    )


def test_revision_can_be_revised_again_with_original_snapshot_history(revision_case):
    case = revision_case
    client, _, headers, _, _, _, _, _ = case
    first = process(case, propose(case))
    assert review(case, first).status_code == 200
    assert publish(case, first).status_code == 200
    parsed = client.get(
        f"/api/v1/knowledge/versions/{first['id']}/parse", headers=headers("admin")
    ).json()
    block = parsed["blocks"][0]
    r = client.post(
        f"/api/v1/knowledge/versions/{first['id']}/revisions",
        headers=headers("admin"),
        json={
            "expected_active_version_id": first["id"],
            "reason": "补充原文件中的人工核对提示用于演示",
            "patches": [
                {
                    "block_index": block["index"],
                    "before_sha256": block["sha256"],
                    "text": block["text"] + "请核对适用条件。",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    second = process(case, r.json())
    assert second["parse_metadata"]["revision"]["depth"] == 2
    assert review(case, second).status_code == 200
    assert publish(case, second).status_code == 200


def test_snapshot_corruption_blocks_revision_and_keeps_original(revision_case):
    case = revision_case
    client, app, headers, settings, _, info, body, original = case
    from workbench.knowledge_service import stored_file

    with app.state.database.sessions() as session:
        version = session.get(DocumentVersion, info["version"]["id"])
        stored_file(settings, version.parse_metadata["parse_snapshot"]["key"]).write_text(
            "tampered", encoding="utf-8"
        )
    base = info["version"]["id"]
    assert (
        client.get(f"/api/v1/knowledge/versions/{base}/parse", headers=headers("admin")).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{base}/revisions", headers=headers("admin"), json=body
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/knowledge/versions/{base}/file", headers=headers()).content
        == original.encode()
    )


def test_blank_reason_and_stale_review_hash_rejected(revision_case):
    case = revision_case
    client, _, headers, _, _, info, body, _ = case
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{info['version']['id']}/revisions",
            headers=headers("admin"),
            json={**body, "reason": "      "},
        ).status_code
        == 422
    )
    version = process(case, propose(case))
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version['id']}/review",
            headers=headers("approver"),
            json={
                "action": "approve",
                "reason": "核对差异但版本指纹不匹配",
                "config_hash": "0" * 64,
            },
        ).status_code
        == 409
    )
    assert publish(case, version).status_code == 409


def test_expired_version_cannot_be_restored_by_rollback(revision_case):
    from datetime import timedelta

    from workbench.models import now

    case = revision_case
    client, app, headers, _, _, info, *_ = case
    revision = process(case, propose(case))
    assert review(case, revision).status_code == 200
    assert publish(case, revision).status_code == 200
    with app.state.database.sessions() as session:
        session.get(DocumentVersion, info["version"]["id"]).effective_until = (
            now().date() - timedelta(days=1)
        )
        session.commit()
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{info['document_id']}/rollback",
            headers=headers("admin"),
            json={
                "version_id": info["version"]["id"],
                "expected_active_version_id": revision["id"],
                "reason": "回退不能重新启用已经过期的政策版本",
            },
        ).status_code
        == 409
    )


def test_faq_source_change_during_indexing_survives_worker_completion(revision_case, monkeypatch):
    case = revision_case
    client, app, headers, settings, index, info, *_ = case
    revised = process(case, propose(case))
    assert review(case, revised).status_code == 200
    pending = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={
            "title": "处理中的FAQ",
            "knowledge_kind": "faq",
            "faq_sources": json.dumps([info["version"]["id"]]),
        },
        files={"file": ("faq.md", "问：期限？答：七天，定制商品除外。".encode(), "text/markdown")},
    )
    assert pending.status_code == 201
    faq_id = pending.json()["version"]["id"]
    upsert = index.upsert

    def publish_while_indexing(*args, **kwargs):
        result = upsert(*args, **kwargs)
        assert publish(case, revised).status_code == 200
        return result

    monkeypatch.setattr(index, "upsert", publish_while_indexing)
    assert work_once(app.state.database, settings, index)
    assert client.get(f"/api/v1/knowledge/versions/{faq_id}", headers=headers()).json()[
        "source_review_required"
    ]
    rollback = client.post(
        f"/api/v1/knowledge/documents/{info['document_id']}/rollback",
        headers=headers("admin"),
        json={
            "version_id": info["version"]["id"],
            "expected_active_version_id": revised["id"],
            "reason": "核对处理中FAQ也必须保留失效记录",
        },
    )
    assert rollback.status_code == 200
    assert publish(case, {"id": faq_id}).status_code == 409
