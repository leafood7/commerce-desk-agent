"""服务启动时建立MCP会话，服务关闭时释放；审批工具继续在本地执行。"""
import sys
from agents.lazy_agent import LazyLoadingAgent
from agents.supportpilot_agent import build_supportpilot_graph, request_shipping
from tool_protocol.client import readonly_mcp_tools


class MCPSupportPilotAgent(LazyLoadingAgent):
    def __init__(self, connection=None):
        super().__init__()
        self.connection = connection
        self._context = None

    async def load(self):
        if self._loaded:
            return
        context = readonly_mcp_tools(self.connection)
        tools = await context.__aenter__()
        try:
            self._graph = build_supportpilot_graph(tools=[*tools, request_shipping])
        except BaseException:
            await context.__aexit__(*sys.exc_info())
            raise
        self._context = context
        self._loaded = True

    async def aclose(self):
        if self._context is not None:
            context, self._context = self._context, None
            self._loaded = False
            self._graph = None
            await context.__aexit__(None, None, None)
