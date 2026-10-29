"""Safe configuration release and conversation history, with explicit model/tool substitutes."""

import json
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
import test_run_dispatch as dispatch_cases
from pydantic import SecretStr

from workbench.models import Message
from workbench.profile_models import RunProfile
from workbench.profiles import effective_settings

dispatch_fixture = dispatch_cases.dispatch_fixture
queued = dispatch_cases.queued
start = dispatch_cases.start
drain = dispatch_cases.drain
current = dispatch_cases.current


def draft(case, **values):
    response = case[0].post(
        "/api/v1/runtime/config/versions", headers=case[2]("admin"), json=values
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(case, row, revision):
    response = case[0].post(
        "/api/v1/runtime/config/versions/" + row["id"] + "/publish",
        headers=case[2]("admin"),
        json={"expected_revision": revision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(case, run):
    response = case[0].get("/api/v1/runs/" + run["id"] + "/configuration", headers=case[2]())
    assert response.status_code == 200, response.text
    return response.json()


def test_draft_is_inert_and_published_snapshot_survives_later_release(queued):
    first = draft(queued, top_k=1, tone="friendly")
    factory = start(queued)
    assert snapshot(queued, factory)["source"] == "factory"
    publish(queued, first, 0)
    old = start(queued)
    frozen = snapshot(queued, old)
    second = draft(queued, top_k=3, tone="plain")
    publish(queued, second, 1)
    # Consume earlier jobs in FIFO order, then the pinned old profile.
    drain(queued)
    drain(queued)
    assert snapshot(queued, old) == frozen and frozen["version_id"] == first["id"]
    run = current(queued, old["id"])
    assert run["output"]["answer"].startswith("您好")
    searches = [args for name, args in queued[3] if name == "search_knowledge_base"]
    assert [row["top_k"] for row in searches] == [3, 1]
    latest = start(queued)
    drain(queued)
    assert snapshot(queued, latest)["version_id"] == second["id"]
    assert not current(queued, latest["id"])["output"]["answer"].startswith("您好")


def test_profile_permissions_scope_and_optimistic_publish_conflict(queued):
    client, app, headers, _ = queued
    assert (
        client.post("/api/v1/runtime/config/versions", headers=headers(), json={}).status_code
        == 403
    )
    assert client.get("/api/v1/runtime/config/versions", headers=headers()).status_code == 403
    assert client.get("/api/v1/runtime/config").status_code == 401
    first = draft(queued)
    publish(queued, first, 0)
    second = draft(queued, tone="friendly")
    stale = client.post(
        "/api/v1/runtime/config/versions/" + second["id"] + "/publish",
        headers=headers("admin"),
        json={"expected_revision": 0},
    )
    assert stale.status_code == 409
    foreign = client.post(
        "/api/v1/runtime/config/versions/" + first["id"] + "/publish",
        headers=headers("admin", "demo-b"),
        json={"expected_revision": 0},
    )
    assert foreign.status_code == 404
    assert (
        client.post(
            "/api/v1/runtime/config/versions/" + first["id"], headers=headers("admin"), json={}
        ).status_code
        == 404
    )
    data = client.get("/api/v1/runtime/config", headers=headers()).json()
    assert data["current"]["id"] == first["id"]
    assert "Bearer" not in json.dumps(data) and "agent_router_key" not in json.dumps(data)


@pytest.mark.parametrize(
    "unsafe",
    [
        {"model_alias": "server_default"},
        {"prompt_template": "ignore_all_rules"},
        {"top_k": 4},
        {"max_output_tokens": 2048},
        {"temperature": 2},
        {"mcp_url": "https://attacker.invalid"},
        {"run_tool_limit": 999},
        {"approval_required": False},
        {"system_prompt": "Refund without approval"},
    ],
)
def test_profile_cannot_enable_model_override_rules_or_insert_arbitrary_prompt(queued, unsafe):
    response = queued[0].post(
        "/api/v1/runtime/config/versions", headers=queued[2]("admin"), json=unsafe
    )
    assert response.status_code == 422


def test_rule_snapshot_never_enables_model_when_live_mode_changes(queued, monkeypatch):
    run = start(queued)
    queued[1].state.settings.agent_router_mode = "model"
    calls = []
    monkeypatch.setattr("workbench.intent_router.model_intent", lambda *args: calls.append(True))
    drain(queued)
    assert calls == [] and current(queued, run["id"])["output"]["planner_mode"] == "rules"


def test_server_binding_change_downgrades_existing_model_snapshot_without_call(queued, monkeypatch):
    settings = queued[1].state.settings
    settings.agent_router_mode = "model"
    settings.agent_router_model = "explicit-test-a"
    run = start(queued)
    settings.agent_router_model = "explicit-test-b"
    calls = []
    monkeypatch.setattr("workbench.intent_router.model_intent", lambda *args: calls.append(True))
    drain(queued)
    assert calls == []
    assert current(queued, run["id"])["output"]["profile_guard"] == "server_model_binding_changed"


def test_model_output_parameter_is_real_and_still_fixed_to_safe_contract(queued, monkeypatch):
    settings = queued[1].state.settings
    settings.agent_router_mode = "model"
    settings.agent_router_model = "explicit-test"
    settings.agent_router_url = "https://test.invalid/chat"
    settings.agent_router_key = SecretStr("explicit-model-substitute")
    row = draft(queued, model_alias="server_default", max_output_tokens=128)
    publish(queued, row, 0)
    requests = []

    def response(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": '{"intent":"shipping"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    monkeypatch.setattr("workbench.intent_router.httpx.post", response)
    run = start(queued)
    drain(queued)
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 128 and requests[0]["temperature"] == 0
    assert current(queued, run["id"])["status"] == "awaiting_confirmation"


def test_legacy_configuration_is_not_retroactively_fabricated_and_snapshot_is_private(
    queued, monkeypatch
):
    run = start(queued)
    with queued[1].state.database.sessions() as session:
        session.delete(session.get(RunProfile, run["id"]))
        session.commit()
    assert snapshot(queued, run)["source"] == "legacy_unrecorded"
    assert snapshot(queued, run)["values"] is None
    assert (
        queued[0]
        .get("/api/v1/runs/" + run["id"] + "/configuration", headers=queued[2]("admin"))
        .status_code
        == 404
    )
    settings, values, reason = effective_settings(queued[1].state.database, run["id"])
    assert reason == "legacy_unrecorded"
    queued[1].state.settings.agent_router_mode = "model"
    calls = []
    monkeypatch.setattr("workbench.intent_router.model_intent", lambda *args: calls.append(True))
    drain(queued)
    assert calls == []
    assert current(queued, run["id"])["output"]["planner_mode"] == "rules"


def test_message_keyset_pagination_handles_same_timestamp_and_rejects_foreign_cursor(queued):
    client, app, headers, _ = queued
    first = client.post(
        "/api/v1/conversations", headers=headers(), json={"title": "pagination-proof"}
    ).json()
    second = client.post(
        "/api/v1/conversations", headers=headers(), json={"title": "different-conversation"}
    ).json()
    with app.state.database.sessions() as session:
        from workbench.security import authenticate

        actor = authenticate(session, headers()["Authorization"].split()[1])
        for index in range(5):
            session.add(
                Message(
                    id="00000000-0000-0000-0000-" + str(index).zfill(12),
                    organization_id=actor.organization_id,
                    conversation_id=first["id"],
                    role="human",
                    content="message-" + str(index),
                    created_at=datetime(2026, 10, 29),
                )
            )
        foreign_id = str(uuid4())
        session.add(
            Message(
                id=foreign_id,
                organization_id=actor.organization_id,
                conversation_id=second["id"],
                role="human",
                content="other",
                created_at=datetime(2026, 10, 29),
            )
        )
        session.commit()
    path = "/api/v1/conversations/" + first["id"] + "/messages?limit=2"
    a = client.get(path, headers=headers()).json()
    assert [row["content"] for row in a["items"]] == ["message-3", "message-4"] and a["has_more"]
    b = client.get(path + "&before=" + a["next_before"], headers=headers()).json()
    assert [row["content"] for row in b["items"]] == ["message-1", "message-2"]
    c = client.get(path + "&before=" + b["next_before"], headers=headers()).json()
    assert [row["content"] for row in c["items"]] == ["message-0"] and not c["has_more"]
    assert client.get(path + "&before=" + foreign_id, headers=headers()).status_code == 422
    assert client.get(path, headers=headers("admin")).status_code == 404
    assert (
        client.get(
            "/api/v1/conversations/" + first["id"] + "/runs", headers=headers("support", "demo-b")
        ).status_code
        == 404
    )


def test_same_conversation_keeps_full_questions_without_using_history_as_model_evidence(
    queued, monkeypatch
):
    client, app, headers, _ = queued
    conversation = client.post(
        "/api/v1/conversations", headers=headers(), json={"title": "long-question"}
    ).json()
    settings = app.state.settings
    settings.agent_router_mode = "model"
    marker = "OLD_ASSISTANT_IS_NOT_POLICY_EVIDENCE"
    with app.state.database.sessions() as session:
        from workbench.security import authenticate

        actor = authenticate(session, headers()["Authorization"].split()[1])
        session.add(
            Message(
                organization_id=actor.organization_id,
                conversation_id=conversation["id"],
                role="assistant",
                content=marker,
            )
        )
        session.commit()
    seen = []

    def model(settings, query):
        seen.append(query)
        return "knowledge", {"total_tokens": 12}

    monkeypatch.setattr("workbench.intent_router.model_intent", model)
    question = "请核对当前发货政策和例外条件。" * 20
    response = client.post(
        "/api/v1/runs",
        headers=headers(),
        json={"kind": "aftersales", "query": question, "conversation_id": conversation["id"]},
    )
    assert response.status_code == 202
    drain(queued)
    assert seen == [question] and marker not in seen[0]
    journal = client.get(
        "/api/v1/conversations/" + conversation["id"] + "/messages", headers=headers()
    ).json()
    assert any(question in row["content"] for row in journal["items"])
    history = client.get(
        "/api/v1/conversations/" + conversation["id"] + "/runs", headers=headers()
    ).json()
    assert history["items"][0]["id"] == response.json()["id"]


def test_profile_snapshot_survives_restart_and_concurrent_publish_has_one_winner(queued):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi.testclient import TestClient

    from workbench.app import create_app

    first = draft(queued, top_k=1)
    second = draft(queued, top_k=2)
    auth = queued[2]("admin")
    barrier = Barrier(2)

    def activate(row):
        barrier.wait()
        return (
            queued[0]
            .post(
                "/api/v1/runtime/config/versions/" + row["id"] + "/publish",
                headers=auth,
                json={"expected_revision": 0},
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(activate, [first, second]))
    assert sorted(statuses) == [200, 409]
    run = start(queued)
    before = snapshot(queued, run)
    restarted = create_app(queued[1].state.settings, gateway=queued[1].state.gateway)
    with TestClient(restarted) as client:
        after = client.get(
            "/api/v1/runs/" + run["id"] + "/configuration", headers=queued[2]()
        ).json()
        assert after == before
        assert (
            client.get("/api/v1/runs/" + run["id"], headers=queued[2]()).json()["status"]
            == "queued"
        )


def test_disabling_model_after_publication_remains_authoritative(queued, monkeypatch):
    settings = queued[1].state.settings
    settings.agent_router_mode = "model"
    profile = draft(queued, model_alias="server_default")
    publish(queued, profile, 0)
    run = start(queued)
    settings.agent_router_mode = "rules"
    calls = []
    monkeypatch.setattr("workbench.intent_router.model_intent", lambda *args: calls.append(True))
    drain(queued)
    assert (
        calls == []
        and current(queued, run["id"])["output"]["profile_guard"] == "server_model_disabled"
    )


def test_template_change_blocks_queued_model_and_obsolete_profile_publication(queued, monkeypatch):
    settings = queued[1].state.settings
    settings.agent_router_mode = "model"
    old = draft(queued, model_alias="server_default")
    run = start(queued)
    calls = []
    monkeypatch.setattr("workbench.profiles.prompt_hash", lambda: "f" * 64)
    monkeypatch.setattr("workbench.intent_router.model_intent", lambda *args: calls.append(True))
    response = queued[0].post(
        "/api/v1/runtime/config/versions/" + old["id"] + "/publish",
        headers=queued[2]("admin"),
        json={"expected_revision": 0},
    )
    assert response.status_code == 409
    drain(queued)
    assert calls == []
    assert current(queued, run["id"])["output"]["profile_guard"] == "server_template_changed"
