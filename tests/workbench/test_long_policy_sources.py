import json
from pathlib import Path

from helpers import MemoryIndex
from test_knowledge import ready, upload


def test_long_policy_source_pages_cover_late_clauses_and_keep_scope(logged_in, workbench):
    client, _, headers = logged_in
    document = json.loads(
        Path("data/evaluation/rag_long_scale_v2/corpus.json").read_text(encoding="utf-8")
    )[0]
    uploaded = upload(client, headers("admin"), document["text"], policy_scope="returns")
    version = ready(workbench, uploaded, MemoryIndex())
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    first = client.get(f"/api/v1/returns/policy-sources/{version}", headers=headers("admin")).json()
    assert len(first["items"]) == 20 and first["has_more"]
    pages = list(first["items"])
    offset = 20
    more = True
    while more:
        response = client.get(
            f"/api/v1/returns/policy-sources/{version}?offset={offset}&limit=20",
            headers=headers("admin"),
        )
        assert response.status_code == 200, response.text
        result = response.json()
        pages.extend(result["items"])
        offset += len(result["items"])
        more = result["has_more"]
    assert len({row["id"] for row in pages}) == len(pages) > 20
    assert all(row["version_id"] == version for row in pages)
    assert any("排除人为损坏" in row["content"] for row in pages[20:])
    assert (
        client.get(f"/api/v1/returns/policy-sources/{version}", headers=headers()).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/returns/policy-sources/{version}", headers=headers("admin", "demo-b")
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/returns/policy-sources/{version}?limit=21", headers=headers("admin")
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/knowledge/documents/{uploaded['document_id']}/withdraw",
            headers=headers("admin"),
            json={},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/returns/policy-sources/{version}?offset=20", headers=headers("admin")
        ).status_code
        == 409
    )
