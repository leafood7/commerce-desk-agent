"""Adversarial model substitutes verify the real graph and persistent accounting."""

import json

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_return_graph import current, resume, return_flow, start  # noqa: F401
from test_returns import return_case  # noqa: F401
from test_scoped_mcp import scoped_mcp  # noqa: F401

from workbench.flow_models import FlowAction
from workbench.models import Message, Run
from workbench.return_decisions import (
    choose_return_action,
    decision_messages,
    request_body,
    validate_decision,
)


@pytest.fixture
def model_flow(return_flow, monkeypatch):  # noqa: F811
    client, app, headers, _, _ = return_flow
    settings = app.state.settings
    settings.agent_router_mode = "model"
    settings.return_decision_mode = "model"
    settings.agent_router_url = "https://model.invalid/chat/completions"
    settings.agent_router_model = "test-only-model"
    settings.agent_router_key = SecretStr("test-only-key")
    profile = client.post(
        "/api/v1/runtime/config/versions",
        headers=headers("admin"),
        json={
            "model_alias": "server_default",
            "return_decisions": "model",
            "return_flow_version": "observed_v2",
        },
    )
    assert profile.status_code == 201, profile.text
    assert (
        client.post(
            f"/api/v1/runtime/config/versions/{profile.json()['id']}/publish",
            headers=headers("admin"),
            json={"expected_revision": 0},
        ).status_code
        == 200
    )
    calls = []

    def model(settings, observation, **kwargs):
        calls.append(observation)
        # Deliberately choose a different legal first read than the fixed baseline.
        allowed = observation["allowed_actions"]
        action = (
            "search_policy"
            if "search_policy" in allowed and "read_order" in allowed
            else allowed[0]
        )
        return {
            "proposal": json.dumps({"action": action}),
            "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            "model": "test-only-model",
        }

    monkeypatch.setattr("workbench.return_decisions.model_decision", model)
    return return_flow, calls


def usage(case, run):
    return case[0].get(f"/api/v1/runs/{run['id']}/usage", headers=case[2]()).json()


def test_observation_model_reorders_reads_and_reacts_to_missing_condition(model_flow):
    case, calls = model_flow
    run = start(case, return_condition="unknown")
    assert run["status"] == "awaiting_input", run
    assert [d["action"] for d in run["output"]["decisions"]] == [
        "search_policy",
        "read_order",
        "check_return",
        "ask_condition",
    ]
    assert calls[-1]["server_check"]["missing"] == ["item_condition"]
    assert all("customer_name" not in json.dumps(c) for c in calls)
    run = resume(case, run, "supply", return_condition="unused")
    assert run["status"] == "awaiting_confirmation", run
    assert usage(case, run)["model_attempts"] == usage(case, run)["max_model_attempts"] == 6
    assert any(d["mode"] == "model_quota_fallback" for d in run["output"]["decisions"])
    assert len(calls) == 6  # Later steps follow rules; quota never blocks a valid task.


@pytest.mark.parametrize(
    "proposal",
    [
        '{"action":"approve"}',
        '{"action":"review","answer":"BLOCKED_MODEL_DRAFT"}',
        '{"action":"read_order","arguments":{"order_number":"B2001"}}',
        "not json",
    ],
)
def test_model_cannot_publish_draft_change_args_or_skip_steps(model_flow, monkeypatch, proposal):
    case, _ = model_flow
    monkeypatch.setattr(
        "workbench.return_decisions.model_decision",
        lambda *a, **k: {
            "proposal": proposal,
            "usage": {"total_tokens": 20},
            "model": "substitute",
        },
    )
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    assert not run["output"].get("request")
    public = case[0].get(f"/api/v1/runs/{run['id']}/events", headers=case[2]()).text
    with case[1].state.database.sessions() as session:
        messages = session.scalars(select(Message).where(Message.run_id == run["id"])).all()
        public += json.dumps(run) + "".join(m.content for m in messages)
    assert "BLOCKED_MODEL_DRAFT" not in public
    data = usage(case, run)
    assert data["model_observed_tokens"] > 0 and data["unknown_model_attempts"] == 0


def test_model_timeout_retains_unknown_reservation_and_falls_back(model_flow, monkeypatch):
    case, calls = model_flow

    def timeout(*a, **k):
        calls.append("timeout")
        raise TimeoutError("test-only")

    monkeypatch.setattr("workbench.return_decisions.model_decision", timeout)
    run = start(case)
    assert run["status"] == "awaiting_confirmation", run
    data = usage(case, run)
    assert data["unknown_model_attempts"] == 2 and data["model_reserved_tokens"] == 16384
    assert len(calls) == 2
    assert any(d["mode"] == "model_quota_fallback" for d in run["output"]["decisions"])


def test_corrections_replace_old_facts_and_can_stop_before_write(model_flow):
    case, calls = model_flow
    run = start(case)
    run = resume(case, run, "correct", return_condition="used", reason="更正：确实使用过")
    assert run["status"] == "completed", run
    assert not run["output"].get("request")
    assert "configured_unused_condition_not_met" in run["output"]["answer"]
    assert any(c["latest_conditions"]["item_condition"] == "used" for c in calls)


def test_identical_observation_is_cached_and_cannot_replay_unknown_request(model_flow):
    case, calls = model_flow
    run = start(case)
    with case[1].state.database.sessions() as session:
        row = session.get(Run, run["id"])
        row.status = "running"
        session.commit()
        action = session.scalar(
            select(FlowAction).where(
                FlowAction.run_id == row.id, FlowAction.key.like("__return_decision__:%")
            )
        )
        from workbench.domain import fingerprint
        from workbench.return_decisions import prompt_hash

        context = next(
            c
            for c in calls
            if action.input_hash == fingerprint({"observation": c, "prompt_sha256": prompt_hash()})
        )
        action.body = {"status": "reserved"}
        session.commit()
    from workbench.execution_limits import ExecutionLimits

    count = len(calls)
    result = choose_return_action(
        case[1].state.database,
        run["id"],
        case[1].state.settings,
        context,
        limits=ExecutionLimits(case[1].state.settings),
    )
    assert result["mode"] == "unknown_cached_fallback" and len(calls) == count


def test_prompt_or_server_change_disables_new_calls_on_old_snapshot(model_flow):
    case, calls = model_flow
    run = start(case)
    case[1].state.settings.return_decision_mode = "rules"
    count = len(calls)
    run = resume(case, run, "correct", reason="新原因")
    assert run["status"] == "awaiting_confirmation" and len(calls) == count
    assert run["output"]["profile_guard"] == "return_model_disabled"


def test_old_profile_without_return_contract_keeps_legacy_graph(model_flow):
    case, calls = model_flow
    # A missing new prompt binding cannot silently authorize model calls.
    with case[1].state.database.sessions() as session:
        from workbench.profile_models import ProfileHead, ProfileVersion

        head = session.scalar(select(ProfileHead))
        version = session.get(ProfileVersion, head.version_id)
        values = dict(version.values)
        for key in (
            "return_decisions",
            "return_flow_version",
            "return_retrieval_mode",
            "return_prompt_sha256",
        ):
            values.pop(key, None)
        from workbench.domain import fingerprint

        version.values, version.config_hash = values, fingerprint(values)
        session.commit()
    run = start(case)
    assert run["status"] == "awaiting_confirmation" and not calls
    assert usage(case, run)["max_model_attempts"] == 1


def test_observation_contract_rejects_unsupported_actions_and_preflight_limits():
    from workbench.config import WorkbenchSettings
    from workbench.intent_router import ModelNotSent

    observation = {
        "allowed_actions": ["ask_condition"],
        "latest_conditions": {"item_condition": "unknown"},
    }
    assert validate_decision({"action": "ask_condition"}, observation) == "ask_condition"
    with pytest.raises(ValueError):
        validate_decision({"action": "review"}, observation)
    settings = WorkbenchSettings(run_model_reserve_tokens=256)
    with pytest.raises(ModelNotSent):
        request_body(settings, observation)
    assert "untrusted" in decision_messages(observation)[0]["content"]


def test_new_prompt_hash_blocks_old_model_snapshot(model_flow, monkeypatch):
    case, calls = model_flow
    run = start(case)
    count = len(calls)
    monkeypatch.setattr("workbench.profiles.return_prompt_hash", lambda: "changed-return-template")
    run = resume(case, run, "correct", reason="核对新的原因")
    assert run["status"] == "awaiting_confirmation" and len(calls) == count
    assert run["output"]["profile_guard"] == "return_template_changed"


def test_preflight_rejection_releases_tokens_but_not_attempts(model_flow, monkeypatch):
    from workbench.intent_router import ModelNotSent

    case, _ = model_flow

    def reject(*a, **k):
        raise ModelNotSent("no HTTP dispatch")

    monkeypatch.setattr("workbench.return_decisions.model_decision", reject)
    run = start(case)
    assert run["status"] == "awaiting_confirmation"
    data = usage(case, run)
    assert data["model_attempts"] == 4
    assert (
        data["model_reserved_tokens"]
        == data["model_observed_tokens"]
        == data["unknown_model_attempts"]
        == 0
    )


def test_model_limit_does_not_reset_after_settings_change(model_flow):
    case, calls = model_flow
    case[1].state.settings.return_model_limit = 2
    run = start(case)
    assert len(calls) == 2
    case[1].state.settings.return_model_limit = 6
    run = resume(case, run, "correct", reason="新的核对原因")
    assert run["status"] == "awaiting_confirmation" and len(calls) == 2
    assert usage(case, run)["max_model_attempts"] == 2


def test_negative_usage_is_unknown_not_zero(model_flow, monkeypatch):
    case, _ = model_flow
    monkeypatch.setattr(
        "workbench.return_decisions.model_decision",
        lambda settings, obs, **kw: {
            "proposal": json.dumps({"action": obs["allowed_actions"][0]}),
            "usage": {"prompt_tokens": -1, "completion_tokens": 3, "total_tokens": 2},
        },
    )
    run = start(case)
    data = usage(case, run)
    assert run["status"] == "awaiting_confirmation"
    assert data["unknown_model_attempts"] == 2 and data["model_reserved_tokens"] == 16384


def test_http_adapter_caps_response_and_keeps_hidden_reasoning_private(monkeypatch):
    import httpx

    from workbench.config import WorkbenchSettings
    from workbench.return_decisions import model_decision

    settings = WorkbenchSettings(
        agent_router_mode="model",
        agent_router_url="https://model.invalid/chat/completions",
        agent_router_key=SecretStr("test-only-key"),
        agent_router_model="deepseek-flash",
    )
    actual_client = httpx.Client
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        if len(seen) > 1:
            return httpx.Response(200, content=b"x" * 70000)
        return httpx.Response(
            200,
            json={
                "model": "substitute",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"action":"ask_condition"}',
                            "reasoning_content": "HIDDEN_REASONING",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        )

    monkeypatch.setattr(
        "workbench.return_decisions.httpx.Client",
        lambda **kw: actual_client(**kw, transport=httpx.MockTransport(respond)),
    )
    result = model_decision(settings, {"allowed_actions": ["ask_condition"]})
    assert "HIDDEN_REASONING" not in json.dumps(result)
    assert seen[0]["thinking"] == {"type": "disabled"} and seen[0]["temperature"] == 0
    assert "test-only-key" not in json.dumps(seen)
    with pytest.raises(ValueError, match="response too large"):
        model_decision(settings, {"allowed_actions": ["ask_condition"]})


def test_http_response_checks_remaining_deadline(monkeypatch):
    import httpx

    from workbench.config import WorkbenchSettings
    from workbench.return_decisions import model_decision

    settings = WorkbenchSettings(
        agent_router_mode="model",
        agent_router_url="https://model.invalid/chat/completions",
        agent_router_key=SecretStr("test-only-key"),
        agent_router_model="deepseek-flash",
    )
    actual_client = httpx.Client
    monkeypatch.setattr(
        "workbench.return_decisions.httpx.Client",
        lambda **kw: actual_client(
            **kw, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"{}"))
        ),
    )
    readings = iter([0, 11])
    monkeypatch.setattr("workbench.return_decisions.monotonic", lambda: next(readings))
    with pytest.raises(TimeoutError, match="deadline"):
        model_decision(settings, {"allowed_actions": ["ask_condition"]}, timeout=10)
