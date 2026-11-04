"""Bounded diagnostic spans. Never record arguments, answers, SQL text or credentials."""

import inspect
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from threading import Lock
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from workbench.models import Run, User, now
from workbench.trace_models import TraceRecord, TraceSpan

log = logging.getLogger(__name__)
context = ContextVar("workbench_trace", default=None)
active_span = ContextVar("workbench_span", default=None)
SAFE = {
    "http_status",
    "db_calls",
    "db_ms",
    "match_count",
    "version_count",
    "dense_count",
    "sparse_count",
    "result",
    "run_status",
    "transport",
    "stream_setup_only",
    "resource_id",
    "version_ids",
    "chunk_ids",
    "job_status",
    "attempt",
    "db_errors",
}


def identifier(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def code(value):
    return (
        value[:64]
        if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", value)
        else "operation_failed"
    )


@dataclass
class TraceContext:
    database: object
    id: str = field(default_factory=lambda: str(uuid4()))
    owner_id: str | None = None
    organization_id: str | None = None
    parent: str | None = None
    pending: list = field(default_factory=list)
    saved: int = 0
    operation: str = "request"
    lock: object = field(default_factory=Lock)


def register(database, actor):
    ctx = context.get()
    if ctx is None:
        return
    ctx.owner_id, ctx.organization_id = actor.id, actor.organization_id
    try:
        with database.sessions() as s:
            row = s.get(TraceRecord, ctx.id)
            if row and (row.owner_id != actor.id or row.organization_id != actor.organization_id):
                ctx.id = str(uuid4())
                row = None
                ctx.parent = None
            if row is None:
                s.add(
                    TraceRecord(
                        id=ctx.id,
                        owner_id=actor.id,
                        organization_id=actor.organization_id,
                        operation=ctx.operation[:120],
                    )
                )
                s.commit()
    except Exception:
        log.warning("trace_registration_unavailable trace_id=%s", ctx.id)


def link(session, run, job=None):
    ctx = context.get()
    if ctx is None or ctx.owner_id != run.owner_id or ctx.organization_id != run.organization_id:
        return
    row = session.get(TraceRecord, ctx.id)
    if row:
        row.run_id = run.id
        if job:
            row.job_id = job.id
            current = active_span.get()
            if current:
                row.dispatch_parent_id = current["id"]


def link_resource(session, kind, resource_id):
    """Attach the first business resource touched by this request to its trace."""
    ctx = context.get()
    if ctx is None or not ctx.owner_id or not resource_id:
        return
    row = session.get(TraceRecord, ctx.id)
    if row and not row.resource_id:
        row.resource_kind = kind[:32]
        row.resource_id = str(resource_id)[:64]


def flush():
    ctx = context.get()
    if not ctx or not ctx.owner_id or not ctx.pending:
        return
    with ctx.lock:
        rows = ctx.pending[:]
        ctx.pending.clear()
    try:
        with ctx.database.sessions() as s:
            root = s.get(TraceRecord, ctx.id)
            if not root:
                return
            if ctx.operation.startswith(("GET ", "POST ", "PUT ", "DELETE ")):
                root.operation = ctx.operation[:120]
            for item in rows:
                if not s.get(TraceSpan, item["id"]):
                    s.add(TraceSpan(trace_id=ctx.id, **item))
            s.commit()
    except Exception:
        # A diagnostic failure must not change a committed business receipt.
        log.warning("trace_persistence_unavailable trace_id=%s", ctx.id)


@contextmanager
def span(service, operation):
    ctx = context.get()
    if not ctx:
        yield None
        return
    parent = active_span.get()
    row = {
        "id": str(uuid4()),
        "parent_id": parent["id"] if parent else ctx.parent,
        "service": service,
        "operation": operation[:120],
        "status": "completed",
        "error_code": None,
        "details": {},
        "started_at": now(),
        "elapsed_ms": 0,
    }
    token = active_span.set(row)
    start = perf_counter()
    try:
        yield row
    except BaseException as exc:
        row["status"] = (
            "cancelled" if type(exc).__name__ in {"CancelledError", "GeneratorExit"} else "failed"
        )
        row["error_code"] = code(
            getattr(exc, "code", None)
            or (
                "http_" + str(exc.status_code)
                if hasattr(exc, "status_code")
                else "operation_failed"
            )
        )
        raise
    finally:
        row["elapsed_ms"] = max(0, int((perf_counter() - start) * 1000))
        row["details"] = {
            k: v
            for k, v in row["details"].items()
            if k in SAFE
            and (
                isinstance(v, (int, float, bool, str, type(None)))
                or (
                    k in {"version_ids", "chunk_ids"}
                    and isinstance(v, list)
                    and len(v) <= 40
                    and all(identifier(x) for x in v)
                )
            )
        }
        with ctx.lock:
            if ctx.saved < 1000:
                ctx.pending.append(row)
                ctx.saved += 1
        active_span.reset(token)


def result_details(row, result):
    if row is None or not isinstance(result, dict):
        return
    status = result.get("status")
    if status in {"found", "no_evidence", "success", "error"}:
        row["details"]["result"] = status
    if status == "error":
        row["status"] = "failed"
        row["error_code"] = code(result.get("error", {}).get("code"))
    request = result.get("request")
    if isinstance(request, dict) and identifier(request.get("id")):
        row["details"]["resource_id"] = request["id"]
    if isinstance(result.get("matches"), list):
        matches = result["matches"]
        row["details"]["match_count"] = len(matches)
        row["details"]["version_ids"] = list(
            dict.fromkeys(
                x["version_id"]
                for x in matches
                if isinstance(x, dict) and identifier(x.get("version_id"))
            )
        )[:40]
        row["details"]["chunk_ids"] = [
            x["id"] for x in matches if isinstance(x, dict) and identifier(x.get("id"))
        ][:40]


def traced(service, operation):
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_call(*args, **kwargs):
                with span(service, operation) as row:
                    result = await fn(*args, **kwargs)
                    result_details(row, result)
                    return result

            return async_call

        @wraps(fn)
        def call(*args, **kwargs):
            with span(service, operation) as row:
                result = fn(*args, **kwargs)
                result_details(row, result)
                return result

        return call

    return decorate


def trace_node(fn, name):
    return traced("langgraph", name)(fn)


def trace_execution(fn):
    @wraps(fn)
    def call(database, run_id, *args, **kwargs):
        previous = context.get()
        token = None
        if previous is None:
            with database.sessions() as s:
                run = s.get(Run, run_id)
                if run:
                    record = s.scalar(
                        select(TraceRecord)
                        .where(TraceRecord.run_id == run_id)
                        .order_by(TraceRecord.created_at.desc())
                        .limit(1)
                    )
                    ctx = TraceContext(
                        database,
                        id=record.id if record else str(uuid4()),
                        owner_id=run.owner_id,
                        organization_id=run.organization_id,
                        operation="legacy_run",
                    )
                    token = context.set(ctx)
                    actor = s.get(User, run.owner_id)
                    register(database, actor)
                    with database.sessions() as linked:
                        link(linked, run)
                        linked.commit()
        try:
            with span("agent", fn.__name__) as row:
                result = fn(database, run_id, *args, **kwargs)
                if row:
                    with database.sessions() as s:
                        run = s.get(Run, run_id)
                        if run:
                            row["details"]["run_status"] = run.status
                            if run.error_code:
                                row["error_code"] = code(run.error_code)
                            if run.status in {"failed", "interrupted"}:
                                row["status"] = "failed"
                            elif run.status == "cancelled":
                                row["status"] = "cancelled"
                            elif run.status.startswith("awaiting"):
                                row["status"] = "paused"
                return result
        finally:
            flush()
            if token:
                context.reset(token)

    return call


def trace_job(fn):
    @wraps(fn)
    def call(database, job_id, *args, **kwargs):
        from workbench.job_models import RunJob

        with database.sessions() as s:
            job = s.get(RunJob, job_id)
            record = s.scalar(
                select(TraceRecord)
                .where(TraceRecord.job_id == job_id)
                .order_by(TraceRecord.created_at.desc())
                .limit(1)
            )
            run = s.get(Run, job.run_id) if job else None
            if not run:
                return fn(database, job_id, *args, **kwargs)
            ctx = TraceContext(
                database,
                id=record.id if record else str(uuid4()),
                owner_id=run.owner_id,
                organization_id=run.organization_id,
                operation="background_job",
                parent=record.dispatch_parent_id if record else None,
            )
            token = context.set(ctx)
            actor = s.get(User, run.owner_id)
            register(database, actor)
            with database.sessions() as linked:
                link(linked, run, job)
                linked.commit()
        try:
            with span("worker", "dispatch") as row:
                result = fn(database, job_id, *args, **kwargs)
                if row:
                    with database.sessions() as current:
                        job = current.get(RunJob, job_id)
                        if job:
                            row["details"]["job_status"] = job.status
                            row["details"]["attempt"] = job.attempts
                            if job.status == "failed":
                                row["status"] = "failed"
                                row["error_code"] = code(job.error_code)
                return result
        finally:
            flush()
            context.reset(token)

    return call


def headers():
    ctx = context.get()
    row = active_span.get()
    return (
        {"X-Trace-ID": ctx.id, "X-Parent-Span-ID": row["id"]}
        if ctx and row and ctx.owner_id
        else {}
    )


class MCPTraceMiddleware:
    def __init__(self, app, database):
        self.app, self.database = app, database

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        values = {k.lower(): v for k, v in scope.get("headers", [])}
        ctx = TraceContext(self.database, operation="mcp")
        incoming = identifier(values.get(b"x-trace-id", b"").decode("ascii", errors="ignore"))
        if incoming:
            ctx.id = incoming
            ctx.parent = identifier(
                values.get(b"x-parent-span-id", b"").decode("ascii", errors="ignore")
            )
        token = context.set(ctx)
        st = active_span.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            flush()
            active_span.reset(st)
            context.reset(token)


def attach_routes(app, session_dependency, actor):
    from fastapi import Depends, HTTPException, Query

    def visible(user):
        # Same owner policy as run/history APIs, including administrators.
        return (
            TraceRecord.organization_id == user.organization_id,
            TraceRecord.owner_id == user.id,
        )

    @app.get("/api/v1/traces")
    def traces(
        run_id: str | None = None,
        resource_id: str | None = None,
        limit: int = Query(20, ge=1, le=50),
        offset: int = Query(0, ge=0),
        user=Depends(actor),
        session=Depends(session_dependency),
    ):
        q = select(TraceRecord).where(*visible(user))
        if run_id:
            q = q.where(TraceRecord.run_id == run_id)
        if resource_id:
            q = q.where(
                or_(
                    TraceRecord.resource_id == resource_id,
                    TraceRecord.run_id == resource_id,
                )
            )
        rows = session.scalars(
            q.order_by(TraceRecord.created_at.desc(), TraceRecord.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "run_id": r.run_id,
                    "resource_kind": r.resource_kind,
                    "resource_id": r.resource_id,
                    "operation": r.operation,
                    "created_at": r.created_at,
                }
                for r in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "offset": offset,
        }

    @app.get("/api/v1/cases/{resource_id}/trail")
    def case_trail_route(
        resource_id: str,
        user=Depends(actor),
        session=Depends(session_dependency),
    ):
        if not 1 <= len(resource_id) <= 64:
            raise HTTPException(404, "Case not found")
        from workbench.case_trail import case_trail

        return case_trail(session, user, resource_id)

    @app.get("/api/v1/traces/{trace_id}")
    def detail(
        trace_id: str,
        limit: int = Query(200, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(actor),
        session=Depends(session_dependency),
    ):
        row = session.scalar(select(TraceRecord).where(TraceRecord.id == trace_id, *visible(user)))
        if not row:
            raise HTTPException(404, "Trace not found")
        rows = session.scalars(
            select(TraceSpan)
            .where(TraceSpan.trace_id == row.id)
            .order_by(TraceSpan.started_at, TraceSpan.id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        return {
            "id": row.id,
            "run_id": row.run_id,
            "operation": row.operation,
            "created_at": row.created_at,
            "items": [
                {
                    "id": r.id,
                    "parent_id": r.parent_id,
                    "service": r.service,
                    "operation": r.operation,
                    "status": r.status,
                    "error_code": r.error_code,
                    "details": r.details,
                    "started_at": r.started_at,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "offset": offset,
            "notice": "仅记录执行元数据；缺少步骤不代表未执行。等待与恢复分多次请求记录，可按流程编号查看。",
        }
