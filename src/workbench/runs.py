"""Persistent read-only runs; a disconnected subscriber never restarts execution."""

import asyncio
import logging

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from workbench.domain import audit, fingerprint, get_conversation, get_order
from workbench.models import Conversation, Message, Run, RunEvent, User, now

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def event(session, run, kind, **payload):
    session.add(
        RunEvent(
            organization_id=run.organization_id,
            run_id=run.id,
            type=kind,
            payload=jsonable_encoder(payload),
        )
    )


def run_view(run):
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "kind": run.kind,
        "status": run.status,
        "input": run.input,
        "output": run.output,
        "error_code": run.error_code,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def owned_run(session, actor, identifier, *, lock=False):
    statement = select(Run).where(
        Run.id == identifier, Run.owner_id == actor.id, Run.organization_id == actor.organization_id
    )
    if lock:
        statement = statement.with_for_update()
    run = session.scalar(statement)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


def create_run(session, actor, body, key=None, *, enqueue_job=None, settings=None):
    from workbench.config import WorkbenchSettings
    from workbench.quotas import acquire_admission, check_admission, ensure_usage

    settings = settings or WorkbenchSettings()
    acquire_admission(session)
    input_hash = fingerprint(body.model_dump())
    if key:
        existing = session.scalar(
            select(Run).where(
                Run.organization_id == actor.organization_id,
                Run.owner_id == actor.id,
                Run.idempotency_key == key,
            )
        )
        if existing:
            if existing.input_hash != input_hash:
                raise HTTPException(409, "Idempotency key belongs to a different request")
            return existing, False
    check_admission(session, actor, settings)
    # Scope checks happen before creating a run or enqueueing work.
    if body.kind == "order_lookup" or (body.kind == "aftersales" and body.order_number):
        get_order(session, actor, body.order_number)
    if body.conversation_id:
        conversation = get_conversation(session, actor, body.conversation_id)
    else:
        conversation = Conversation(
            organization_id=actor.organization_id,
            owner_id=actor.id,
            title=(
                "查询订单 " + body.order_number
                if body.kind == "order_lookup"
                else ("售后协助 " if body.kind == "aftersales" else "政策咨询 ") + body.query[:80]
            ),
        )
        session.add(conversation)
        session.flush()
    run = Run(
        organization_id=actor.organization_id,
        owner_id=actor.id,
        conversation_id=conversation.id,
        kind=body.kind,
        input=body.model_dump(),
        idempotency_key=key,
        input_hash=input_hash,
    )
    session.add(run)
    try:
        session.flush()
        ensure_usage(session, run, settings, legacy=False)
        from workbench.profiles import snapshot_run

        snapshot_run(session, run, actor, settings)
        session.add(
            Message(
                organization_id=actor.organization_id,
                conversation_id=conversation.id,
                run_id=run.id,
                role="human",
                content=(
                    "查询订单 " + body.order_number
                    if body.kind == "order_lookup"
                    else ("售后协助 " if body.kind == "aftersales" else "政策咨询 ")
                    + body.query
                ),
            )
        )
        event(session, run, "run.queued")
        conversation.updated_at = now()
        audit(session, actor, "run.created", run.id, kind=body.kind)
        if enqueue_job:
            enqueue_job(session, run)
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = (
            session.scalar(
                select(Run).where(
                    Run.organization_id == actor.organization_id,
                    Run.owner_id == actor.id,
                    Run.idempotency_key == key,
                )
            )
            if key
            else None
        )
        if existing and existing.input_hash == input_hash:
            return existing, False
        raise HTTPException(409, "Conflicting run request") from None
    return run, True


def execute_lookup(database, run_id, gateway, token, *, execution_guard=None):
    from workbench.execution_limits import ExecutionLimits, call_bounded
    from workbench.mcp_gateway import ToolFailure
    from workbench.profiles import effective_settings, render_answer
    from workbench.quotas import reserve_tool

    run_settings, profile, profile_guard = effective_settings(database, run_id)
    limits = ExecutionLimits(
        run_settings, reserve_tool_hook=lambda: reserve_tool(database, run_id, execution_guard)
    )

    def on_retry(name, retry, code):
        with database.sessions() as session:
            run = session.get(Run, run_id)
            event(session, run, "tool.retry", name=name, retry=retry, error_code=code)
            session.commit()

    def check_active():
        with database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if execution_guard and not execution_guard(session):
                raise ToolFailure("worker_lease_lost")
            if run.status != "running":
                raise ToolFailure("cancelled")

    try:
        with database.sessions() as session:
            claim = session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == "queued")
                .values(status="running", updated_at=now())
            )
            if execution_guard and not execution_guard(session):
                session.rollback()
                return
            if claim.rowcount != 1:
                session.rollback()
                return
            run = session.get(Run, run_id)
            kind, arguments = run.kind, dict(run.input)
            tool = "get_order_status" if kind == "order_lookup" else "search_knowledge_base"
            event(session, run, "run.started")
            event(session, run, "tool.started", name=tool, transport="mcp")
            session.commit()
        # No database lock is held during network/model inference.
        tool_input = (
            {"order_number": arguments["order_number"]}
            if kind == "order_lookup"
            else {
                "query": arguments["query"],
                "policy_scope": arguments.get("policy_scope"),
                "strategy": "hybrid_rerank",
                "top_k": profile["top_k"],
            }
        )
        payload = asyncio.run(
            call_bounded(
                gateway, tool, tool_input, token, limits, database.settings, on_retry, check_active
            )
        )
        limits.check()
        with database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if execution_guard and not execution_guard(session):
                return
            if run.status != "running":
                return
            actor = session.get(User, run.owner_id)
            if actor is None or not actor.active or actor.organization_id != run.organization_id:
                raise ToolFailure("unauthorized")
            if kind == "order_lookup":
                data = payload["order"]
                answer = (
                    f"订单 {data['number']}：{data['payment_status']}，{data['shipping_status']}。"
                    + (
                        f"预计信息：{data['estimated_delivery']}。"
                        if data.get("estimated_delivery")
                        else ""
                    )
                    + (
                        "实际签收时间未提供。"
                        if not data.get("signed_at")
                        else f"实际签收时间：{data['signed_at']}。"
                    )
                )
                output = {"answer": answer, "order": data, "transport": "mcp"}
            else:
                from workbench.knowledge_service import validated_evidence

                try:
                    matches = validated_evidence(session, actor, payload["matches"])
                except HTTPException as exc:
                    raise ToolFailure("invalid_or_expired_evidence") from exc
                answer = (
                    "已找到已发布的政策原文，以下为依据摘录。请结合适用范围与条件核对。"
                    if matches
                    else "当前已发布资料没有可用依据，无法确认该政策。"
                )
                output = {
                    "answer": answer,
                    "evidence": matches,
                    "retrieval": payload.get("trace", {}),
                    "transport": "mcp",
                    "generation": "source_excerpts",
                }
            event(
                session, run, "tool.finished", name=tool, status=payload["status"], transport="mcp"
            )
            output["answer"] = render_answer(answer, profile)
            answer = output["answer"]
            output["profile_guard"] = profile_guard
            output["execution"] = {"tool_attempts": limits.attempts}
            run.output, run.status, run.updated_at = jsonable_encoder(output), "completed", now()
            session.add(
                Message(
                    organization_id=run.organization_id,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    role="assistant",
                    content=answer,
                )
            )
            session.get(Conversation, run.conversation_id).updated_at = now()
            event(session, run, "answer.published", **run.output)
            event(session, run, "run.finished", status="completed")
            session.commit()
    except Exception as exc:
        code = exc.code if isinstance(exc, ToolFailure) else "run_failed"
        logger.error("Run %s failed: %s", run_id, code)
        with database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if execution_guard and not execution_guard(session):
                return
            if run and run.status not in TERMINAL:
                run.status, run.error_code, run.updated_at = "failed", code, now()
                event(session, run, "tool.failed", error_code=code, transport="mcp")
                event(session, run, "run.finished", status="failed", error_code=code)
                session.commit()


def cancel_run(session, actor, identifier):
    run = owned_run(session, actor, identifier, lock=True)
    if run.kind == "aftersales" and (run.output or {}).get("write_in_flight"):
        raise HTTPException(409, "Submission may be in progress; reconcile actual request first")
    if run.status not in TERMINAL or (run.kind == "aftersales" and run.status == "interrupted"):
        from workbench.job_models import RunJob

        if run.kind == "aftersales":
            previous = run.output or {}
            answer = (
                "已停止流程跟进；已提交的申请未被撤销，请查询真实状态。"
                if previous.get("request")
                else "已停止本次流程，未提交申请。"
            )
            run.output = {**previous, "answer": answer, "evidence": [], "write_in_flight": False}
            for obsolete in ("proposal", "pause_id", "known"):
                run.output.pop(obsolete, None)
            session.add(
                Message(
                    organization_id=run.organization_id,
                    conversation_id=run.conversation_id,
                    run_id=run.id,
                    role="assistant",
                    content=answer,
                )
            )
            event(session, run, "answer.published", **run.output)
        session.execute(
            update(RunJob)
            .where(RunJob.run_id == run.id, RunJob.status == "queued")
            .values(status="skipped", updated_at=now())
        )
        run.status, run.updated_at = "cancelled", now()
        event(session, run, "run.finished", status="cancelled")
        audit(session, actor, "run.cancelled", run.id)
        session.commit()
    return run


def recover_interrupted(database):
    # Single-process foundation deployment: never silently replay an unknown operation.
    with database.sessions() as session:
        runs = session.scalars(select(Run).where(Run.status.in_(["queued", "running"]))).all()
        from workbench.job_models import RunJob

        for run in runs:
            if database.settings.run_dispatch_mode == "worker" and session.scalar(
                select(RunJob.id).where(
                    RunJob.run_id == run.id, RunJob.status.in_(["queued", "running"])
                )
            ):
                continue
            run.status, run.error_code, run.updated_at = "interrupted", "service_restarted", now()
            event(session, run, "run.finished", status="interrupted", error_code=run.error_code)
        session.commit()
