import json

import pytest
from helpers import MemoryIndex

from workbench.document_parsing import text_blocks
from workbench.ingestion import work_once
from workbench.knowledge_schemas import SearchInput
from workbench.parent_context import expand_parents, parent_child_chunks
from workbench.policy_audience import PolicyAudience
from workbench.retrieval import KnowledgeRetriever


def test_parent_children_keep_whole_section_and_budget():
    blocks = text_blocks(
        "# 退货\n\n七天内可退。\n\n"
        + "流程说明。" * 50
        + "\n\n定制商品除外。\n\n# 运费\n\n用户承担。"
    )
    chunks, metadata = parent_child_chunks(blocks, 160, 20)
    assert len(metadata["parents"]) == 2
    assert "定制商品除外" in metadata["parents"][0]["text"]
    rows = [
        {
            **c,
            "id": str(c["position"]),
            "version_id": "v",
            "organization_id": "a",
            "parent_id": metadata["children"][str(c["position"])],
        }
        for c in chunks
    ]
    selected, trace = expand_parents([rows[0], rows[0]], rows, 4000)
    assert len({r["id"] for r in selected}) == len(selected)
    assert any("定制商品除外" in r["content"] for r in selected)
    assert all(r["parent_id"] == "p0" for r in selected)
    _, limited = expand_parents([rows[0]], rows, len(rows[0]["content"]))
    assert limited["limited_ids"] and not limited["parent_expansion_complete"]
    assert trace["policy_sufficiency"] == "not_established"


def prepare(client, app, headers, settings, index, channel="线上", target=None):
    content = (
        "# 退货政策\n\n普通商品签收后七天可申请。\n\n" + "办理说明。" * 40 + "\n\n定制商品除外。"
    )
    audience = {"merchant": "演示商家", "region": "大陆", "channel": channel}
    url = "/api/v1/knowledge/documents" + (f"/{target}/versions" if target else "")
    response = client.post(
        url,
        headers=headers("admin"),
        data={
            "title": "演示政策",
            "chunk_strategy": "parent_child",
            "chunk_chars": "160",
            "chunk_overlap": "20",
            "policy_scope": "returns",
            "audience": json.dumps(audience),
        },
        files={"file": ("policy.md", content.encode(), "text/markdown")},
    )
    assert response.status_code == 201, response.text
    value = response.json()
    assert work_once(app.state.database, settings, index)
    version = value["version"]["id"]
    result = client.post(
        f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
    )
    assert result.status_code == 200, result.text
    return value, audience


def test_scoped_search_read_correction_and_withdraw(logged_in, workbench):
    client, app, headers = logged_in
    settings = workbench[2]
    index = MemoryIndex()
    first, audience = prepare(client, app, headers, settings, index)
    second, _ = prepare(client, app, headers, settings, index, "门店")
    org = client.get("/api/v1/auth/me", headers=headers()).json()["organization_id"]
    settings.retrieval_context_mode = "parent_child"
    retriever = KnowledgeRetriever(app.state.database, settings, index)
    unknown = retriever.search(org, SearchInput(query="七天", strategy="hybrid"))
    assert unknown["coverage"] == "needs_scope" and not unknown["matches"]
    assert "channel" in unknown["clarification"]["missing"]
    result = retriever.search(
        org, SearchInput(query="七天", strategy="hybrid", audience=PolicyAudience(**audience))
    )
    assert result["matches"] and all(
        r["version_id"] == first["version"]["id"] for r in result["matches"]
    )
    assert any("定制商品除外" in r["content"] for r in result["matches"])
    body = {
        "version_id": first["version"]["id"],
        "parent_id": "p0",
        "max_chars": 100,
        "audience": audience,
    }
    reply = client.post("/api/v1/knowledge/read", headers=headers(), json=body)
    assert reply.status_code == 200, reply.text
    page = reply.json()
    assert not page["complete"] and page["next_offset"] == 100
    pieces = [page["content"]]
    while page["next_offset"] is not None:
        body["offset"] = page["next_offset"]
        page = client.post("/api/v1/knowledge/read", headers=headers(), json=body).json()
        pieces.append(page["content"])
    assert "定制商品除外" in "".join(pieces)
    assert (
        client.post(
            "/api/v1/knowledge/read", headers=headers(organization="demo-b"), json=body
        ).status_code
        == 404
    )
    corrected = {**audience, "channel": "门店"}
    assert (
        client.post(
            "/api/v1/knowledge/read", headers=headers(), json={**body, "audience": corrected}
        ).status_code
        == 409
    )
    changed = retriever.search(
        org, SearchInput(query="七天", strategy="hybrid", audience=PolicyAudience(**corrected))
    )
    assert all(r["version_id"] == second["version"]["id"] for r in changed["matches"])
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{first['document_id']}/withdraw",
            headers=headers("admin"),
            json={},
        ).status_code
        == 200
    )
    assert client.post("/api/v1/knowledge/read", headers=headers(), json=body).status_code == 409
    assert not retriever.search(
        org, SearchInput(query="七天", strategy="hybrid", audience=PolicyAudience(**audience))
    )["matches"]


def test_read_schema_and_parent_expansion_isolation():
    from pydantic import ValidationError

    from workbench.knowledge_schemas import ReadInput

    for values in [{"parent_id": "../../secret"}, {"offset": -1}, {"max_chars": 6001}]:
        with pytest.raises(ValidationError):
            ReadInput.model_validate({"version_id": "v", "parent_id": "p0", **values})
    rows = [
        {
            "id": "a",
            "version_id": "v",
            "organization_id": "a",
            "parent_id": "p0",
            "position": 0,
            "content": "anchor",
        },
        {
            "id": "b",
            "version_id": "v",
            "organization_id": "b",
            "parent_id": "p0",
            "position": 1,
            "content": "foreign",
        },
        {
            "id": "c",
            "version_id": "old",
            "organization_id": "a",
            "parent_id": "p0",
            "position": 2,
            "content": "old",
        },
    ]
    selected, _ = expand_parents([rows[0]], rows, 4000)
    assert [r["id"] for r in selected] == ["a"]


def test_real_official_source_parses_and_reads_in_isolated_database(logged_in, workbench):
    import hashlib
    from pathlib import Path

    client, app, headers = logged_in
    root = Path(__file__).resolve().parents[2] / "data/evaluation/rag_upgrade_sources_v1"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    content = (root / manifest["file"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == manifest["sha256"]
    reply = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={
            "title": "官方法规解析实验，不作商家资格映射",
            "chunk_strategy": "parent_child",
            "policy_scope": "returns",
        },
        files={"file": ("official.txt", content, "text/plain")},
    )
    assert reply.status_code == 201, reply.text
    version = reply.json()["version"]["id"]
    assert work_once(app.state.database, workbench[2], MemoryIndex())
    detail = client.get(f"/api/v1/knowledge/versions/{version}", headers=headers()).json()
    assert detail["parse_metadata"]["parent_child"]["parent_count"] == 38
    assert "parents" not in detail["parse_metadata"]["parent_child"]
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    source = client.post(
        "/api/v1/knowledge/read",
        headers=headers(),
        json={"version_id": version, "parent_id": "p5", "max_chars": 6000},
    )
    assert source.status_code == 200, source.text
    assert "第六条" in source.json()["content"] and "定作" in source.json()["content"]
    assert source.json()["complete"]


def test_explicit_cross_article_and_table_headers_are_preserved():
    text = "# 第1条 退货\n\n七天内可退，例外参见第3条。\n\n# 第2条 流程\n\n联系商家。\n\n# 第3条 例外\n\n定制商品除外。"
    chunks, meta = parent_child_chunks(text_blocks(text), 160, 20)
    assert meta["parents"][0]["links"] == ["p2"]
    rows = [
        {
            **c,
            "id": str(c["position"]),
            "version_id": "v",
            "organization_id": "a",
            "parent_id": meta["children"][str(c["position"])],
            "parent_links": next(
                p["links"]
                for p in meta["parents"]
                if p["id"] == meta["children"][str(c["position"])]
            ),
        }
        for c in chunks
    ]
    selected, _ = expand_parents([rows[0]], rows, 2000)
    assert any("定制商品除外" in row["content"] for row in selected)
    table = "# 期限\n\n| 商品 | 期限 |\n|---|---|\n" + "\n".join(
        f"| 商品{i} | {i}天 |" for i in range(30)
    )
    children, parents = parent_child_chunks(text_blocks(table), 160, 20)
    assert all(
        "| 商品 | 期限 |" in row["content"] for row in children if "商品29" in row["content"]
    )
    assert parents["parents"][0]["text"].count("| 商品 | 期限 |") == 1


def test_docx_table_headers_repeated_only_in_retrieval_children(tmp_path):
    import zipfile

    from workbench.document_parsing import parse_document

    path = tmp_path / "table.docx"
    rows = [
        "<w:tr><w:tc><w:p><w:r><w:t>商品</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>期限</w:t></w:r></w:p></w:tc></w:tr>"
    ]
    rows += [
        f"<w:tr><w:tc><w:p><w:r><w:t>型号{i}</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>{i}天</w:t></w:r></w:p></w:tc></w:tr>"
        for i in range(30)
    ]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'
            + "".join(rows)
            + "</w:tbl></w:body></w:document>",
        )
    blocks, _ = parse_document(path, ".docx")
    children, meta = parent_child_chunks(blocks, 160, 20)
    assert all("商品 | 期限" in c["content"] for c in children if "型号29" in c["content"])
    assert meta["parents"][0]["text"].count("商品 | 期限") == 1


def test_faq_source_change_blocks_read_search_and_republish(logged_in, workbench):
    from workbench.knowledge_schemas import SearchInput

    client, app, headers = logged_in
    index = MemoryIndex()
    source, audience = prepare(client, app, headers, workbench[2], index)
    source_id = source["version"]["id"]
    reply = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={
            "title": "FAQ-退货期限",
            "chunk_strategy": "parent_child",
            "knowledge_kind": "faq",
            "audience": json.dumps(audience),
            "faq_sources": json.dumps([source_id]),
        },
        files={
            "file": (
                "faq.md",
                "# 退货期限\n\n普通商品七天内可申请，定制品除外。".encode(),
                "text/markdown",
            )
        },
    )
    assert reply.status_code == 201, reply.text
    faq = reply.json()
    version = faq["version"]["id"]
    assert work_once(app.state.database, workbench[2], index)
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    org = client.get("/api/v1/auth/me", headers=headers()).json()["organization_id"]
    retriever = KnowledgeRetriever(app.state.database, workbench[2], index)
    query = SearchInput(
        query="退货期限", strategy="hybrid", version_id=version, audience=PolicyAudience(**audience)
    )
    assert retriever.search(org, query)["matches"]
    # Publish a genuinely changed source version.
    changed = client.post(
        f"/api/v1/knowledge/documents/{source['document_id']}/versions",
        headers=headers("admin"),
        data={"chunk_strategy": "parent_child"},
        files={"file": ("v2.md", "# 退货\n\n更新后的退货条款。".encode(), "text/markdown")},
    ).json()
    assert work_once(app.state.database, workbench[2], index)
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{changed['version']['id']}/publish",
            headers=headers("admin"),
            json={},
        ).status_code
        == 200
    )
    assert not retriever.search(org, query)["matches"]
    assert client.get(f"/api/v1/knowledge/versions/{version}", headers=headers()).json()[
        "source_review_required"
    ]
    assert (
        client.post(
            "/api/v1/knowledge/read",
            headers=headers(),
            json={"version_id": version, "parent_id": "p0"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 409
    )
