"""Durable dispatch: queued jobs survive restarts; expired active jobs never auto replay."""

import argparse
import logging
import secrets
import time
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, update

from workbench.job_models import RunJob
from workbench.knowledge_models import MCPGrant
from workbench.models import AuthSession, Run, User, now
from workbench.runs import event
from workbench.security import digest
from workbench.tool_contracts import ROLE_SCOPES

logger = logging.getLogger(__name__)
INITIAL = "__initial__"


def enqueue(session, run, parent_hash, action_key=None):
    session.add(
        RunJob(run_id=run.id, action_key=action_key or INITIAL, parent_session_hash=parent_hash)
    )


def lease_valid(session, job_id, token):
    known = session.get(RunJob, job_id)
    if not known:
        return False
    # Lock order is always run -> job, including checkpoint writes and lease expiry.
    session.scalar(select(Run).where(Run.id == known.run_id).with_for_update())
    job = session.scalar(
        select(RunJob)
        .where(RunJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return bool(
        job
        and job.status == "running"
        and job.lease_token == token
        and job.lease_until
        and job.lease_until > now()
    )


def expire_jobs(database):
    with database.sessions() as session:
        candidates = [
            (row.id, row.run_id)
            for row in session.scalars(
                select(RunJob)
                .where(RunJob.status == "running", RunJob.lease_until < now())
                .order_by(RunJob.id)
                .limit(20)
            )
        ]
    count = 0
    for job_id, run_id in candidates:
        with database.sessions() as session:
            run = session.scalar(
                select(Run).where(Run.id == run_id).with_for_update(skip_locked=True)
            )
            if not run:
                continue
            job = session.scalar(select(RunJob).where(RunJob.id == job_id).with_for_update())
            if job.status != "running" or job.lease_until >= now():
                continue
            job.status, job.error_code, job.updated_at = (
                "interrupted",
                "worker_lease_expired",
                now(),
            )
            if run.status in {"queued", "running"}:
                run.status, run.error_code, run.updated_at = "interrupted", job.error_code, now()
                if run.kind == "aftersales":
                    run.output = {
                        **(run.output or {}),
                        "answer": "后台执行已中断；请恢复并核实实际申请状态，勿重复新建。",
                        "engine": "langgraph",
                    }
                event(session, run, "run.finished", status="interrupted", error_code=job.error_code)
            session.commit()
            count += 1
    return count


def claim_job(database):
    expire_jobs(database)
    with database.sessions() as session:
        job = session.scalar(
            select(RunJob)
            .where(RunJob.status == "queued")
            .order_by(RunJob.created_at, RunJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not job:
            return None
        token = secrets.token_hex(16)
        changed = session.execute(
            update(RunJob)
            .where(RunJob.id == job.id, RunJob.status == "queued")
            .values(
                status="running",
                lease_token=token,
                lease_until=now() + timedelta(seconds=database.settings.agent_active_seconds + 60),
                attempts=RunJob.attempts + 1,
                updated_at=now(),
            )
        )
        if changed.rowcount != 1:
            session.rollback()
            return None
        identifier = job.id
        session.commit()
        return identifier, token


def authorize(session, job, run, settings):
    parent = session.get(AuthSession, job.parent_session_hash)
    actor = session.get(User, run.owner_id)
    if not parent or parent.user_id != run.owner_id or parent.expires_at <= now():
        raise HTTPException(401, "Job requires a current parent session")
    if not actor or not actor.active or actor.organization_id != run.organization_id:
        raise HTTPException(401, "Job actor is no longer authorized")
    token = secrets.token_urlsafe(32)
    session.add(
        MCPGrant(
            token_hash=digest(token),
            parent_session_hash=job.parent_session_hash,
            user_id=actor.id,
            audience=settings.mcp_url.rstrip("/"),
            scopes=sorted(ROLE_SCOPES[actor.role]),
            expires_at=min(parent.expires_at, now() + timedelta(minutes=5)),
        )
    )
    return token


def process_job(database, job_id, token, gateway):
    def guard(session):
        return lease_valid(session, job_id, token)

    try:
        with database.sessions() as session:
            if not guard(session):
                return
            job = session.get(RunJob, job_id)
            run = session.scalar(select(Run).where(Run.id == job.run_id).with_for_update())
            if run.status != "queued":
                job.status, job.updated_at = "skipped", now()
                session.commit()
                return
            delegated = authorize(session, job, run, database.settings)
            run_id, kind, action = run.id, run.kind, job.action_key
            session.commit()
        if kind == "aftersales":
            from workbench.aftersales import execute_flow

            execute_flow(
                database,
                run_id,
                gateway,
                delegated,
                None if action == INITIAL else action,
                execution_guard=guard,
            )
        else:
            from workbench.runs import execute_lookup

            execute_lookup(database, run_id, gateway, delegated, execution_guard=guard)
        with database.sessions() as session:
            if not guard(session):
                return
            job = session.get(RunJob, job_id)
            run = session.get(Run, run_id)
            job.status = "failed" if run.status in {"failed", "interrupted"} else "finished"
            job.error_code, job.updated_at = run.error_code, now()
            job.lease_token = job.lease_until = None
            session.commit()
    except Exception as exc:
        code = "job_authorization_expired" if isinstance(exc, HTTPException) else "job_failed"
        logger.error("Job %s stopped: %s", job_id, code)
        with database.sessions() as session:
            if not guard(session):
                return
            job = session.get(RunJob, job_id)
            job.status, job.error_code, job.updated_at = "failed", code, now()
            job.lease_token = job.lease_until = None
            run = session.scalar(select(Run).where(Run.id == job.run_id).with_for_update())
            if run.status in {"queued", "running"}:
                run.status, run.error_code, run.updated_at = "interrupted", code, now()
                event(session, run, "run.finished", status="interrupted", error_code=code)
            session.commit()


def main():
    from workbench.config import WorkbenchSettings
    from workbench.database import Database
    from workbench.mcp_gateway import MCPGateway

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    database = Database(WorkbenchSettings())
    database.check_schema()
    if database.settings.run_dispatch_mode != "worker":
        raise RuntimeError("Worker requires WORKBENCH_RUN_DISPATCH_MODE=worker")
    gateway = MCPGateway(database.settings)
    try:
        while True:
            claim = claim_job(database)
            if claim:
                process_job(database, *claim, gateway)
            if args.once:
                return
            if not claim:
                time.sleep(0.5)
    finally:
        database.close()


if __name__ == "__main__":
    main()
