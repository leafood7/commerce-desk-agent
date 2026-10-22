"""AG-UI protocol endpoint for the agent service.

Exposes any agent in the service over the AG-UI protocol (https://docs.ag-ui.com)
so it can be used with AG-UI compatible frontends like CopilotKit. The
LangGraph -> AG-UI event translation is handled by the official `ag-ui-langgraph`
package; this module only wires it into the service's agent registry, auth, and
tracing.

See docs/AGUI.md for usage, including how to connect a client.
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from ag_ui.core import EventType, RunAgentInput
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]

from agents import DEFAULT_AGENT, AgentGraph, get_agent
from core import settings
from service.utils import ensure_model_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agui")

# 这些字段由协议（thread_id 来自 RunAgentInput）或检查点组件管理，
# 因此客户端不能通过 forwardedProps.configurable 覆盖它们
RESERVED_CONFIGURABLE_KEYS = {"thread_id", "checkpoint_id", "checkpoint_ns"}


def _base_config(input_data: RunAgentInput, agent_id: str) -> RunnableConfig:
    """Build the base RunnableConfig for an AG-UI run.

    Clients can pass configurable values (e.g. `model`, `user_id`, or custom agent
    config) in `forwardedProps.configurable` - the AG-UI equivalent of the vanilla
    API's `model` / `user_id` / `agent_config` fields. `thread_id` is taken from
    the AG-UI input by the `ag-ui-langgraph` package itself.
    """
    forwarded: dict[str, Any] = input_data.forwarded_props or {}
    configurable = forwarded.get("configurable") or {}
    if not isinstance(configurable, dict):
        raise HTTPException(status_code=422, detail="forwardedProps.configurable must be an object")
    if overlap := RESERVED_CONFIGURABLE_KEYS & configurable.keys():
        raise HTTPException(
            status_code=422,
            detail=f"forwardedProps.configurable contains reserved keys: {overlap}",
        )

    if (model := configurable.get("model")) is not None:
        ensure_model_available(model)

    callbacks: list[Any] = []
    if settings.LANGFUSE_TRACING:
        callbacks.append(CallbackHandler())

    configurable = dict(configurable)
    user_id = configurable.setdefault("user_id", str(uuid4()))

    return RunnableConfig(
        configurable=configurable,
        # 写入检查点元数据，使 AG-UI 对话也能显示在 /threads 中
        metadata={"user_id": user_id, "agent_id": agent_id},
        callbacks=callbacks,
    )


async def _event_stream(
    agent_id: str,
    graph: AgentGraph,
    input_data: RunAgentInput,
    config: RunnableConfig,
    encoder: EventEncoder,
) -> AsyncGenerator[str, None]:
    # 每个请求创建一个 LangGraphAgent：它保存单次执行状态，创建成本较低
    agent = LangGraphAgent(name=agent_id, graph=graph, config=config)  # type: ignore[arg-type]
    async for event in agent.run(input_data):
        # 不要转发 RAW 原样透传事件；标准 AG-UI 客户端会忽略它们，
        # 而且它们会向调用者暴露服务端内部信息，包括 on_chat_model_start
        # 中的完整提示词；只有当接口由可信中间层调用，且需要全部
        # 事件数据时，才考虑移除此过滤器，
        # 例如使用 AG-UI 事件检查器时
        if event.type == EventType.RAW:
            continue
        yield encoder.encode(event)


@router.post("/run", operation_id="agui_run_default")
@router.post("/{agent_id}/run", operation_id="agui_run")
async def agui_run(
    input_data: RunAgentInput, request: Request, agent_id: str = DEFAULT_AGENT
) -> StreamingResponse:
    """
    Run an agent over the AG-UI protocol, streaming AG-UI events via SSE.

    Point an AG-UI client (e.g. CopilotKit's runtime or HttpAgent) at this endpoint.
    Use the same threadId across runs to continue a conversation - threads are
    persisted in the service's checkpointer and shared with the vanilla API.
    """
    try:
        graph: AgentGraph = get_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    config = _base_config(input_data, agent_id)
    encoder = EventEncoder(accept=request.headers.get("accept", ""))
    return StreamingResponse(
        _event_stream(agent_id, graph, input_data, config, encoder),
        media_type=encoder.get_content_type(),
    )
