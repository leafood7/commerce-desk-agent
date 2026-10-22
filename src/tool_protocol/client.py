"""MCP客户端连接和LangChain适配，保持原有字符串业务响应契约。"""
import json
import logging
import math
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.sessions import create_session
from langchain_mcp_adapters.tools import load_mcp_tools
from tool_protocol.config import readonly_environment

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TOOLS = {"get_order_status", "search_knowledge_base"}
logger = logging.getLogger(__name__)


def server_connection(overrides=None):
    """只传递只读业务配置与本地模型缓存设置，不传模型接口密钥。"""
    env = readonly_environment()
    timeout = float(env.get("SUPPORTPILOT_MCP_TIMEOUT", "60"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("SUPPORTPILOT_MCP_TIMEOUT必须为正的有限秒数。")
    connection = {"transport": "stdio", "command": sys.executable,
                  "args": ["-m", "tool_protocol.server"], "cwd": str(ROOT), "env": env,
                  "session_kwargs": {"read_timeout_seconds": timedelta(seconds=timeout)}}
    if overrides:
        connection.update(overrides)
    return connection


def text_result(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list) and len(value) == 1 and value[0].get("type") == "text":
        return value[0]["text"]
    raise ValueError("MCP只读工具响应不是约定的单一文本块。")


@asynccontextmanager
async def readonly_mcp_tools(connection=None):
    """在同一async任务内进入/退出；会话存活期间可并发调用、复用模型缓存。"""
    active = {"open": True}
    async with create_session(connection or server_connection()) as session:
        await session.initialize()
        remote = await load_mcp_tools(session, handle_tool_errors=False)
        if {t.name for t in remote} != EXPECTED_TOOLS:
            raise ValueError("MCP Server工具清单与只读契约不一致。")

        def normalize(adapted):
            async def invoke(config: RunnableConfig, **kwargs):
                try:
                    if not active["open"]:
                        raise RuntimeError("MCP session closed")
                    value = text_result(await adapted.ainvoke(kwargs, config=config))
                    if adapted.name == "search_knowledge_base":
                        payload = json.loads(value)
                        if not isinstance(payload, dict) or payload.get("status") not in {"found", "no_evidence", "error"}:
                            raise ValueError("政策响应状态无效。")
                        if payload["status"] in {"found", "no_evidence"} and not isinstance(payload.get("matches"), list):
                            raise ValueError("政策响应缺少matches。")
                    return value
                except Exception:
                    logger.exception("MCP readonly tool failed: %s", adapted.name)
                    return json.dumps({"status": "error", "message": "MCP工具暂不可用，无法确认。请检查本地工具服务。", "matches": []}, ensure_ascii=False)
            return StructuredTool(name=adapted.name, description=adapted.description,
                                  args_schema=adapted.args_schema, coroutine=invoke,
                                  metadata={"transport": "mcp", "server": "commerce-desk-readonly"})

        try:
            yield [normalize(tool) for tool in remote]
        finally:
            active["open"] = False
