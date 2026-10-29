"""AG-UI protocol endpoint for the agent service.

Exposes any agent in the service over the AG-UI protocol (https://docs.ag-ui.com)
so it can be used with AG-UI compatible frontends like CopilotKit. The
LangGraph -> AG-UI event translation is handled by the official `ag-ui-langgraph`
package; this module only wires it into the service's agent registry, auth, and
tracing.

See docs/AGUI.md for usage, including how to connect a client.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from ag_ui.core import (
    EventType,
    MessagesSnapshotEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.utils import agui_messages_to_langchain, langchain_messages_to_agui
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langfuse.langchain import CallbackHandler  # type: ignore[import-untyped]

from agents import DEFAULT_AGENT, AgentGraph, get_agent
from agents.answer_guard import answer_guard_mode
from core import settings
from service.utils import (
    convert_message_content_to_string,
    ensure_model_available,
    private_answer_event,
    public_message,
    public_messages,
    public_state,
)

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

    config = RunnableConfig(
        configurable=configurable,
        # 写入检查点元数据，使 AG-UI 对话也能显示在 /threads 中
        metadata={"user_id": user_id, "agent_id": agent_id},
        callbacks=callbacks,
    )
    try:
        answer_guard_mode(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return config


class PublicLangGraphAgent(LangGraphAgent):
    """旧输出模式也不能暴露前置检查或上轮保留的私有检查点。"""

    async def _handle_single_event(self, event, state):
        if private_answer_event(event.get("metadata"), event.get("tags")):
            return
        async for output in super()._handle_single_event(event, state):
            yield output

    def get_state_snapshot(self, state):
        return public_state(super().get_state_snapshot(state))

    async def get_state_and_messages_snapshots(self, config):
        snapshot = await self.graph.aget_state(config)
        state = public_state(snapshot.values or {})
        yield StateSnapshotEvent(snapshot=self.get_state_snapshot(state))
        messages = self._filter_orphan_tool_messages(state.get("messages", []))
        yield MessagesSnapshotEvent(messages=langchain_messages_to_agui(messages))


async def _guarded_event_stream(
    graph: AgentGraph,
    input_data: RunAgentInput,
    config: RunnableConfig,
    encoder: EventEncoder,
) -> AsyncGenerator[str, None]:
    """仅将提交后的消息映射为 AG-UI，草稿和模型事件从未进入编码器。"""
    config = {
        **config,
        "configurable": {**config.get("configurable", {}), "thread_id": input_data.thread_id},
    }
    # 检查点是对话的事实来源；客户端不能注入助手证据、工具结果或私有状态。
    latest = agui_messages_to_langchain([input_data.messages[-1]])
    graph_input = {"messages": latest}
    yield encoder.encode(RunStartedEvent(thread_id=input_data.thread_id, run_id=input_data.run_id))
    final_state = None
    try:
        async for mode, event in graph.astream(
            graph_input, config, stream_mode=["updates", "values"]
        ):
            if mode == "values":
                final_state = event
                continue
            if mode != "updates":
                continue
            for node, update in event.items():
                if node == "__interrupt__":
                    # 客服审批属于独立接口，不将图内部中断内容直接暴露为答案。
                    raise ValueError("Unexpected interrupt in answer publication")
                for message in (update or {}).get("messages", []):
                    message = public_message(message, guarded=True)
                    if message is None or isinstance(message, HumanMessage):
                        continue
                    message_id = message.id or str(uuid4())
                    if isinstance(message, AIMessage) and message.tool_calls:
                        for call in message.tool_calls:
                            yield encoder.encode(
                                ToolCallStartEvent(
                                    tool_call_id=call["id"],
                                    tool_call_name=call["name"],
                                    parent_message_id=message_id,
                                )
                            )
                            yield encoder.encode(
                                ToolCallArgsEvent(
                                    tool_call_id=call["id"],
                                    delta=json.dumps(call["args"], ensure_ascii=False),
                                )
                            )
                            yield encoder.encode(ToolCallEndEvent(tool_call_id=call["id"]))
                    elif isinstance(message, ToolMessage):
                        yield encoder.encode(
                            ToolCallResultEvent(
                                message_id=message_id,
                                tool_call_id=message.tool_call_id,
                                content=convert_message_content_to_string(message.content),
                                role="tool",
                            )
                        )
                    elif isinstance(message, AIMessage):
                        yield encoder.encode(TextMessageStartEvent(message_id=message_id))
                        yield encoder.encode(
                            TextMessageContentEvent(
                                message_id=message_id,
                                delta=convert_message_content_to_string(message.content),
                            )
                        )
                        yield encoder.encode(TextMessageEndEvent(message_id=message_id))
        if final_state is None:
            raise ValueError("No final state")
        # 历史中的旧 off 回答仍可展示；私有消息和供应商元数据始终过滤。
        recorded = final_state.get("messages", [])
        last_user = max(
            (i for i, message in enumerate(recorded) if isinstance(message, HumanMessage)),
            default=0,
        )
        messages = public_messages(recorded[:last_user]) + public_messages(
            recorded[last_user:], guarded=True
        )
        if not any(
            isinstance(message, AIMessage)
            and message.additional_kwargs.get("answer_guard_published")
            and not message.tool_calls
            for message in recorded[last_user:]
        ):
            raise ValueError("No published response")
        yield encoder.encode(MessagesSnapshotEvent(messages=langchain_messages_to_agui(messages)))
        yield encoder.encode(
            RunFinishedEvent(thread_id=input_data.thread_id, run_id=input_data.run_id)
        )
    except Exception:
        logger.exception("Guarded AG-UI run failed")
        yield encoder.encode(RunErrorEvent(message="回答暂未完成，请稍后重试。"))


async def _event_stream(
    agent_id: str,
    graph: AgentGraph,
    input_data: RunAgentInput,
    config: RunnableConfig,
    encoder: EventEncoder,
) -> AsyncGenerator[str, None]:
    if answer_guard_mode(config) != "off":
        async for event in _guarded_event_stream(graph, input_data, config, encoder):
            yield event
        return
    # 每个请求创建一个 LangGraphAgent：它保存单次执行状态，创建成本较低
    agent = PublicLangGraphAgent(name=agent_id, graph=graph, config=config, emit_raw_events=False)  # type: ignore[arg-type]
    async for event in agent.run(input_data):
        # 不要转发 RAW 原样透传事件；标准 AG-UI 客户端会忽略它们，
        # 而且它们会向调用者暴露服务端内部信息，包括 on_chat_model_start
        # 中的完整提示词；只有当接口由可信中间层调用，且需要全部
        # 事件数据时，才考虑移除此过滤器，
        # 例如使用 AG-UI 事件检查器时
        if event.type == EventType.RAW:
            continue
        if event.type == EventType.STATE_DELTA:
            # 只发布经过筛选的完整状态，不转发能绕过字段筛选的 JSON Patch。
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
    if answer_guard_mode(config) != "off":
        if not input_data.messages or input_data.messages[-1].role != "user":
            raise HTTPException(status_code=422, detail="检查模式需要一条新的用户消息。")
        if input_data.resume or (input_data.forwarded_props or {}).get("command"):
            raise HTTPException(status_code=422, detail="请通过独立审批接口处理业务审批。")
    encoder = EventEncoder(accept=request.headers.get("accept", ""))
    return StreamingResponse(
        _event_stream(agent_id, graph, input_data, config, encoder),
        media_type=encoder.get_content_type(),
    )
