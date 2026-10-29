"""Run lifecycle, continuation fencing and publication for the private graph."""

import asyncio
import logging

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from langgraph.types import Command
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from workbench.aftersales_graph import build_graph
from workbench.checkpoints import SQLCheckpointSaver
from workbench.domain import audit, fingerprint
from workbench.execution_limits import ExecutionLimits, call_bounded
from workbench.flow_models import FlowAction
from workbench.knowledge_service import validated_evidence
from workbench.mcp_gateway import ToolFailure
from workbench.models import Conversation, Message, Run, User, now
from workbench.runs import TERMINAL, event, owned_run

WAITING = {"awaiting_input", "awaiting_confirmation", "awaiting_approval"}
logger = logging.getLogger(__name__)


class FlowStopped(Exception):
    pass


def continue_flow(session, actor, identifier, body, key, *, enqueue_job=None, settings=None):
    if not key or key.startswith("__"):
        raise HTTPException(422, "Idempotency-Key required for continuation")
    from workbench.config import WorkbenchSettings
    from workbench.quotas import acquire_admission, check_admission
    from workbench.runs import cancel_run

    settings = settings or WorkbenchSettings()
    acquire_admission(session)
    run = owned_run(session, actor, identifier, lock=True)
    if run.kind != "aftersales":
        raise HTTPException(409, "Not an after-sales run")
    return_fields = (
        body.return_item_id,
        body.return_quantity,
        body.return_condition,
        body.return_policy_id,
        body.return_description,
        body.return_basis,
    )
    if not run.input.get("return_policy_id") and any(v is not None for v in return_fields):
        raise HTTPException(422, "Return fields require a return workflow")
    if run.input.get("return_policy_id") and body.shipping_kind is not None:
        raise HTTPException(422, "Shipping fields cannot modify a return workflow")
    if body.return_description is not None:
        from workbench.profile_models import RunProfile

        snapshot = session.get(RunProfile, run.id)
        if not snapshot or snapshot.values.get("return_flow_version") != "guided_v3":
            raise HTTPException(409, "This saved workflow does not support description review")
    previous = session.get(FlowAction, (run.id, key))
    body_data = body.model_dump(mode="json")
    body_hash = fingerprint(
        {
            k: v
            for k, v in body_data.items()
            if k not in {"return_description", "condition_proposal_hash", "return_basis"}
            or v is not None
        }
    )
    if previous:
        if previous.input_hash != body_hash:
            raise HTTPException(409, "Continuation key belongs to different input")
        return run, False
    allowed = {
        "awaiting_input": {"supply", "accept_condition"},
        "awaiting_confirmation": {"confirm", "correct", "cancel"},
        "awaiting_approval": {"refresh"},
        "interrupted": {"retry"},
    }
    if (run.output or {}).get("pause_kind") == "quality_wait":
        allowed["awaiting_input"] = {"refresh"}
    if body.action not in allowed.get(run.status, set()):
        raise HTTPException(409, "Action is not valid for the current pause")
    if run.status in WAITING and body.pause_id != (run.output or {}).get("pause_id"):
        raise HTTPException(409, "Pause has changed; reload the current run")
    if body.action == "accept_condition":
        proposal = (run.output or {}).get("condition_proposal") or {}
        if (
            (run.output or {}).get("pause_kind") != "condition_confirmation"
            or proposal.get("candidate") not in {"used", "unused"}
            or body.condition_proposal_hash != proposal.get("proposal_hash")
        ):
            raise HTTPException(409, "Condition proposal changed or is unavailable")
    if body.action == "cancel":
        session.add(FlowAction(run_id=run.id, key=key, input_hash=body_hash, body=body_data))
        return cancel_run(session, actor, identifier), False
    check_admission(session, actor, settings)
    if (
        session.scalar(
            select(func.count()).select_from(FlowAction).where(FlowAction.run_id == run.id)
        )
        >= 100
    ):
        raise HTTPException(409, "Continuation limit reached; query actual request status")
    expected = run.status
    changed = session.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == expected)
        .values(status="queued", updated_at=now())
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Another continuation is active")
    session.add(FlowAction(run_id=run.id, key=key, input_hash=body_hash, body=body_data))
    event(session, run, "run.resumed", action=body.action)
    audit(session, actor, "aftersales.resumed", run.id, continuation_action=body.action)
    if body.action in {"supply", "correct"}:
        session.add(
            Message(
                organization_id=run.organization_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                role="human",
                content="补充/更正："
                + str(
                    {
                        k: v
                        for k, v in body_data.items()
                        if k not in {"pause_id", "action"} and v is not None
                    }
                ),
            )
        )
    try:
        if enqueue_job:
            enqueue_job(session, run)
        session.commit()
    except IntegrityError:
        session.rollback()
        previous = session.get(FlowAction, (run.id, key))
        if previous and previous.input_hash == body_hash:
            return session.get(Run, run.id), False
        raise HTTPException(409, "Conflicting continuation") from None
    session.refresh(run)
    return run, True


def execute_flow(database, run_id, gateway, token, action_key=None, *, execution_guard=None):
    write_started = False
    from workbench.profiles import effective_settings, render_answer
    from workbench.quotas import reserve_tool

    run_settings, profile, profile_guard = effective_settings(database, run_id)
    limits = ExecutionLimits(
        run_settings, reserve_tool_hook=lambda: reserve_tool(database, run_id, execution_guard)
    )

    def ensure_actor(session, run):
        if execution_guard and not execution_guard(session):
            raise FlowStopped()
        user = session.get(User, run.owner_id)
        if not user or not user.active or user.organization_id != run.organization_id:
            raise ToolFailure("unauthorized")
        return user

    def tools(name, arguments):
        nonlocal write_started
        from workbench.tool_contracts import CONTRACTS

        write = not CONTRACTS[name][3]
        arguments = dict(arguments)
        if name in {"request_shipping", "create_return_draft"}:
            arguments["idempotency_key"] = "aftersales:" + run_id

        def on_attempt():
            nonlocal write_started
            with database.sessions() as session:
                run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
                if run.status != "running":
                    raise FlowStopped()
                ensure_actor(session, run)
                if write:
                    write_started = True
                    run.output = {**(run.output or {}), "write_in_flight": True}
                event(
                    session,
                    run,
                    "tool.started",
                    name=name,
                    transport="mcp",
                    write=write,
                    attempt=limits.attempts,
                )
                session.commit()

        def on_retry(tool, retry, code):
            with database.sessions() as session:
                run = session.get(Run, run_id)
                event(session, run, "tool.retry", name=tool, retry=retry, error_code=code)
                session.commit()

        result = jsonable_encoder(
            asyncio.run(
                call_bounded(
                    gateway, name, arguments, token, limits, database.settings, on_retry, on_attempt
                )
            )
        )
        # Persist real write receipts before graph/checkpoint publication.
        with database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if execution_guard and not execution_guard(session):
                raise FlowStopped()
            if write:
                run.output = {
                    **(run.output or {}),
                    "request": result["request"],
                    "write_in_flight": False,
                    "audit_status": result.get("audit_status", "recorded"),
                }
            event(
                session, run, "tool.finished", name=name, status=result["status"], transport="mcp"
            )
            session.commit()
        return result

    def validate(matches):
        with database.sessions() as session:
            run = session.get(Run, run_id)
            return validated_evidence(session, ensure_actor(session, run), matches)

    try:
        with database.sessions() as session:
            claim = session.execute(
                update(Run)
                .where(Run.id == run_id, Run.status == "queued")
                .values(status="running", updated_at=now())
            )
            if claim.rowcount != 1:
                session.rollback()
                return
            run = session.get(Run, run_id)
            ensure_actor(session, run)
            initial = {
                k: v
                for k, v in run.input.items()
                if k
                in {
                    "query",
                    "order_number",
                    "shipping_kind",
                    "reason",
                    "return_policy_id",
                    "return_item_id",
                    "return_quantity",
                    "return_condition",
                    "return_description",
                    "return_basis",
                }
            }
            action = session.get(FlowAction, (run_id, action_key)).body if action_key else None
            event(session, run, "run.started", engine="langgraph")
            session.commit()
        from workbench.intent_router import route_once

        graph_builder = build_graph

        def planner(query):
            return route_once(database, run_id, run_settings, query)

        if run.input.get("return_policy_id"):
            from workbench.return_decisions import choose_return_action
            from workbench.return_graph import build_return_graph
            from workbench.tool_contracts import verify_grant

            def model_guard(session, record):
                ensure_actor(session, record)
                user, _ = verify_grant(session, token, run_settings)
                if user.id != record.owner_id or user.role != "support":
                    raise ToolFailure("forbidden")

            def return_planner(observation):
                result = choose_return_action(
                    database, run_id, run_settings, observation, limits=limits, guard=model_guard
                )
                with database.sessions() as session:
                    record = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
                    model_guard(session, record)
                    if record.status != "running":
                        raise FlowStopped()
                    event(
                        session,
                        record,
                        "agent.decision",
                        action=result["action"],
                        mode=result["mode"],
                    )
                    session.commit()
                return result

            graph_builder = build_return_graph
            planner = return_planner
        graph = graph_builder(
            SQLCheckpointSaver(database, run_id, execution_guard=execution_guard),
            tools,
            validate,
            planner,
            profile=profile,
        )
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 64}
        snapshot = graph.get_state(config)
        if not snapshot.values:
            if action and action["action"] != "retry":
                raise ValueError("Missing persisted graph")
            argument = initial
        else:
            argument = None if not action or action["action"] == "retry" else Command(resume=action)
        for node_updates in graph.stream(argument, config, stream_mode="updates"):
            limits.check()
            # Publish node names only. State, model text and checkpoint data remain private.
            for name in node_updates:
                if name.startswith("__"):
                    continue
                with database.sessions() as session:
                    run = session.get(Run, run_id)
                    if execution_guard and not execution_guard(session):
                        raise FlowStopped()
                    if run.status == "running":
                        event(session, run, "graph.step", node=name)
                        session.commit()
        snapshot = graph.get_state(config)
        state = snapshot.values
        pending = [item for task in snapshot.tasks for item in task.interrupts]
        with database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if run.status != "running":
                return
            ensure_actor(session, run)
            if pending:
                pause = pending[0]
                public = jsonable_encoder(dict(pause.value))
                public["pause_id"] = pause.id
                if public.get("evidence"):
                    public["evidence"] = validated_evidence(
                        session, ensure_actor(session, run), public["evidence"]
                    )
                status = public.pop("status")
            else:
                warnings = []
                try:
                    evidence = validated_evidence(
                        session, ensure_actor(session, run), state.get("evidence", [])
                    )
                except HTTPException:
                    if not state.get("request"):
                        raise
                    evidence = []
                    warnings = ["先前政策来源已变化，已移除引用；办理状态来自实际业务记录。"]
                public = {
                    "answer": state.get("answer", "处理已结束。"),
                    "order": state.get("order"),
                    "request": state.get("request"),
                    "evidence": evidence,
                    "warnings": warnings,
                    "audit_status": state.get("audit_status", "recorded"),
                    "retrieval": state.get("retrieval", {}),
                }
                status = state.get("terminal", "completed")
            public["answer"] = render_answer(public["answer"], profile)
            public.setdefault("retrieval", state.get("retrieval", {}))
            if profile.get("return_retrieval_mode") == "clause_bound" and not public.get(
                "warnings"
            ):
                public["policy_context"] = state.get("policy_context", [])
                public["context_purpose"] = state.get("context_purpose", "application")
            if profile.get("return_flow_version") == "guided_v3":
                from workbench.return_fact_checks import fact_checks, render_fact_checks

                assessment = fact_checks(
                    state.get("query"),
                    state.get("order") or {},
                    evidence=public.get("evidence"),
                    quality=state.get("quality_info"),
                )
                public.update(
                    fact_assessment=assessment,
                    condition_origin=state.get("condition_origin"),
                    flow_version="guided_v3",
                )
                preview = state.get("preview") or {}
                public["business_checks"] = {
                    key: preview.get(key) for key in ("eligibility", "missing", "blocked")
                }
                public["answer"] += render_fact_checks(assessment)
            public["profile_guard"] = profile_guard
            public["execution"] = {"tool_attempts": limits.attempts}
            public.update(
                engine="langgraph",
                transport="mcp",
                write_in_flight=False,
                planner_mode=state.get("planner_mode", "rules"),
                tool_plan_mode=state.get("tool_plan_mode", "fixed"),
                decisions=state.get("decisions", []),
            )
            old_pause = (run.output or {}).get("pause_id")
            run.output, run.status, run.error_code, run.updated_at = public, status, None, now()
            if not pending or old_pause != public.get("pause_id"):
                session.add(
                    Message(
                        organization_id=run.organization_id,
                        conversation_id=run.conversation_id,
                        run_id=run.id,
                        role="assistant",
                        content=public["answer"],
                    )
                )
            session.get(Conversation, run.conversation_id).updated_at = now()
            event(session, run, "answer.published", **public)
            event(session, run, "run.paused" if pending else "run.finished", status=status)
            session.commit()
    except FlowStopped:
        return
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, ToolFailure)
            else "invalid_or_expired_evidence"
            if isinstance(exc, HTTPException)
            else "flow_failed"
        )
        logger.exception("After-sales run %s stopped: %s", run_id, code)
        with database.sessions() as session:
            run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
            if execution_guard and not execution_guard(session):
                return
            if run and run.status not in TERMINAL:
                uncertain = write_started or (run.output or {}).get("write_in_flight", False)
                run.status, run.error_code, run.updated_at = "interrupted", code, now()
                run.output = {
                    **(run.output or {}),
                    "answer": "本次处理暂停；申请可能已经提交，请点击恢复核实真实状态，勿重复新建。"
                    if uncertain
                    else "本次处理未完成，未新增办理承诺。可恢复重试，或联系人工核实。",
                    "write_in_flight": bool(uncertain),
                    "engine": "langgraph",
                }
                if code == "run_tool_quota":
                    run.output["answer"] = (
                        "本流程累计工具额度已用完；已保存的办理记录仍保留。请直接查询实际申请或联系人工核实，勿重复申请。"
                    )
                event(session, run, "tool.failed", error_code=code)
                event(session, run, "run.paused", status="interrupted", error_code=code)
                session.commit()
