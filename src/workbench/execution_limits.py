"""Per-active-continuation budgets; waiting for a person consumes no clock."""

import time

from workbench.mcp_gateway import ToolFailure


class ExecutionLimits:
    def __init__(self, settings, *, clock=time.monotonic, reserve_tool_hook=None):
        self.clock = clock
        self.deadline = clock() + settings.agent_active_seconds
        self.limit = settings.agent_tool_limit
        self.attempts = 0
        self.reserve_tool_hook = reserve_tool_hook

    def check(self):
        if self.clock() >= self.deadline:
            raise ToolFailure("active_time_limit")

    def reserve_tool(self):
        self.check()
        if self.attempts >= self.limit:
            raise ToolFailure("tool_call_limit")
        if self.reserve_tool_hook:
            self.reserve_tool_hook()
        self.attempts += 1

    def remaining(self):
        self.check()
        return self.deadline - self.clock()


async def call_bounded(
    gateway, name, arguments, token, limits, settings, on_retry, on_attempt=None
):
    import asyncio

    from workbench.tool_contracts import CONTRACTS

    retries = settings.mcp_read_retries if CONTRACTS[name][3] else 0
    for retry in range(retries + 1):
        limits.reserve_tool()
        if on_attempt:
            on_attempt()
        try:
            return await asyncio.wait_for(
                gateway.call(name, arguments, token),
                timeout=min(settings.mcp_timeout_seconds, limits.remaining()),
            )
        except (ToolFailure, TimeoutError) as exc:
            failure = exc if isinstance(exc, ToolFailure) else ToolFailure("timeout")
            limits.check()
            if failure.code not in {"timeout", "unavailable"} or retry >= retries:
                raise failure
            delay = settings.mcp_retry_delay_seconds * (2**retry)
            if limits.remaining() <= delay:
                raise ToolFailure("active_time_limit") from failure
            on_retry(name, retry + 1, failure.code)
            await asyncio.sleep(delay)
