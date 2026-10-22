"""本地stdio MCP Server：仅暴露两项只读业务工具，stdout仅用于协议。"""
import logging
import os
from functools import partial
from anyio import to_thread
from typing import Annotated
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, StrictStr
from tool_protocol.config import readonly_environment
from business.readonly_tools import ORDER_DESCRIPTION, POLICY_DESCRIPTION, order_result, policy_result


def build_server(lookup=None, search=None):
    server = FastMCP("CommerceDesk Readonly", log_level="WARNING")
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(description=ORDER_DESCRIPTION, annotations=readonly, structured_output=False)
    async def get_order_status(order_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]) -> str:
        return await to_thread.run_sync(partial(order_result, order_id, lookup=lookup))

    @server.tool(description=POLICY_DESCRIPTION, annotations=readonly, structured_output=False)
    async def search_knowledge_base(query: Annotated[StrictStr, Field(min_length=1, max_length=1200)]) -> str:
        if not query.strip():
            raise ValueError("政策查询不能为空。")
        return await to_thread.run_sync(partial(policy_result, query, search=search))

    return server


def prepare_runtime():
    """Windows上在协议线程启动前初始化数值DLL和模型缓存。"""
    from business.knowledge import embedding_function, is_index_ready, search_policies
    # 显式在主线程完成数值依赖导入，避免首次工具调用才从工作线程加载DLL。
    from sentence_transformers import CrossEncoder  # noqa: F401
    if is_index_ready():
        try:
            embedding_function()
            if os.getenv("SUPPORTPILOT_RETRIEVAL_STRATEGY", "vector") == "hybrid_rerank":
                from business.rag_retrieval import reranker
                reranker()
        except Exception:
            logging.getLogger(__name__).exception("Policy model warmup failed; policy tool will report errors.")
    return search_policies


def main():
    for key, value in readonly_environment().items():
        os.environ.setdefault(key, value)
    logging.basicConfig(level=logging.WARNING)
    search = prepare_runtime()
    build_server(search=search).run(transport="stdio")


if __name__ == "__main__":
    main()
