import pytest

from workbench.config import WorkbenchSettings
from workbench.execution_limits import ExecutionLimits
from workbench.mcp_gateway import ToolFailure


def test_active_budget_does_not_reset_when_checked_and_counts_attempts():
    current = [10.0]
    limits = ExecutionLimits(
        WorkbenchSettings(agent_active_seconds=5, agent_tool_limit=2), clock=lambda: current[0]
    )
    limits.reserve_tool()
    limits.reserve_tool()
    with pytest.raises(ToolFailure, match="tool_call_limit"):
        limits.reserve_tool()
    assert limits.attempts == 2
    current[0] = 15.0
    with pytest.raises(ToolFailure, match="active_time_limit"):
        limits.check()


def test_new_continuation_has_new_clock_and_settings_are_bounded():
    current = [0.0]
    settings = WorkbenchSettings(agent_active_seconds=5)
    first = ExecutionLimits(settings, clock=lambda: current[0])
    current[0] = 100.0
    with pytest.raises(ToolFailure):
        first.check()
    resumed = ExecutionLimits(settings, clock=lambda: current[0])
    assert resumed.remaining() == 5
    for values in ({"agent_tool_limit": 0}, {"agent_active_seconds": 0}, {"mcp_read_retries": 3}):
        with pytest.raises(ValueError):
            WorkbenchSettings(**values)


def test_read_retries_are_bounded_but_writes_and_permissions_are_not_retried():
    import asyncio
    from types import SimpleNamespace

    settings = WorkbenchSettings(mcp_retry_delay_seconds=0)
    calls = []
    events = []

    async def failing(name, arguments, token):
        calls.append(name)
        raise ToolFailure("unavailable")

    gateway = SimpleNamespace(call=failing)
    from workbench.execution_limits import call_bounded

    for name, expected in (("get_order_status", 2), ("request_shipping", 1)):
        calls.clear()
        with pytest.raises(ToolFailure, match="unavailable"):
            asyncio.run(
                call_bounded(
                    gateway,
                    name,
                    {},
                    "private-test-token",
                    ExecutionLimits(settings),
                    settings,
                    lambda *args: events.append(args),
                )
            )
        assert len(calls) == expected
    assert events == [("get_order_status", 1, "unavailable")]

    async def denied(*args):
        calls.append("denied")
        raise ToolFailure("forbidden")

    calls.clear()
    with pytest.raises(ToolFailure, match="forbidden"):
        asyncio.run(
            call_bounded(
                SimpleNamespace(call=denied),
                "get_order_status",
                {},
                "",
                ExecutionLimits(settings),
                settings,
                lambda *args: events.append(args),
            )
        )
    assert calls == ["denied"]


def test_slow_tool_is_cancelled_by_active_deadline_without_retry():
    import asyncio
    from types import SimpleNamespace

    from workbench.execution_limits import call_bounded

    settings = WorkbenchSettings(mcp_retry_delay_seconds=0)
    limits = ExecutionLimits(settings)
    limits.deadline = limits.clock() + 0.02
    cancelled = []

    async def slow(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    with pytest.raises(ToolFailure, match="active_time_limit"):
        asyncio.run(
            call_bounded(
                SimpleNamespace(call=slow),
                "get_order_status",
                {},
                "",
                limits,
                settings,
                lambda *args: pytest.fail("No retry past deadline"),
            )
        )
    assert cancelled == [True]


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("schema", "invalid_response"),
        ("connect", "unavailable"),
        ("forbidden", "forbidden"),
        ("bad_request", "tool_failed"),
    ],
)
def test_gateway_distinguishes_transport_failure_from_schema_and_client_errors(
    monkeypatch, kind, expected
):
    import asyncio
    from contextlib import asynccontextmanager

    import httpx

    from workbench.mcp_gateway import MCPGateway

    @asynccontextmanager
    async def broken(*args, **kwargs):
        if kind == "schema":
            raise ValueError("malformed protocol")
        if kind == "connect":
            raise httpx.ConnectError("transport unavailable")
        status = 403 if kind == "forbidden" else 400
        response = httpx.Response(status, request=httpx.Request("POST", "http://test.invalid/mcp"))
        raise httpx.HTTPStatusError("request rejected", request=response.request, response=response)
        yield

    monkeypatch.setattr("workbench.mcp_gateway.streamable_http_client", broken)
    with pytest.raises(ToolFailure, match=expected):
        asyncio.run(
            MCPGateway(WorkbenchSettings()).call(
                "get_order_status", {"order_number": "A1001"}, "test-token"
            )
        )
