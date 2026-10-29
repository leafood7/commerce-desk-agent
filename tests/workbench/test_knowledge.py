import zipfile
from datetime import timedelta

import pytest
from helpers import MemoryIndex

from workbench.document_parsing import IngestionError, chunks_from_blocks, parse_document
from workbench.ingestion import claim_job, process_job, work_once
from workbench.knowledge_models import DocumentVersion, IngestionJob
from workbench.knowledge_schemas import SearchInput
from workbench.models import User, now
from workbench.retrieval import KnowledgeRetriever


def upload(client, headers, text="运费券仅适用于指定地区的配送费用。", **options):
    reply = client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        data={"title": "配送政策", **options},
        files={"file": ("shipping.md", text.encode(), "text/markdown")},
    )
    assert reply.status_code == 201, reply.text
    return reply.json()


def ready(workbench, uploaded, index):
    _, app, settings = workbench
    assert work_once(app.state.database, settings, index)
    return uploaded["version"]["id"]


def test_upload_requires_admin_and_never_publishes_automatically(logged_in, workbench):
    client, app, headers = logged_in
    forbidden = client.post(
        "/api/v1/knowledge/documents",
        headers=headers(),
        files={"file": ("policy.txt", b"rules", "text/plain")},
    )
    assert forbidden.status_code == 403
    row = upload(client, headers("admin"))
    index = MemoryIndex()
    version = ready(workbench, row, index)
    detail = client.get("/api/v1/knowledge/versions/" + version, headers=headers()).json()
    assert detail["status"] == "ready" and detail["published"] is False
    retriever = KnowledgeRetriever(app.state.database, app.state.settings, index)
    actor = client.get("/api/v1/auth/me", headers=headers()).json()
    assert (
        retriever.search(actor["organization_id"], SearchInput(query="运费券", strategy="hybrid"))[
            "status"
        ]
        == "no_evidence"
    )


def test_publish_replace_withdraw_and_cached_snapshot_isolation(logged_in, workbench):
    client, app, headers = logged_in
    index = MemoryIndex()
    row = upload(client, headers("admin"), policy_scope="freight_coupon")
    version = ready(workbench, row, index)
    url = "/api/v1/knowledge/versions/" + version
    assert client.post(url + "/publish", headers=headers(), json={}).status_code == 403
    assert client.post(url + "/publish", headers=headers("admin"), json={}).status_code == 200
    organization = client.get("/api/v1/auth/me", headers=headers()).json()["organization_id"]
    retriever = KnowledgeRetriever(app.state.database, app.state.settings, index)
    query = SearchInput(query="运费券", strategy="hybrid", policy_scope="freight_coupon")
    first = retriever.search(organization, query)
    assert first["status"] == "found"
    assert retriever.search(organization, query)["matches"]
    assert retriever.snapshot_builds == 1
    changed = client.post(
        f"/api/v1/knowledge/documents/{row['document_id']}/versions",
        headers=headers("admin"),
        files={"file": ("v2.md", "运费券新增覆盖华南地区。".encode(), "text/markdown")},
    )
    assert changed.status_code == 201
    second_id = ready(workbench, changed.json(), index)
    client.post(
        f"/api/v1/knowledge/versions/{second_id}/publish", headers=headers("admin"), json={}
    ).raise_for_status()
    result = retriever.search(organization, query)
    assert result["matches"] and all(item["version_id"] == second_id for item in result["matches"])
    assert retriever.snapshot_builds == 2
    old_citation = client.get(
        "/api/v1/knowledge/citations/" + first["matches"][0]["id"], headers=headers()
    )
    assert old_citation.json()["currently_published"] is False
    client.post(
        f"/api/v1/knowledge/documents/{row['document_id']}/withdraw", headers=headers("admin")
    ).raise_for_status()
    assert retriever.search(organization, query)["status"] == "no_evidence"


def test_document_file_citations_and_versions_are_scoped(logged_in, workbench):
    client, _, headers = logged_in
    row = upload(client, headers("admin"))
    version = ready(workbench, row, MemoryIndex())
    foreign = headers("admin", "demo-b")
    for suffix in ["", "/file"]:
        assert (
            client.get(
                "/api/v1/knowledge/versions/" + version + suffix, headers=foreign
            ).status_code
            == 404
        )
    for suffix in ["/publish", "/retry"]:
        assert (
            client.post(
                "/api/v1/knowledge/versions/" + version + suffix, headers=foreign, json={}
            ).status_code
            == 404
        )
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{row['document_id']}/withdraw", headers=foreign
        ).status_code
        == 404
    )
    assert client.get("/api/v1/knowledge/documents", headers=foreign).json()["items"] == []


def test_duplicate_version_returns_same_job_and_no_extra_chunks(logged_in, workbench):
    client, app, headers = logged_in
    row = upload(client, headers("admin"))
    payload = {
        "file": ("shipping.md", "运费券仅适用于指定地区的配送费用。".encode(), "text/markdown")
    }
    duplicate = client.post(
        f"/api/v1/knowledge/documents/{row['document_id']}/versions",
        headers=headers("admin"),
        files=payload,
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["version"]["id"] == row["version"]["id"]
    index = MemoryIndex()
    ready(workbench, row, index)
    assert not work_once(app.state.database, app.state.settings, index)


def test_failed_ingestion_can_be_retried_without_partial_publication(logged_in, workbench):
    client, app, headers = logged_in
    row = upload(client, headers("admin"))

    class BrokenIndex(MemoryIndex):
        def upsert(self, *args):
            raise RuntimeError("fixture backend unavailable")

    version = ready(workbench, row, BrokenIndex())
    detail = client.get("/api/v1/knowledge/versions/" + version, headers=headers()).json()
    assert detail["status"] == "failed" and detail["error_code"] == "ingestion_failed"
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/retry", headers=headers("admin")
        ).status_code
        == 200
    )
    assert work_once(app.state.database, app.state.settings, MemoryIndex())
    assert (
        client.get("/api/v1/knowledge/versions/" + version, headers=headers()).json()["status"]
        == "ready"
    )


def test_expired_worker_lease_is_reclaimed_and_old_owner_cannot_commit(logged_in, workbench):
    client, app, headers = logged_in
    row = upload(client, headers("admin"))
    first = claim_job(app.state.database)
    with app.state.database.sessions() as session:
        job = session.get(IngestionJob, first[0])
        job.lease_until = now() - timedelta(seconds=1)
        session.commit()
    second = claim_job(app.state.database)
    assert first[0] == second[0] and first[1] != second[1]
    process_job(app.state.database, app.state.settings, *first, MemoryIndex())
    with app.state.database.sessions() as session:
        assert session.get(DocumentVersion, row["version"]["id"]).status == "processing"
    process_job(app.state.database, app.state.settings, *second, MemoryIndex())
    with app.state.database.sessions() as session:
        assert session.get(DocumentVersion, row["version"]["id"]).status == "ready"


def test_scope_filters_known_wrong_policy_but_does_not_invent_applicability(logged_in, workbench):
    client, app, headers = logged_in
    index = MemoryIndex()
    for scope, text in [
        ("goods_coupon", "商品满减券部分退货不承诺立即退整券。"),
        ("freight_coupon", "运费券不当然随商品退货返还。"),
    ]:
        row = upload(client, headers("admin"), text, policy_scope=scope)
        version = ready(workbench, row, index)
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).raise_for_status()
    org = client.get("/api/v1/auth/me", headers=headers()).json()["organization_id"]
    result = KnowledgeRetriever(app.state.database, app.state.settings, index).search(
        org,
        SearchInput(query="运费券部分退货返还", policy_scope="freight_coupon", strategy="hybrid"),
    )
    assert result["matches"] and all(
        row["policy_scope"] != "goods_coupon" for row in result["matches"]
    )


def test_parser_metadata_limits_and_citation_boundaries(tmp_path):
    path = tmp_path / "policy.md"
    path.write_text(
        "# 运费券\n\n只适用于配送费用。\n\n## 例外\n\n活动另有约定时以约定为准。", encoding="utf-8"
    )
    blocks, metadata = parse_document(path, ".md")
    chunks = chunks_from_blocks(blocks, "structural", 160, 20)
    assert any("例外" in row["section"] for row in chunks)
    assert all(len(row["content"]) <= 160 for row in chunks)
    assert metadata["warnings"] == []
    path.write_bytes(b"not a PDF")
    with pytest.raises(IngestionError, match="invalid_pdf"):
        parse_document(path, ".pdf")


def test_pdf_empty_and_docx_table_preservation(tmp_path):
    from pypdf import PdfWriter

    pdf = tmp_path / "empty.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as stream:
        writer.write(stream)
    with pytest.raises(IngestionError, match="no_extractable_text"):
        parse_document(pdf, ".pdf")
    docx = tmp_path / "policy.docx"
    xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>优惠规则</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>运费券</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>仅配送费用</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    blocks, _ = parse_document(docx, ".docx")
    assert any("运费券 | 仅配送费用" in row.text and row.section == "优惠规则" for row in blocks)


def test_forged_vector_ids_never_become_evidence(logged_in, workbench):
    client, app, headers = logged_in
    index = MemoryIndex()
    row = upload(client, headers("admin"))
    version = ready(workbench, row, index)
    client.post(
        f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
    ).raise_for_status()
    index.query = lambda *args: [{"id": "forged-foreign-id", "distance": 0.0}]
    org = client.get("/api/v1/auth/me", headers=headers()).json()["organization_id"]
    result = KnowledgeRetriever(app.state.database, app.state.settings, index).search(
        org, SearchInput(query="运费券", strategy="vector")
    )
    assert result["status"] == "no_evidence"


def test_repeated_new_upload_reuses_document_and_version(logged_in):
    client, _, headers = logged_in
    first = upload(client, headers("admin"))
    repeated = upload(client, headers("admin"))
    assert repeated["created"] is False
    assert repeated["document_id"] == first["document_id"]
    assert repeated["version"]["id"] == first["version"]["id"]


def test_publication_validator_rejects_forged_and_expired_sources(logged_in, workbench):
    from fastapi import HTTPException

    from workbench.knowledge_service import validated_evidence

    client, app, headers = logged_in
    index = MemoryIndex()
    row = upload(client, headers("admin"))
    version = ready(workbench, row, index)
    client.post(
        f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
    ).raise_for_status()
    identity = client.get("/api/v1/auth/me", headers=headers()).json()
    retriever = KnowledgeRetriever(app.state.database, app.state.settings, index)
    matches = retriever.search(
        identity["organization_id"], SearchInput(query="运费券", strategy="hybrid")
    )["matches"]
    with app.state.database.sessions() as session:
        actor = session.get(User, identity["id"])
        assert validated_evidence(session, actor, matches)
        with pytest.raises(HTTPException):
            validated_evidence(session, actor, [{**matches[0], "content": "虚构的条款"}])
    client.post(
        f"/api/v1/knowledge/documents/{row['document_id']}/withdraw", headers=headers("admin")
    ).raise_for_status()
    with app.state.database.sessions() as session:
        with pytest.raises(HTTPException):
            validated_evidence(session, session.get(User, identity["id"]), matches)


def test_exhausted_crashed_job_becomes_failed(logged_in, workbench):
    client, app, headers = logged_in
    row = upload(client, headers("admin"))
    claimed = claim_job(app.state.database)
    with app.state.database.sessions() as session:
        job = session.get(IngestionJob, claimed[0])
        job.attempts = 3
        job.lease_until = now() - timedelta(seconds=1)
        session.commit()
    assert claim_job(app.state.database) is None
    detail = client.get(
        "/api/v1/knowledge/versions/" + row["version"]["id"], headers=headers()
    ).json()
    assert detail["status"] == "failed" and detail["error_code"] == "retry_limit_reached"


def test_no_blank_lines_between_markdown_sections(tmp_path):
    path = tmp_path / "sections.md"
    path.write_text("# 第一章\n第一章内容\n## 条件\n第二段内容", encoding="utf-8")
    blocks, _ = parse_document(path, ".md")
    assert next(block for block in blocks if block.text == "第一章内容").section == "第一章"
    assert next(block for block in blocks if block.text == "第二段内容").section == "第一章 / 条件"
