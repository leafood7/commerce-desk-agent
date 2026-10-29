"""Queue tests: explicit local tool substitutes; real container drill is separate."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import test_aftersales as flow_cases
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_aftersales import count, current, resume, start  # noqa: F401

from workbench.app import create_app
from workbench.checkpoints import SQLCheckpointSaver
from workbench.flow_models import GraphCheckpoint
from workbench.job_models import RunJob
from workbench.mcp_gateway import ToolFailure
from workbench.models import Message, Run, ShippingRequest, now
from workbench.run_worker import claim_job, expire_jobs, process_job

dispatch_fixture = flow_cases.flow


@pytest.fixture
def queued(request):
    case = request.getfixturevalue("dispatch_fixture")
    case[1].state.settings.run_dispatch_mode = "worker"
    return case


def drain(case):
    database = case[1].state.database
    claim = claim_job(database)
    assert claim
    process_job(database, *claim, case[1].state.gateway)
    return claim


def test_queue_survives_api_restart_and_duplicate_submission_executes_once(queued):
    client, app, headers, calls = queued
    body = {"kind": "order_lookup", "order_number": "A1001"}
    auth = {**headers(), "Idempotency-Key": "same-queued-read"}
    first = client.post("/api/v1/runs", json=body, headers=auth).json()
    second = client.post("/api/v1/runs", json=body, headers=auth).json()
    assert first["id"] == second["id"] and first["status"] == "queued"
    assert calls == [] and count(queued, RunJob) == 1
    restarted = create_app(app.state.settings, gateway=app.state.gateway)
    with TestClient(restarted) as other:
        assert (
            other.get("/api/v1/runs/" + first["id"], headers=headers()).json()["status"] == "queued"
        )
    claim = drain(queued)
    assert current(queued, first["id"])["status"] == "completed"
    assert len(calls) == 1
    process_job(app.state.database, *claim, app.state.gateway)
    assert len(calls) == 1
    with app.state.database.sessions() as session:
        job = session.get(RunJob, claim[0])
        assert job.status == "finished" and job.attempts == 1
        assert job.parent_session_hash != auth["Authorization"].split()[1]


def test_enqueue_failure_rolls_back_run_message_and_job_atomically(queued, monkeypatch):
    def fail(*args):
        raise RuntimeError("explicit enqueue failure")

    monkeypatch.setattr("workbench.run_worker.enqueue", fail)
    with pytest.raises(RuntimeError, match="enqueue failure"):
        queued[0].post("/api/v1/runs", json={"order_number": "A1001"}, headers=queued[2]())
    for model in (Run, Message, RunJob):
        assert count(queued, model) == 0


def test_logout_before_worker_execution_revokes_job_without_calling_tools(queued):
    run = start(queued)
    assert run["status"] == "queued"
    queued[0].post("/api/v1/auth/logout", headers=queued[2]())
    drain(queued)
    with queued[1].state.database.sessions() as session:
        row = session.get(Run, run["id"])
        assert row.status == "interrupted" and row.error_code == "job_authorization_expired"
    assert queued[3] == [] and count(queued, ShippingRequest) == 0


def test_cancelled_queued_job_is_skipped(queued):
    run = start(queued)
    assert (
        queued[0].post("/api/v1/runs/" + run["id"] + "/cancel", headers=queued[2]()).status_code
        == 200
    )
    assert claim_job(queued[1].state.database) is None
    assert current(queued, run["id"])["status"] == "cancelled"
    assert queued[3] == []


def test_expired_lease_is_interrupted_not_replayed_and_cannot_write_checkpoint(queued):
    run = start(queued)
    database = queued[1].state.database
    job_id, token = claim_job(database)
    with database.sessions() as session:
        job = session.get(RunJob, job_id)
        job.lease_until = now() - timedelta(seconds=1)
        session.commit()
    assert expire_jobs(database) == 1
    process_job(database, job_id, token, queued[1].state.gateway)
    assert current(queued, run["id"])["error_code"] == "worker_lease_expired"
    assert claim_job(database) is None and queued[3] == []
    saver = SQLCheckpointSaver(database, run["id"], execution_guard=lambda session: False)
    with pytest.raises(ToolFailure, match="worker_lease_lost"):
        saver.put({"configurable": {"thread_id": run["id"]}}, {"id": "blocked"}, {}, {})
    assert count(queued, GraphCheckpoint) == 0


def test_queued_confirmation_is_not_replayed_after_uncertain_write(queued, monkeypatch):
    run = start(queued)
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "awaiting_confirmation" and count(queued, ShippingRequest) == 0
    original = queued[1].state.gateway.call
    failed = [False]

    async def uncertain(name, arguments, token):
        result = await original(name, arguments, token)
        if name == "request_shipping" and not failed[0]:
            failed[0] = True
            raise ToolFailure("timeout")
        return result

    monkeypatch.setattr(queued[1].state.gateway, "call", uncertain)
    run = resume(queued, run, "confirm")
    assert run["status"] == "queued" and count(queued, ShippingRequest) == 0
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "interrupted" and run["output"]["write_in_flight"]
    assert count(queued, ShippingRequest) == 1 and claim_job(queued[1].state.database) is None
    resume(queued, run, "retry")
    drain(queued)
    assert current(queued, run["id"])["status"] == "awaiting_approval"
    assert count(queued, ShippingRequest) == 1


def test_running_job_is_not_interrupted_by_api_restart(queued):
    run = start(queued)
    database = queued[1].state.database
    claim_job(database)
    with database.sessions() as session:
        row = session.get(Run, run["id"])
        row.status = "running"
        session.commit()
    restarted = create_app(queued[1].state.settings, gateway=queued[1].state.gateway)
    with TestClient(restarted) as client:
        assert (
            client.get("/api/v1/runs/" + run["id"], headers=queued[2]()).json()["status"]
            == "running"
        )


def test_two_mysql_claimers_only_one_gets_the_job(queued):
    database = queued[1].state.database
    if database.engine.dialect.name != "mysql":
        pytest.skip("Real row locking requires MySQL")
    start(queued)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_job(database), range(2)))
    assert sum(item is not None for item in claims) == 1


def test_expired_initial_authorization_can_relogin_and_resume_without_checkpoint(queued):
    from conftest import PASSWORD

    from workbench.models import AuthSession
    from workbench.security import digest

    run = start(queued)
    token = queued[2]()["Authorization"].split()[1]
    with queued[1].state.database.sessions() as session:
        session.get(AuthSession, digest(token)).expires_at = now() - timedelta(seconds=1)
        session.commit()
    drain(queued)
    with queued[1].state.database.sessions() as session:
        assert session.get(Run, run["id"]).status == "interrupted"
    response = queued[0].post(
        "/api/v1/auth/login",
        json={"organization": "demo-a", "email": "support@demo.test", "password": PASSWORD},
    )
    assert response.status_code == 200
    new_auth = {"Authorization": "Bearer " + response.json()["access_token"]}
    renewed = (queued[0], queued[1], lambda *args, **kwargs: new_auth, queued[3])
    run = current(renewed, run["id"])
    resume(renewed, run, "retry")
    drain(renewed)
    assert current(renewed, run["id"])["status"] == "awaiting_confirmation"
    assert count(renewed, ShippingRequest) == 0


def test_lease_expiry_during_read_fences_late_checkpoint_and_publication(queued, monkeypatch):
    original = queued[1].state.gateway.call
    database = queued[1].state.database

    async def expires(name, arguments, token):
        result = await original(name, arguments, token)
        with database.sessions() as session:
            job = session.scalar(select(RunJob).where(RunJob.status == "running"))
            job.lease_until = now() - timedelta(seconds=1)
            session.commit()
        return result

    monkeypatch.setattr(queued[1].state.gateway, "call", expires)
    run = start(queued)
    drain(queued)
    expire_jobs(database)
    run = current(queued, run["id"])
    assert run["status"] == "interrupted" and run["error_code"] == "worker_lease_expired"
    assert [name for name, _ in queued[3]] == ["get_order_status"]
    assert count(queued, ShippingRequest) == 0
    from workbench.models import RunEvent

    with database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(RunEvent.run_id == run["id"], RunEvent.type == "answer.published")
            )
            == 0
        )


def test_queued_normal_shipping_reaches_terminal_publication_without_nested_run_lock(queued):
    run = start(queued, shipping_kind="normal")
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "awaiting_confirmation"
    resume(queued, run, "confirm")
    drain(queued)
    run = current(queued, run["id"])
    assert run["status"] == "completed"
    assert run["output"]["request"]["status"] == "completed"
    assert count(queued, ShippingRequest) == 1


def test_queue_observation_is_owner_scoped_and_never_returns_delegation_secrets(queued):
    client, app, headers, calls = queued
    assert client.get("/api/v1/runtime/queue").status_code == 401
    support_run = start(queued)
    other = client.post("/api/v1/runs", headers=headers("admin"), json={"order_number": "A1001"})
    assert other.status_code == 202
    data = client.get("/api/v1/runtime/queue", headers=headers()).json()
    assert data["scope"] == "current_user"
    assert data["counts"]["queued"] == 1 and data["oldest_queued_seconds"] is not None
    assert set(data) == {
        "dispatch",
        "scope",
        "observed_at",
        "counts",
        "oldest_queued_seconds",
        "recent_failures",
    }
    with app.state.database.sessions() as session:
        own_job = session.scalar(select(RunJob).where(RunJob.run_id == support_run["id"]))
        own_job.status, own_job.error_code = "failed", "job_authorization_expired"
        session.commit()
    data = client.get("/api/v1/runtime/queue", headers=headers()).json()
    assert data["counts"]["queued"] == 0 and data["counts"]["failed"] == 1
    assert data["oldest_queued_seconds"] is None
    assert data["recent_failures"][0]["run_id"] == support_run["id"]
    admin = client.get("/api/v1/runtime/queue", headers=headers("admin")).json()
    assert admin["counts"]["queued"] == 1 and admin["recent_failures"] == []
    foreign = client.get("/api/v1/runtime/queue", headers=headers("support", "demo-b")).json()
    assert sum(foreign["counts"].values()) == 0
    assert client.post("/api/v1/runtime/queue", headers=headers(), json={}).status_code == 405
    for forbidden in ("parent_session_hash", "lease_token", "access_token", "Bearer"):
        assert forbidden not in str(data)
