"""Complete policy-maintenance lifecycle through API; no provider/model calls."""

import copy

import pytest
from helpers import MemoryIndex

from workbench.ingestion import work_once


@pytest.fixture
def candidate_case(logged_in, workbench):
    client, app, headers = logged_in
    response = client.post(
        "/api/v1/knowledge/documents",
        headers=headers("admin"),
        data={
            "title": "政策提取完整链路",
            "policy_scope": "returns",
            "chunk_strategy": "parent_child",
        },
        files={
            "file": (
                "policy.md",
                "# 范围\n仅适用于本店普通商品。\n\n# 时限\n签收7天内可申请退货，商品必须未使用。\n\n# 例外\n定制商品除外。运费由商家承担。".encode(),
                "text/markdown",
            )
        },
    )
    assert response.status_code == 201, response.text
    version = response.json()["version"]["id"]
    assert work_once(app.state.database, workbench[2], MemoryIndex())
    response = client.post(
        f"/api/v1/knowledge/versions/{version}/policy-extractions",
        headers=headers("admin"),
        json={},
    )
    assert response.status_code == 201, response.text
    return client, app, headers, version, response.json()


def request(case, row, operation, role="admin", **extra):
    client, _, headers, _, _ = case
    body = {"expected_revision": row["revision"], "note": "逐项核对原文范围条件与例外", **extra}
    if operation in {"save", "submit"}:
        body["cards"] = extra.get("cards", row["payload"]["cards"])
    return client.post(
        "/api/v1/knowledge/policy-extractions/" + row["id"] + "/" + operation,
        headers=headers(role),
        json=body,
    )


def submitted(case):
    r = request(case, case[-1], "submit")
    assert r.status_code == 200, r.text
    return r.json()


def approved(case):
    row = submitted(case)
    r = request(case, row, "review", role="approver", action="approve", acknowledge_warnings=True)
    assert r.status_code == 200, r.text
    return r.json()


def test_extract_edit_independent_review_publish_and_read(candidate_case):
    case = candidate_case
    client, app, headers, version, row = case
    assert {c["category"] for c in row["payload"]["cards"]} == {
        "scope",
        "window",
        "condition",
        "exception",
        "responsibility",
    }
    assert (
        client.get(
            f"/api/v1/knowledge/versions/{version}/policy-summary", headers=headers()
        ).status_code
        == 404
    )
    cards = copy.deepcopy(row["payload"]["cards"])
    cards[0]["statement"] = "仅限本店普通商品，其他商品需另行核对。"
    edited = request(case, row, "save", cards=cards)
    assert edited.status_code == 200, edited.text
    r = request(case, edited.json(), "submit")
    assert r.status_code == 200, r.text
    r = request(
        case, r.json(), "review", role="approver", action="approve", acknowledge_warnings=True
    )
    assert r.status_code == 200, r.text
    r = request(case, r.json(), "publish", acknowledge_warnings=True)
    assert r.status_code == 200, r.text
    assert r.json()["candidate"]["status"] == "published"
    assert r.json()["version"]["published"]
    live = client.get(f"/api/v1/knowledge/versions/{version}/policy-summary", headers=headers())
    assert live.status_code == 200, live.text
    assert live.json()["cards"][0]["statement"] == cards[0]["statement"]
    assert live.json()["business_rule_activation"] is False
    from sqlalchemy import func, select
    from workbench.return_models import ReturnPolicy

    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ReturnPolicy)) == 0


def test_pending_cannot_publish_through_original_endpoint(candidate_case):
    client, _, headers, version, row = candidate_case
    r = client.post(
        f"/api/v1/knowledge/versions/{version}/publish", headers=headers("admin"), json={}
    )
    assert r.status_code == 409, r.text
    assert request(candidate_case, row, "publish", acknowledge_warnings=True).status_code == 409


def test_fabricated_source_or_stale_revision_cannot_submit(candidate_case):
    row = candidate_case[-1]
    cards = copy.deepcopy(row["payload"]["cards"])
    cards[0]["sources"][0]["quote"] = "原文没有的无限期退货"
    assert request(candidate_case, row, "submit", cards=cards).status_code == 422
    assert request(candidate_case, row, "save").status_code == 200
    assert request(candidate_case, row, "submit").status_code == 409


def test_independent_roles_and_org_isolation(candidate_case):
    client, _, headers, version, row = candidate_case
    assert (
        client.post(
            f"/api/v1/knowledge/versions/{version}/policy-extractions", headers=headers(), json={}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/knowledge/policy-extractions/" + row["id"], headers=headers("admin", "demo-b")
        ).status_code
        == 404
    )
    pending = submitted(candidate_case)
    assert (
        request(
            candidate_case, pending, "review", action="approve", acknowledge_warnings=True
        ).status_code
        == 403
    )
    assert (
        request(candidate_case, pending, "review", role="approver", action="approve").status_code
        == 422
    )


def test_source_withdrawal_invalidates_approval(candidate_case):
    client, _, headers, version, _ = candidate_case
    # Bind candidate to an already-published source; external withdrawal then changes the binding.
    row = approved(candidate_case)
    assert request(candidate_case, row, "publish", acknowledge_warnings=True).status_code == 200
    row = client.post(
        f"/api/v1/knowledge/versions/{version}/policy-extractions",
        headers=headers("admin"),
        json={},
    ).json()
    row = request(candidate_case, row, "submit").json()
    row = request(
        candidate_case, row, "review", role="approver", action="approve", acknowledge_warnings=True
    ).json()
    doc = client.get(f"/api/v1/knowledge/versions/{version}", headers=headers("admin")).json()[
        "document_id"
    ]
    assert (
        client.post(
            "/api/v1/knowledge/documents/" + doc + "/withdraw", headers=headers("admin"), json={}
        ).status_code
        == 200
    )
    assert request(candidate_case, row, "publish", acknowledge_warnings=True).status_code == 409
    assert (
        client.get(
            f"/api/v1/knowledge/versions/{version}/policy-summary", headers=headers()
        ).status_code
        == 404
    )


def test_rejection_preserved_and_new_candidate_is_separate(candidate_case):
    client, _, headers, version, initial = candidate_case
    row = submitted(candidate_case)
    r = request(candidate_case, row, "review", role="approver", action="reject")
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/v1/knowledge/versions/{version}/policy-extractions",
        headers=headers("admin"),
        json={},
    )
    assert r.status_code == 201, r.text
    assert r.json()["id"] != initial["id"] and r.json()["number"] == 2
    assert request(candidate_case, initial, "save").status_code == 409
