# ruff: noqa: F811
import asyncio

import pytest
from test_returns import draft, return_case  # noqa: F401
from test_scoped_mcp import grant, scoped_mcp  # noqa: F401

from workbench.mcp_gateway import ToolFailure


def make_pdf(text):
    """Minimal single-page Helvetica PDF with a valid xref, readable by pypdf."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(content), content),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def test_upload_list_download_evidence(logged_in):
    client, app, headers = logged_in
    data = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("evidence.png", data, "image/png")},
        data={"kind": "product_photo", "note": "外包装照片"},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == "evidence.png"
    assert body["kind"] == "product_photo"
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(data)

    listing = client.get("/api/v1/after-sales/attachments", headers=headers("support", "demo-a"))
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [body["id"]]

    download = client.get(
        "/api/v1/after-sales/attachments/" + body["id"] + "/file",
        headers=headers("support", "demo-a"),
    )
    assert download.status_code == 200
    assert download.content == data
    assert download.headers["content-type"].startswith("image/png")


def test_upload_rejects_unsupported_extension(logged_in):
    client, app, headers = logged_in
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 422


def test_upload_rejects_empty_file(logged_in):
    client, app, headers = logged_in
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("empty.png", b"", "image/png")},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 413


def test_cross_organization_isolated(logged_in):
    client, app, headers = logged_in
    data = b"\x89PNG\r\n\x1a\n" + b"y" * 10
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("e.png", data, "image/png")},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201
    attachment_id = response.json()["id"]

    other = client.get("/api/v1/after-sales/attachments", headers=headers("support", "demo-b"))
    assert other.json()["items"] == []

    denied = client.get(
        "/api/v1/after-sales/attachments/" + attachment_id + "/file",
        headers=headers("support", "demo-b"),
    )
    assert denied.status_code == 404


def test_upload_requires_support_or_admin(logged_in):
    client, app, headers = logged_in
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("e.png", b"\x89PNG\r\n\x1a\n0000", "image/png")},
        headers=headers("approver", "demo-a"),
    )
    assert response.status_code == 403


def test_pdf_invoice_extraction_candidates(logged_in):
    client, app, headers = logged_in
    pdf = make_pdf("Invoice Order Number: A1001 Amount: 199.00 Date: 2026-10-01")
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("invoice.pdf", pdf, "application/pdf")},
        data={"kind": "invoice"},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING_REVIEW"
    assert body["extraction_method"] == "pdf_text_rules"
    fields = {item["field"]: item["value"] for item in body["extracted_data"]["fields"]}
    assert fields["order_number"] == "A1001"
    assert fields["amount"] == "199.00"
    assert fields["date"] == "2026-10-01"
    assert all(item["verification_status"] == "pending_review" for item in body["extracted_data"]["fields"])


def test_pdf_logistics_extraction_candidates(logged_in):
    client, app, headers = logged_in
    pdf = make_pdf("Tracking Number: SF1234567890 Carrier SF Express Date: 2026-10-02")
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("waybill.pdf", pdf, "application/pdf")},
        data={"kind": "express_waybill"},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    fields = {item["field"]: item["value"] for item in body["extracted_data"]["fields"]}
    assert fields["tracking_number"] == "SF1234567890"
    assert fields["carrier"] == "顺丰速运"
    assert "order_number" not in fields


def test_image_pending_manual_review_without_ocr(logged_in):
    client, app, headers = logged_in
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"z" * 12, "image/png")},
        data={"kind": "product_photo"},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING_REVIEW"
    assert body["extraction_method"] == "manual_review"
    assert body["extracted_data"]["fields"] == []
    assert body["extracted_data"]["notes"]


def test_corrupt_pdf_extraction_failed_file_kept(logged_in):
    client, app, headers = logged_in
    data = b"not a real pdf"
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("broken.pdf", data, "application/pdf")},
        data={"kind": "invoice"},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "EXTRACTION_FAILED"
    assert body["extraction_error"] == "pdf_parse_failed"

    download = client.get(
        "/api/v1/after-sales/attachments/" + body["id"] + "/file",
        headers=headers("support", "demo-a"),
    )
    assert download.status_code == 200
    assert download.content == data


def test_review_decisions_and_terminal_state(logged_in):
    client, app, headers = logged_in
    upload = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"a" * 8, "image/png")},
        data={"kind": "product_photo"},
        headers=headers("support", "demo-a"),
    ).json()

    missing_note = client.post(
        f"/api/v1/after-sales/attachments/{upload['id']}/review",
        json={"decision": "verified", "note": ""},
        headers=headers("support", "demo-a"),
    )
    assert missing_note.status_code == 422

    denied = client.post(
        f"/api/v1/after-sales/attachments/{upload['id']}/review",
        json={"decision": "verified", "note": "无权角色"},
        headers=headers("approver", "demo-a"),
    )
    assert denied.status_code == 403

    reviewed = client.post(
        f"/api/v1/after-sales/attachments/{upload['id']}/review",
        json={"decision": "verified", "note": "照片与订单商品一致"},
        headers=headers("support", "demo-a"),
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["status"] == "VERIFIED"
    assert body["review_note"] == "照片与订单商品一致"
    assert body["reviewed_by"] and body["reviewed_at"]

    again = client.post(
        f"/api/v1/after-sales/attachments/{upload['id']}/review",
        json={"decision": "rejected", "note": "重复核验"},
        headers=headers("admin", "demo-a"),
    )
    assert again.status_code == 409


def test_review_needs_supplement_and_failed_extraction_reviewable(logged_in):
    client, app, headers = logged_in
    supplement = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("b.png", b"\x89PNG\r\n\x1a\n" + b"b" * 8, "image/png")},
        data={"kind": "invoice"},
        headers=headers("support", "demo-a"),
    ).json()
    response = client.post(
        f"/api/v1/after-sales/attachments/{supplement['id']}/review",
        json={"decision": "needs_supplement", "note": "发票抬头缺失，请补充"},
        headers=headers("admin", "demo-a"),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "NEEDS_SUPPLEMENT"

    failed = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("broken.pdf", b"junk", "application/pdf")},
        data={"kind": "invoice"},
        headers=headers("support", "demo-a"),
    ).json()
    assert failed["status"] == "EXTRACTION_FAILED"
    rescued = client.post(
        f"/api/v1/after-sales/attachments/{failed['id']}/review",
        json={"decision": "verified", "note": "人工核对原件无误"},
        headers=headers("admin", "demo-a"),
    )
    assert rescued.status_code == 200
    assert rescued.json()["status"] == "VERIFIED"


def test_evidence_linked_to_return_request(return_case):
    client, app, headers, _, _ = return_case
    request = draft(return_case)
    response = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"c" * 8, "image/png")},
        data={"kind": "product_photo", "return_request_id": request["id"]},
        headers=headers("support", "demo-a"),
    )
    assert response.status_code == 201, response.text
    attachment_id = response.json()["id"]
    assert response.json()["return_request_id"] == request["id"]

    filtered = client.get(
        f"/api/v1/after-sales/attachments?return_request_id={request['id']}",
        headers=headers("support", "demo-a"),
    )
    assert [item["id"] for item in filtered.json()["items"]] == [attachment_id]

    bogus = client.post(
        "/api/v1/after-sales/attachments",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"d" * 8, "image/png")},
        data={"kind": "product_photo", "return_request_id": "0" * 36},
        headers=headers("support", "demo-a"),
    )
    assert bogus.status_code == 404


def test_mcp_lists_return_evidence_with_review_status(scoped_mcp, return_case):
    client, app, headers, gateway, _ = scoped_mcp
    request = draft(return_case)
    upload = client.post(
        "/api/v1/after-sales/attachments",
        files={
            "file": (
                "waybill.pdf",
                make_pdf("Tracking Number: SF1234567890 Carrier SF Express"),
                "application/pdf",
            )
        },
        data={"kind": "express_waybill", "return_request_id": request["id"]},
        headers=headers("support"),
    )
    assert upload.status_code == 201, upload.text

    token = grant(client, headers("support"))
    data = asyncio.run(gateway.call("list_return_evidence", {"request_id": request["id"]}, token))
    assert data["status"] == "found"
    evidence = data["evidence"]
    assert [item["id"] for item in evidence] == [upload.json()["id"]]
    assert evidence[0]["status"] == "PENDING_REVIEW"
    assert evidence[0]["extraction_method"] == "pdf_text_rules"
    assert "storage_key" not in evidence[0]

    with pytest.raises(ToolFailure, match="not_found"):
        asyncio.run(gateway.call("list_return_evidence", {"request_id": "0" * 36}, token))