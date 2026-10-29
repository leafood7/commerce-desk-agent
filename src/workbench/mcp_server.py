"""Authenticated first-party Streamable HTTP tools; no OAuth server is advertised."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from time import perf_counter

import uvicorn
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from mcp import types
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from workbench import returns
from workbench.config import WorkbenchSettings
from workbench.database import Database
from workbench.domain import create_shipping, get_order, order_view, shipping_view
from workbench.knowledge_models import ToolInvocation
from workbench.models import ShippingRequest
from workbench.retrieval import KnowledgeRetriever
from workbench.return_schemas import ReturnInput
from workbench.tool_contracts import CONTRACTS, verify_grant


class GrantVerifier(TokenVerifier):
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    async def verify_token(self, token):
        def check():
            with self.database.sessions() as session:
                user, scopes = verify_grant(session, token, self.settings)
                return AccessToken(
                    token=token, client_id=user.id, subject=user.id, scopes=sorted(scopes)
                )

        try:
            return await asyncio.to_thread(check)
        except HTTPException:
            return None


def create_mcp_app(settings=None, *, index=None):
    settings = settings or WorkbenchSettings()
    database = Database(settings)
    if index is None:
        from workbench.knowledge_index import ChromaIndex

        index = ChromaIndex(settings)
    retriever = KnowledgeRetriever(database, settings, index)
    server = Server("CommerceDesk Scoped Tools")

    @server.list_tools()
    async def list_tools():
        access = get_access_token()
        scopes = set(access.scopes) if access else set()
        return [
            types.Tool(
                name=name,
                description=description,
                inputSchema=schema.model_json_schema(),
                annotations=types.ToolAnnotations(
                    readOnlyHint=readonly,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
            for name, (schema, scope, description, readonly) in CONTRACTS.items()
            if scope in scopes
        ]

    def execute(name, arguments, token):
        started, actor_id, organization_id = perf_counter(), None, None
        status, error_code = "error", None
        safe_arguments = {}
        try:
            with database.sessions() as session:
                user, scopes = verify_grant(session, token, settings)
                actor_id, organization_id = user.id, user.organization_id
                if name not in CONTRACTS:
                    raise HTTPException(404, "Unknown tool")
                schema, scope, _, _ = CONTRACTS[name]
                if scope not in scopes:
                    raise HTTPException(403, "Tool is not permitted for this role")
                body = schema.model_validate(arguments)
                safe_arguments = body.model_dump(mode="json")
                if name == "confirm_return_application":
                    data = {
                        "status": "success",
                        "request": returns.request_view(
                            session,
                            user,
                            returns.confirm_request(session, user, body.request_id, body),
                        ),
                    }
                elif name == "submit_simulated_return":
                    data = {
                        "status": "success",
                        "request": returns.request_view(
                            session,
                            user,
                            returns.submit_simulated(session, user, body.request_id, body),
                        ),
                    }
                elif name == "evaluate_return":
                    data = {"status": "found", "preview": returns.evaluate(session, user, body)[2]}
                elif name == "create_return_draft":
                    application = ReturnInput.model_validate(
                        body.model_dump(exclude={"idempotency_key"})
                    )
                    data = {
                        "status": "success",
                        "request": returns.request_view(
                            session,
                            user,
                            returns.create_request(
                                session, user, application, body.idempotency_key
                            ),
                        ),
                    }
                elif name == "get_return_status":
                    data = {
                        "status": "found",
                        "request": returns.request_view(
                            session, user, returns.owned_request(session, user, body.request_id)
                        ),
                    }
                elif name == "get_order_status":
                    data = {
                        "status": "found",
                        "order": order_view(session, get_order(session, user, body.order_number)),
                    }
                elif name == "get_shipping_status":
                    request = session.get(ShippingRequest, body.request_id)
                    if request is None or request.organization_id != user.organization_id:
                        raise HTTPException(404, "Request not found")
                    data = {"status": "found", "request": shipping_view(session, request)}
                elif name == "request_shipping":
                    data = {
                        "status": "success",
                        "request": shipping_view(
                            session, create_shipping(session, user, body, body.idempotency_key)
                        ),
                    }
                else:
                    data = retriever.search(user.organization_id, body)
                status = data["status"]
        except ValidationError:
            error_code, data = "invalid_argument", None
        except HTTPException as exc:
            error_code = {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
            }.get(exc.status_code, "invalid_argument")
            data = None
        except Exception:
            logging.getLogger(__name__).exception("MCP tool failed: %s", name)
            error_code, data = "unavailable", None
        if data is None:
            data = {
                "status": "error",
                "error": {
                    "code": error_code,
                    "retryable": error_code == "unavailable",
                    "message": "工具未完成，请核对权限、参数或服务状态。",
                },
            }
        if actor_id:
            try:
                with database.sessions() as session:
                    session.add(
                        ToolInvocation(
                            organization_id=organization_id,
                            actor_id=actor_id,
                            name=name[:80],
                            arguments=safe_arguments,
                            status=status,
                            error_code=error_code,
                            elapsed_ms=int((perf_counter() - started) * 1000),
                        )
                    )
                    session.commit()
            except Exception:
                # Business writes may already be committed. Never invite a duplicate
                # operation by replacing their real outcome with an audit error.
                logging.getLogger(__name__).exception("Tool audit persistence failed: %s", name)
                data["audit_status"] = "unavailable"
        return data

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        access = get_access_token()
        if access is None:
            data = {"status": "error", "error": {"code": "unauthorized", "retryable": False}}
        else:
            data = await asyncio.to_thread(execute, name, arguments, access.token)
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text", text=json.dumps(jsonable_encoder(data), ensure_ascii=False)
                )
            ],
            isError=data["status"] == "error",
        )

    manager = StreamableHTTPSessionManager(
        server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
    verifier = GrantVerifier(database, settings)

    async def http_handler(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    secured = RequireAuthMiddleware(http_handler, required_scopes=["tools:use"])
    secured = AuthContextMiddleware(secured)
    secured = AuthenticationMiddleware(secured, backend=BearerAuthBackend(verifier))

    @asynccontextmanager
    async def lifespan(application):
        database.check_schema()
        async with manager.run():
            yield
        database.close()

    async def health(request):
        return JSONResponse({"status": "ok", "service": "scoped-mcp"})

    application = Starlette(
        routes=[Route("/health", health), Route("/mcp", secured)], lifespan=lifespan
    )
    application.state.database = database
    application.state.retriever = retriever
    return application


def main():
    settings = WorkbenchSettings()
    from workbench.knowledge_index import ChromaIndex

    index = ChromaIndex(settings)
    if settings.mcp_warm_models:
        index.warm()
    uvicorn.run(
        create_mcp_app(settings, index=index), host=settings.mcp_bind_host, port=settings.mcp_port
    )


if __name__ == "__main__":
    main()
