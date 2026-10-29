import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

import workbench.profile_models  # noqa: F401
import workbench.quota_models  # noqa: F401
import workbench.return_models  # noqa: F401
from workbench.aftersales import WAITING, continue_flow, execute_flow
from workbench.aftersales_schemas import FlowResume
from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.domain import (
    audit,
    create_shipping,
    decide_shipping,
    get_conversation,
    get_order,
    order_view,
    shipping_view,
    user_view,
)
from workbench.job_models import RunJob  # noqa: F401
from workbench.models import (
    AuthSession,
    Conversation,
    Message,
    Order,
    OrderItem,
    Run,
    RunEvent,
    ShippingRequest,
    User,
    now,
)
from workbench.runs import (
    TERMINAL,
    cancel_run,
    create_run,
    execute_lookup,
    owned_run,
    recover_interrupted,
    run_view,
)
from workbench.schemas import (
    ConversationInput,
    DecisionInput,
    LoginInput,
    RunInput,
    ShippingInput,
    UserInput,
)
from workbench.security import authenticate, digest, login, password_hash

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def create_app(settings=None, *, gateway=None):
    settings = settings or WorkbenchSettings()
    database = Database(settings)
    from workbench.mcp_gateway import MCPGateway

    gateway = gateway or MCPGateway(settings)

    @asynccontextmanager
    async def lifespan(application):
        database.check_schema()
        recover_interrupted(database)
        yield
        database.close()

    application = FastAPI(title="CommerceDesk Workbench", version="0.2.0", lifespan=lifespan)
    application.state.database = database
    application.state.settings = settings
    application.state.gateway = gateway
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"],
        expose_headers=["X-Request-ID"],
    )

    @application.middleware("http")
    async def request_log(request, call_next):
        request_id, started = str(uuid4()), time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            1000 * (time.perf_counter() - started),
        )
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default errors include input values; credentials must not be echoed.
        errors = [
            {k: value for k, value in error.items() if k in {"loc", "msg", "type"}}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errors}))

    def session():
        with database.sessions() as connection:
            yield connection

    def actor(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        connection=Depends(session),
    ):
        if not credentials:
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        return authenticate(connection, credentials.credentials)

    def require(*roles):
        def permitted(user=Depends(actor)):
            if user.role not in roles:
                raise HTTPException(403, "Role is not permitted")
            return user

        return permitted

    def idempotency_key(value: str | None = Header(default=None, alias="Idempotency-Key")):
        if value is not None and (
            not 1 <= len(value) <= 128
            or not value.isascii()
            or any(ord(c) < 33 or ord(c) > 126 for c in value)
        ):
            raise HTTPException(422, "Invalid Idempotency-Key")
        return value

    @application.get("/health")
    def health():
        return {"status": "ok", "application": "workbench"}

    @application.get("/ready")
    def ready():
        try:
            database.check_schema()
        except Exception:
            raise HTTPException(503, "Business database is not ready") from None
        return {"status": "ready"}

    @application.get("/api/v1/runtime/profile")
    def runtime_profile(user=Depends(actor)):
        # Explicit allowlist: no endpoints, credentials, database URLs or cross-user usage.
        return {
            "router": {
                "mode": settings.agent_router_mode,
                "model": settings.agent_router_model
                if settings.agent_router_mode == "model"
                else None,
                "temperature": 0,
                "max_output_tokens": 256,
                "automatic_retries": 0,
            },
            "dispatch": settings.run_dispatch_mode,
            "admission": {
                "owner_active_runs": settings.queue_owner_limit,
                "global_active_runs": settings.queue_global_limit,
            },
            "cumulative": {
                "tool_attempts": settings.run_tool_limit,
                "model_tokens": settings.run_model_token_limit,
                "model_attempts": 1,
            },
            "execution": {
                "active_seconds": settings.agent_active_seconds,
                "tool_attempts": settings.agent_tool_limit,
                "read_retries": settings.mcp_read_retries,
                "write_retries": 0,
            },
            "retrieval": {
                "context_chars": settings.context_chars,
                "context_mode": settings.retrieval_context_mode,
            },
        }

    @application.get("/api/v1/runtime/queue")
    def own_queue(user=Depends(actor), connection=Depends(session)):
        # Scope by both organization and owner; this is not a global operator dashboard.
        scope = (Run.organization_id == user.organization_id, Run.owner_id == user.id)
        counts = {
            key: 0 for key in ("queued", "running", "finished", "failed", "interrupted", "skipped")
        }
        for status, count in connection.execute(
            select(RunJob.status, func.count())
            .join(Run, RunJob.run_id == Run.id)
            .where(*scope)
            .group_by(RunJob.status)
        ):
            if status in counts:
                counts[status] = count
        oldest = connection.scalar(
            select(func.min(RunJob.created_at))
            .join(Run, RunJob.run_id == Run.id)
            .where(*scope, RunJob.status == "queued")
        )
        failures = connection.execute(
            select(RunJob.run_id, RunJob.error_code, RunJob.updated_at)
            .join(Run, RunJob.run_id == Run.id)
            .where(*scope, RunJob.status.in_(["failed", "interrupted"]))
            .order_by(RunJob.updated_at.desc(), RunJob.id.desc())
            .limit(5)
        ).all()
        observed = now()
        return {
            "dispatch": settings.run_dispatch_mode,
            "scope": "current_user",
            "observed_at": observed,
            "counts": counts,
            "oldest_queued_seconds": max(0, int((observed - oldest).total_seconds()))
            if oldest
            else None,
            "recent_failures": [
                {"run_id": run_id, "error_code": code, "updated_at": timestamp}
                for run_id, code, timestamp in failures
            ],
        }

    @application.post("/api/v1/auth/login")
    def sign_in(
        body: LoginInput, request: Request, response: Response, connection=Depends(session)
    ):
        token, user = login(
            connection,
            body.organization,
            body.email,
            body.password.get_secret_value(),
            request.client.host if request.client else "unknown",
            settings.session_minutes,
        )
        response.headers["Cache-Control"] = "no-store"
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": settings.session_minutes * 60,
            "user": user_view(user),
        }

    @application.get("/api/v1/auth/me")
    def me(user=Depends(actor)):
        return user_view(user)

    @application.post("/api/v1/auth/logout", status_code=204)
    def logout(user=Depends(actor), credentials=Depends(bearer), connection=Depends(session)):
        connection.execute(
            delete(AuthSession).where(AuthSession.token_hash == digest(credentials.credentials))
        )
        connection.commit()
        return Response(status_code=204)

    @application.post("/api/v1/users", status_code=201)
    def add_user(body: UserInput, user=Depends(require("admin")), connection=Depends(session)):
        new_user = User(
            organization_id=user.organization_id,
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            password_hash=password_hash(body.password.get_secret_value()),
        )
        connection.add(new_user)
        try:
            connection.flush()
            audit(connection, user, "user.created", new_user.id, role=body.role)
            connection.commit()
        except IntegrityError:
            connection.rollback()
            raise HTTPException(409, "User already exists") from None
        return user_view(new_user)

    @application.post("/api/v1/users/{identifier}/disable")
    def disable_user(identifier: str, user=Depends(require("admin")), connection=Depends(session)):
        target = connection.scalar(
            select(User).where(User.id == identifier, User.organization_id == user.organization_id)
        )
        if target is None:
            raise HTTPException(404, "User not found")
        if target.id == user.id:
            raise HTTPException(409, "Cannot disable your own administrative account")
        target.active = False
        audit(connection, user, "user.disabled", target.id)
        connection.commit()
        return user_view(target)

    @application.get("/api/v1/orders")
    def orders(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        user=Depends(actor),
        connection=Depends(session),
    ):
        rows = connection.scalars(
            select(Order)
            .where(Order.organization_id == user.organization_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        grouped = {row.id: [] for row in rows}
        if grouped:
            for item in connection.scalars(
                select(OrderItem)
                .where(
                    OrderItem.organization_id == user.organization_id,
                    OrderItem.order_id.in_(grouped),
                )
                .order_by(OrderItem.sku)
            ):
                grouped[item.order_id].append(item)
        return {
            "items": [order_view(connection, row, grouped[row.id]) for row in rows],
            "limit": limit,
            "offset": offset,
        }

    @application.get("/api/v1/orders/{number}")
    def order(number: str, user=Depends(actor), connection=Depends(session)):
        return order_view(connection, get_order(connection, user, number))

    @application.get("/api/v1/orders/{number}/tickets")
    def tickets(number: str, user=Depends(actor), connection=Depends(session)):
        from workbench.models import ShippingTicket

        record = get_order(connection, user, number)
        rows = connection.scalars(
            select(ShippingTicket)
            .where(
                ShippingTicket.organization_id == user.organization_id,
                ShippingTicket.order_id == record.id,
            )
            .order_by(ShippingTicket.created_at.desc(), ShippingTicket.id.desc())
            .limit(100)
        )
        return {
            "items": [
                {
                    "id": row.id,
                    "request_id": row.request_id,
                    "kind": row.kind,
                    "status": row.status,
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        }

    @application.post("/api/v1/conversations", status_code=201)
    def new_conversation(body: ConversationInput, user=Depends(actor), connection=Depends(session)):
        row = Conversation(organization_id=user.organization_id, owner_id=user.id, title=body.title)
        connection.add(row)
        connection.commit()
        return {"id": row.id, "title": row.title}

    @application.get("/api/v1/conversations")
    def conversations(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        user=Depends(actor),
        connection=Depends(session),
    ):
        rows = connection.scalars(
            select(Conversation)
            .where(
                Conversation.organization_id == user.organization_id,
                Conversation.owner_id == user.id,
            )
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [
                {"id": row.id, "title": row.title, "updated_at": row.updated_at} for row in rows
            ],
            "limit": limit,
            "offset": offset,
        }

    @application.get("/api/v1/conversations/{identifier}/messages")
    def messages(
        identifier: str,
        limit: int = Query(100, ge=1, le=200),
        before: str | None = Query(default=None, min_length=1, max_length=36),
        user=Depends(actor),
        connection=Depends(session),
    ):
        get_conversation(connection, user, identifier)
        query = select(Message).where(
            Message.conversation_id == identifier, Message.organization_id == user.organization_id
        )
        if before:
            cursor = connection.scalar(
                select(Message).where(
                    Message.id == before,
                    Message.conversation_id == identifier,
                    Message.organization_id == user.organization_id,
                )
            )
            if not cursor:
                raise HTTPException(422, "Cursor does not belong to this conversation")
            query = query.where(
                or_(
                    Message.created_at < cursor.created_at,
                    and_(Message.created_at == cursor.created_at, Message.id < cursor.id),
                )
            )
        rows = connection.scalars(
            query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
        ).all()
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        return {
            "items": [
                {
                    "id": row.id,
                    "role": row.role,
                    "content": row.content,
                    "run_id": row.run_id,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            "has_more": more,
            "next_before": rows[0].id if more else None,
        }

    @application.get("/api/v1/conversations/{identifier}/runs")
    def conversation_runs(
        identifier: str,
        limit: int = Query(20, ge=1, le=100),
        user=Depends(actor),
        connection=Depends(session),
    ):
        get_conversation(connection, user, identifier)
        rows = connection.scalars(
            select(Run)
            .where(
                Run.conversation_id == identifier,
                Run.organization_id == user.organization_id,
                Run.owner_id == user.id,
            )
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(limit)
        )
        return {"items": [run_view(row) for row in rows]}

    @application.get("/api/v1/aftersales/runs")
    def recent_aftersales(
        workflow: str = Query("shipping", pattern="^(shipping|returns)$"),
        user=Depends(actor),
        connection=Depends(session),
    ):
        rows = connection.scalars(
            select(Run)
            .where(
                Run.kind == "aftersales",
                Run.owner_id == user.id,
                Run.organization_id == user.organization_id,
                Run.input["return_policy_id"].as_string().is_not(None)
                if workflow == "returns"
                else Run.input["return_policy_id"].as_string().is_(None),
            )
            .order_by(Run.created_at.desc())
            .limit(20)
        ).all()
        return {"items": [run_view(row) for row in rows]}

    @application.post("/api/v1/runs", status_code=202)
    def start_run(
        body: RunInput,
        background: BackgroundTasks,
        user=Depends(actor),
        key=Depends(idempotency_key),
        credentials=Depends(bearer),
        connection=Depends(session),
    ):
        from workbench.tool_contracts import mint_grant

        if body.kind == "aftersales":
            from workbench.aftersales_graph import classify

            if classify(body.query) == "shipping" and user.role != "support":
                raise HTTPException(403, "Only support may start a shipping workflow")
            if body.return_policy_id and user.role != "support":
                raise HTTPException(403, "Only support may start a return workflow")
        from workbench.run_worker import enqueue

        queued = settings.run_dispatch_mode == "worker"
        delegated_token = (
            None if queued else mint_grant(connection, credentials.credentials, settings)[0]
        )
        run, created = create_run(
            connection,
            user,
            body,
            key,
            settings=settings,
            enqueue_job=(
                lambda session, row: enqueue(session, row, digest(credentials.credentials))
            )
            if queued
            else None,
        )
        if created and not queued:
            background.add_task(
                execute_flow if body.kind == "aftersales" else execute_lookup,
                database,
                run.id,
                gateway,
                delegated_token,
            )
        return run_view(run)

    @application.post("/api/v1/runs/{identifier}/resume", status_code=202)
    def resume_flow(
        identifier: str,
        body: FlowResume,
        background: BackgroundTasks,
        user=Depends(actor),
        key=Depends(idempotency_key),
        credentials=Depends(bearer),
        connection=Depends(session),
    ):
        from workbench.run_worker import enqueue
        from workbench.tool_contracts import mint_grant

        queued = settings.run_dispatch_mode == "worker"
        delegated_token = (
            None if queued else mint_grant(connection, credentials.credentials, settings)[0]
        )
        run, created = continue_flow(
            connection,
            user,
            identifier,
            body,
            key,
            settings=settings,
            enqueue_job=(
                lambda session, row: enqueue(session, row, digest(credentials.credentials), key)
            )
            if queued
            else None,
        )
        if created and not queued:
            background.add_task(execute_flow, database, run.id, gateway, delegated_token, key)
        return run_view(run)

    @application.get("/api/v1/runs/{identifier}")
    def run(identifier: str, user=Depends(actor), connection=Depends(session)):
        return run_view(owned_run(connection, user, identifier))

    @application.get("/api/v1/runs/{identifier}/business-status")
    def business_status(identifier: str, user=Depends(actor), connection=Depends(session)):
        record = owned_run(connection, user, identifier)
        if record.kind != "aftersales":
            raise HTTPException(409, "Business reconciliation requires an after-sales run")
        if record.input.get("return_policy_id"):
            from workbench.return_models import ReturnRequest
            from workbench.returns import request_view

            request = connection.scalar(
                select(ReturnRequest).where(
                    ReturnRequest.organization_id == user.organization_id,
                    ReturnRequest.created_by == user.id,
                    ReturnRequest.idempotency_key == "aftersales:" + record.id,
                )
            )
            return {
                "run_id": record.id,
                "lookup_status": "found" if request else "not_observed",
                "request": request_view(connection, user, request) if request else None,
                "run_status": record.status,
            }
        request = connection.scalar(
            select(ShippingRequest).where(
                ShippingRequest.organization_id == user.organization_id,
                ShippingRequest.created_by == user.id,
                ShippingRequest.idempotency_key == "aftersales:" + record.id,
            )
        )
        return {
            "run_id": record.id,
            "lookup_status": "found" if request else "not_observed",
            "request": shipping_view(connection, request) if request else None,
            "run_status": record.status,
        }

    @application.get("/api/v1/runs/{identifier}/configuration")
    def configuration(identifier: str, user=Depends(actor), connection=Depends(session)):
        from workbench.profiles import run_profile_view

        return run_profile_view(connection, owned_run(connection, user, identifier))

    @application.get("/api/v1/runs/{identifier}/usage")
    def run_usage(identifier: str, user=Depends(actor), connection=Depends(session)):
        from workbench.quotas import ensure_usage, usage_view

        connection.commit()  # End the authentication RR snapshot before locking usage.
        record = owned_run(connection, user, identifier, lock=True)
        usage = ensure_usage(connection, record, settings)
        result = usage_view(usage)
        connection.commit()
        return result

    @application.post("/api/v1/runs/{identifier}/cancel")
    def cancel(identifier: str, user=Depends(actor), connection=Depends(session)):
        return run_view(cancel_run(connection, user, identifier))

    @application.get("/api/v1/runs/{identifier}/events")
    def stream_events(
        identifier: str,
        after: int = Query(0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        credentials=Depends(bearer),
        user=Depends(actor),
        connection=Depends(session),
    ):
        owned_run(connection, user, identifier)
        cursor = after
        if last_event_id is not None:
            if not last_event_id.isdecimal() or len(last_event_id) > 18:
                raise HTTPException(422, "Invalid Last-Event-ID")
            cursor = max(cursor, int(last_event_id))
        if cursor and not connection.scalar(
            select(RunEvent.id).where(RunEvent.run_id == identifier, RunEvent.id == cursor)
        ):
            raise HTTPException(422, "Cursor does not belong to this run")
        token = credentials.credentials

        def batch(current):
            with database.sessions() as read_session:
                current_user = authenticate(read_session, token)
                current_run = owned_run(read_session, current_user, identifier)
                rows = read_session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == identifier, RunEvent.id > current)
                    .order_by(RunEvent.id)
                    .limit(100)
                ).all()
                return jsonable_encoder(
                    [
                        {
                            "id": row.id,
                            "run_id": row.run_id,
                            "type": row.type,
                            "timestamp": row.created_at,
                            "payload": row.payload,
                        }
                        for row in rows
                    ]
                ), current_run.status

        async def generate():
            position, empty = cursor, 0
            while True:
                try:
                    rows, state = await asyncio.to_thread(batch, position)
                except HTTPException:
                    yield "event: authentication_required\ndata: {}\n\n"
                    return
                for row in rows:
                    position = row["id"]
                    yield f"id: {position}\nevent: {row['type']}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if state in (TERMINAL | WAITING) and len(rows) < 100:
                    return
                empty = 0 if rows else empty + 1
                if empty and empty % 40 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/api/v1/shipping/requests", status_code=201)
    def shipping(
        body: ShippingInput,
        user=Depends(require("support")),
        key=Depends(idempotency_key),
        connection=Depends(session),
    ):
        return shipping_view(connection, create_shipping(connection, user, body, key))

    @application.get("/api/v1/shipping/requests")
    def shipping_requests(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        status: str | None = None,
        user=Depends(actor),
        connection=Depends(session),
    ):
        statement = select(ShippingRequest).where(
            ShippingRequest.organization_id == user.organization_id
        )
        if status:
            statement = statement.where(ShippingRequest.status == status)
        rows = connection.scalars(
            statement.order_by(ShippingRequest.created_at.desc(), ShippingRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [shipping_view(connection, row) for row in rows],
            "limit": limit,
            "offset": offset,
        }

    @application.post("/api/v1/shipping/requests/{identifier}/decision")
    def decision(
        identifier: str,
        body: DecisionInput,
        user=Depends(require("approver")),
        connection=Depends(session),
    ):
        return shipping_view(connection, decide_shipping(connection, user, identifier, body))

    from workbench.knowledge_api import attach_knowledge_routes

    attach_knowledge_routes(
        application, database, settings, session, actor, require, bearer, gateway
    )
    from workbench.profile_api import attach_profile_routes

    attach_profile_routes(application, settings, session, actor, require)
    from workbench.return_api import attach_return_routes

    attach_return_routes(application, session, actor, require, idempotency_key)
    from workbench.quality_api import attach_quality_routes

    attach_quality_routes(application, session, actor, require, idempotency_key)
    return application


app = create_app()
