"""One bounded HTTP call context per invocation; never silently call local tools."""

import asyncio
import json
from datetime import timedelta

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from workbench.tool_contracts import CONTRACTS


class ToolFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class MCPGateway:
    def __init__(self, settings):
        self.settings = settings

    async def call(self, name, arguments, token):
        if name not in CONTRACTS:
            raise ToolFailure("unknown_tool")
        body = CONTRACTS[name][0].model_validate(arguments)
        timeout = self.settings.mcp_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    headers={"Authorization": "Bearer " + token},
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with streamable_http_client(
                        self.settings.mcp_url, http_client=client
                    ) as (read, write, _):
                        async with ClientSession(
                            read, write, read_timeout_seconds=timedelta(seconds=timeout)
                        ) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            result = None
                            if name in {tool.name for tool in tools.tools}:
                                result = await session.call_tool(name, body.model_dump(mode="json"))
            if result is None:
                raise ToolFailure("forbidden")
            if len(result.content) != 1 or result.content[0].type != "text":
                raise ToolFailure("invalid_response")
            payload = json.loads(result.content[0].text)
            if not isinstance(payload, dict) or payload.get("status") not in {
                "found",
                "no_evidence",
                "success",
                "error",
            }:
                raise ToolFailure("invalid_response")
            if result.isError or payload["status"] == "error":
                raise ToolFailure(payload.get("error", {}).get("code", "tool_failed"))
            if name == "get_order_status":
                data = payload.get("order", {})
                if data.get("number") != body.order_number or not isinstance(
                    data.get("items"), list
                ):
                    raise ToolFailure("invalid_response")
            if name == "search_knowledge_base" and not isinstance(payload.get("matches"), list):
                raise ToolFailure("invalid_response")
            if name == "evaluate_return":
                preview = payload.get("preview", {})
                application = preview.get("application", {})
                if (
                    application != body.model_dump(mode="json")
                    or preview.get("eligibility")
                    not in {"eligible", "blocked", "needs_information"}
                    or not isinstance(preview.get("evidence"), dict)
                ):
                    raise ToolFailure("invalid_response")
            if name in {
                "create_return_draft",
                "get_return_status",
                "confirm_return_application",
                "submit_simulated_return",
            }:
                request = payload.get("request", {})
                if (
                    not isinstance(request.get("id"), str)
                    or request.get("actual_refund_executed") is not False
                    or request.get("external_mode") != "simulation"
                ):
                    raise ToolFailure("invalid_response")
                if name != "create_return_draft" and request["id"] != body.request_id:
                    raise ToolFailure("invalid_response")
                if name == "create_return_draft" and any(
                    request.get(field) != getattr(body, field)
                    for field in (
                        "order_number",
                        "item_id",
                        "policy_id",
                        "quantity",
                        "reason",
                        "item_condition",
                    )
                ):
                    raise ToolFailure("invalid_response")
            return payload
        except ToolFailure:
            raise
        except TimeoutError as exc:
            raise ToolFailure("timeout") from exc
        except Exception as exc:

            def leaves(error):
                if isinstance(error, BaseExceptionGroup):
                    return [leaf for child in error.exceptions for leaf in leaves(child)]
                return [error]

            failures = leaves(exc)
            for failure in failures:
                if isinstance(failure, ToolFailure):
                    raise failure from exc
                if isinstance(failure, (TimeoutError, httpx.TimeoutException)):
                    raise ToolFailure("timeout") from exc
                if isinstance(failure, httpx.HTTPStatusError):
                    if failure.response.status_code in {401, 403}:
                        raise ToolFailure(
                            "unauthorized" if failure.response.status_code == 401 else "forbidden"
                        ) from exc
                    raise ToolFailure(
                        "unavailable"
                        if failure.response.status_code in {408, 429, 500, 502, 503, 504}
                        else "tool_failed"
                    ) from exc
            if any(isinstance(item, ValueError) for item in failures):
                raise ToolFailure("invalid_response") from exc
            if any(
                isinstance(
                    item,
                    (httpx.TransportError, OSError, anyio.EndOfStream, anyio.BrokenResourceError),
                )
                for item in failures
            ):
                raise ToolFailure("unavailable") from exc
            raise ToolFailure("tool_failed") from exc
