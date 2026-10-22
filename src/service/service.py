import inspect
import json
import logging
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_core._api import LangChainBetaWarning
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse  # type: ignore[import-untyped]
from langfuse.langchain import (
    CallbackHandler,  # type: ignore[import-untyped]
)
from langgraph.types import Command, Interrupt
from langsmith import Client as LangsmithClient
from langsmith import uuid7

from agents import DEFAULT_AGENT, AgentGraph, get_agent, get_all_agent_info, load_agent
from core import settings
from memory import initialize_database, initialize_store
from schema import (
    ChatHistory,
    ChatHistoryInput,
    ChatMessage,
    Feedback,
    FeedbackResponse,
    ServiceMetadata,
    StreamInput,
    UserInput,
    UserThreads,
    UserThreadsInput,
)
from service.agui import router as agui_router
from service.threads import list_user_threads
from service.shipping_api import router as shipping_router
from service.utils import (
    convert_message_content_to_string,
    ensure_model_available,
    langchain_to_chat_message,
    messages_from_checkpoint,
    remove_tool_calls,
)

warnings.filterwarnings("ignore", category=LangChainBetaWarning)
logger = logging.getLogger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    """Generate idiomatic operation IDs for OpenAPI client generation."""
    return route.name


def verify_bearer(
    http_auth: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(HTTPBearer(description="Please provide AUTH_SECRET api key.", auto_error=False)),
    ],
) -> None:
    if not settings.AUTH_SECRET:
        return
    auth_secret = settings.AUTH_SECRET.get_secret_value()
    if not http_auth or http_auth.credentials != auth_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Configurable lifespan that initializes the appropriate database checkpointer, store,
    and agents with async loading - for example for starting up MCP clients.
    """
    try:
        # 同时初始化检查点组件（短期记忆）和存储组件（长期记忆）
        async with initialize_database() as saver, initialize_store() as store:
            # 初始化这两个组件
            if hasattr(saver, "setup"):  # ignore: union-attr
                await saver.setup()
            # 仅为 PostgreSQL 存储执行初始化；InMemoryStore 不需要此步骤
            if hasattr(store, "setup"):  # ignore: union-attr
                await store.setup()

            if not settings.AUTH_SECRET:
                logger.warning(
                    "AUTH_SECRET is not configured. This workspace has no account isolation. "
                    "Set AUTH_SECRET in your environment to enable bearer token authentication."
                )

            # 为各智能体配置两个记忆组件，并支持异步加载
            agents = get_all_agent_info()
            for a in agents:
                try:
                    await load_agent(a.key)
                    logger.info(f"Agent loaded: {a.key}")
                except Exception as e:
                    logger.error(f"Failed to load agent {a.key}: {e}")
                    # 单个智能体加载失败时，继续加载其他智能体，不终止整个服务启动

                agent = get_agent(a.key)
                # 设置检查点组件，用于保存当前对话的记忆和消息历史
                agent.checkpointer = saver
                # 设置存储组件，用于保存跨对话的长期知识
                agent.store = store
            yield
    except Exception as e:
        logger.error(f"Error during database/store/agents initialization: {e}")
        raise


app = FastAPI(lifespan=lifespan, generate_unique_id_function=custom_generate_unique_id)
router = APIRouter(dependencies=[Depends(verify_bearer)])
# AG-UI 协议接口使用相同的 Bearer 身份验证，参见 service/agui.py
router.include_router(agui_router)
router.include_router(shipping_router)


@router.get("/info")
async def info() -> ServiceMetadata:
    models = list(settings.AVAILABLE_MODELS)
    models.sort()
    return ServiceMetadata(
        agents=get_all_agent_info(),
        models=models,
        default_agent=DEFAULT_AGENT,
        default_model=settings.DEFAULT_MODEL,
    )


async def _handle_input(
    user_input: UserInput, agent: AgentGraph, agent_id: str
) -> tuple[dict[str, Any], UUID]:
    """
    Parse user input and handle any required interrupt resumption.
    Returns kwargs for agent invocation and the run_id.
    """
    run_id = uuid7()
    thread_id = user_input.thread_id or str(uuid4())
    user_id = user_input.user_id or str(uuid4())

    configurable = {"thread_id": thread_id, "user_id": user_id}
    if user_input.model is not None:
        ensure_model_available(user_input.model)
        configurable["model"] = user_input.model

    callbacks: list[Any] = []
    if settings.LANGFUSE_TRACING:
        # 初始化 LangChain 的 Langfuse 回调处理器，用于追踪执行过程
        langfuse_handler = CallbackHandler()

        callbacks.append(langfuse_handler)

    if user_input.agent_config:
        # 检查保留键，即使 configurable 中没有 model，也不能通过此处覆盖它
        reserved_keys = {"thread_id", "user_id", "model"}
        if overlap := reserved_keys & user_input.agent_config.keys():
            raise HTTPException(
                status_code=422,
                detail=f"agent_config contains reserved keys: {overlap}",
            )
        configurable.update(user_input.agent_config)

    config = RunnableConfig(
        configurable=configurable,
        metadata={"user_id": user_id, "agent_id": agent_id},
        run_id=run_id,
        callbacks=callbacks,
    )

    # 检查是否有等待恢复的中断
    state = await agent.aget_state(config=config)

    interrupted_tasks = [
        task for task in state.tasks if hasattr(task, "interrupts") and task.interrupts
    ]

    input: Command | dict[str, Any]
    if interrupted_tasks:
        # 如果存在中断，将用户输入视为恢复智能体执行所需的回答
        input = Command(resume=user_input.message)
    else:
        input = {"messages": [HumanMessage(content=user_input.message)]}

    kwargs = {
        "input": input,
        "config": config,
    }

    return kwargs, run_id


@router.post("/{agent_id}/invoke", operation_id="invoke_with_agent_id")
@router.post("/invoke")
async def invoke(user_input: UserInput, agent_id: str = DEFAULT_AGENT) -> ChatMessage:
    """
    Invoke an agent with user input to retrieve a final response.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to messages for recording feedback.
    Use user_id to persist and continue a conversation across multiple threads.
    """
    # 注意：当前接口只返回最后一条消息或中断信息
    # 如果智能体输出多条 AIMessage，例如 interrupt-agent 中的后台步骤，
    # 或 research-assistant 中的工具步骤，中间消息会被省略；
    # 如需保留这些内容，可以修改接口，返回 ChatMessage 列表，
    # 以包含这些中间消息
    agent: AgentGraph = get_agent(agent_id)
    kwargs, run_id = await _handle_input(user_input, agent, agent_id)

    try:
        response_events: list[tuple[str, Any]] = await agent.ainvoke(**kwargs, stream_mode=["updates", "values"])  # type: ignore # fmt: skip
        response_type, response = response_events[-1]
        # 执行因中断而停止时，两种流模式都会在最后一个事件中报告中断，
        # 所以应先检查中断，再考虑使用最后一条消息
        if "__interrupt__" in response:
            # 将第一个中断的值作为 AIMessage 返回
            output = langchain_to_chat_message(
                AIMessage(content=response["__interrupt__"][0].value)
            )
        elif response_type == "values":
            # 正常响应，智能体已成功完成本轮执行
            output = langchain_to_chat_message(response["messages"][-1])
        else:
            raise ValueError(f"Unexpected response type: {response_type}")

        output.run_id = str(run_id)
        return output
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


async def message_generator(
    user_input: StreamInput, agent_id: str = DEFAULT_AGENT
) -> AsyncGenerator[str, None]:
    """
    Generate a stream of messages from the agent.

    This is the workhorse method for the /stream endpoint.
    """
    agent: AgentGraph = get_agent(agent_id)
    kwargs, run_id = await _handle_input(user_input, agent, agent_id)

    try:
        # 处理执行图产生的流式事件，并通过 SSE 逐步发送消息
        async for stream_event in agent.astream(  # type: ignore[no-matching-overload]
            **kwargs, stream_mode=["updates", "messages", "custom"], subgraphs=True
        ):
            if not isinstance(stream_event, tuple):
                continue
            # 根据是否启用子图，处理不同的事件结构
            if len(stream_event) == 3:
                # 启用 subgraphs=True 时，结构为：(节点路径, 流模式, 事件)
                _, stream_mode, event = stream_event
            else:
                # 未启用子图时，结构为：(流模式, 事件)
                stream_mode, event = stream_event
            new_messages: list[Any] = []
            if stream_mode == "updates":
                for node, updates in event.items():
                    # 这里使用简化方式处理智能体中断
                    # 更完善的实现可以增加
                    # 一种结构化的 ChatMessage 类型，专门返回中断值
                    if node == "__interrupt__":
                        interrupt: Interrupt
                        for interrupt in updates:
                            new_messages.append(AIMessage(content=interrupt.value))
                        continue
                    updates = updates or {}
                    update_messages = updates.get("messages", [])
                    # 使用 langgraph-supervisor 库时的特殊处理
                    if "supervisor" in node or "sub-agent" in node:
                        # 实际智能体在这里提供的工具仅用于移交和交还控制权
                        if isinstance(update_messages[-1], ToolMessage):
                            if "sub-agent" in node and len(update_messages) > 1:
                                # 如果是子智能体，只保留最后两条消息：交还控制权的工具调用及其结果
                                update_messages = update_messages[-2:]
                            else:
                                # 如果是监督者，只保留最后一条移交结果；工具调用本身来自 agent 节点
                                update_messages = [update_messages[-1]]
                        else:
                            update_messages = []
                    new_messages.extend(update_messages)

            if stream_mode == "custom":
                new_messages = [event]

            # LangGraph 流式输出可能产生元组：(字段名, 字段值)
            # 例如 ('content', 字符串)、('tool_calls', 工具调用列表)、('additional_kwargs', 附加数据)
            # 只将支持的字段收集到 parts 中，跳过不支持的元数据
            # 更多信息：https://langchain-ai.github.io/langgraph/cloud/how-tos/stream_messages/
            processed_messages = []
            current_message: dict[str, Any] = {}
            for message in new_messages:
                if isinstance(message, tuple):
                    key, value = message
                    # 将消息各部分暂存到字典中
                    current_message[key] = value
                else:
                    # 如果正在组装消息，则先加入已完成的那条消息
                    if current_message:
                        processed_messages.append(_create_ai_message(current_message))
                        current_message = {}
                    processed_messages.append(message)

            # 加入剩余的消息片段
            if current_message:
                processed_messages.append(_create_ai_message(current_message))

            for message in processed_messages:
                try:
                    chat_message = langchain_to_chat_message(message)
                    chat_message.run_id = str(run_id)
                except Exception as e:
                    logger.error(f"Error parsing message: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'content': 'Unexpected error'})}\n\n"
                    continue
                # LangGraph 会再次发送输入消息，为避免网页重复显示，将其过滤掉
                if chat_message.type == "human" and chat_message.content == user_input.message:
                    continue
                yield f"data: {json.dumps({'type': 'message', 'content': chat_message.model_dump()})}\n\n"

            if stream_mode == "messages":
                if not user_input.stream_tokens:
                    continue
                msg, metadata = event
                if "skip_stream" in metadata.get("tags", []):
                    continue
                # astream("messages") 有时也会让非模型节点产生额外消息，
                # 这里将其过滤掉
                if not isinstance(msg, AIMessageChunk):
                    continue
                content = remove_tool_calls(msg.content)
                if content:
                    # 在 OpenAI 兼容响应中，正文为空通常表示
                    # 模型正在请求调用工具，
                    # 因此只输出非空的正文内容
                    yield f"data: {json.dumps({'type': 'token', 'content': convert_message_content_to_string(content)})}\n\n"
    except Exception as e:
        logger.error(f"Error in message generator: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': 'Internal server error'})}\n\n"
    finally:
        yield "data: [DONE]\n\n"


def _create_ai_message(parts: dict) -> AIMessage:
    sig = inspect.signature(AIMessage)
    valid_keys = set(sig.parameters)
    filtered = {k: v for k, v in parts.items() if k in valid_keys}
    return AIMessage(**filtered)


def _sse_response_example() -> dict[int | str, Any]:
    return {
        status.HTTP_200_OK: {
            "description": "Server Sent Event Response",
            "content": {
                "text/event-stream": {
                    "example": "data: {'type': 'token', 'content': 'Hello'}\n\ndata: {'type': 'token', 'content': ' World'}\n\ndata: [DONE]\n\n",
                    "schema": {"type": "string"},
                }
            },
        }
    }


@router.post(
    "/{agent_id}/stream",
    response_class=StreamingResponse,
    responses=_sse_response_example(),
    operation_id="stream_with_agent_id",
)
@router.post("/stream", response_class=StreamingResponse, responses=_sse_response_example())
async def stream(user_input: StreamInput, agent_id: str = DEFAULT_AGENT) -> StreamingResponse:
    """
    Stream an agent's response to a user input, including intermediate messages and tokens.

    If agent_id is not provided, the default agent will be used.
    Use thread_id to persist and continue a multi-turn conversation. run_id kwarg
    is also attached to all messages for recording feedback.
    Use user_id to persist and continue a conversation across multiple threads.

    Set `stream_tokens=false` to return intermediate messages but not token-by-token.
    """
    return StreamingResponse(
        message_generator(user_input, agent_id),
        media_type="text/event-stream",
    )


@router.post("/feedback")
async def feedback(feedback: Feedback) -> FeedbackResponse:
    """
    Record feedback for a run to LangSmith.

    This is a simple wrapper for the LangSmith create_feedback API, so the
    credentials can be stored and managed in the service rather than the client.
    See: https://api.smith.langchain.com/redoc#tag/feedback/operation/create_feedback_api_v1_feedback_post
    """
    client = LangsmithClient()
    kwargs = feedback.kwargs or {}
    client.create_feedback(
        run_id=feedback.run_id,
        key=feedback.key,
        score=feedback.score,
        **kwargs,
    )
    return FeedbackResponse()


@router.post("/{agent_id}/history", operation_id="history_with_agent_id")
@router.post("/history")
async def history(input: ChatHistoryInput, agent_id: str = DEFAULT_AGENT) -> ChatHistory:
    """
    Get chat history for a thread and agent.

    If agent_id is not provided, the default agent will be used.
    """
    agent: AgentGraph = get_agent(agent_id)
    config = RunnableConfig(configurable={"thread_id": input.thread_id})
    try:
        messages: list[BaseMessage] = []
        # 函数式 API 智能体将对话保存在 __previous__ 中，aget_state 不会返回它；
        # 因此优先读取原始检查点，只有必要时才回退到图状态读取方式
        checkpointer = getattr(agent, "checkpointer", None)
        if checkpointer:
            tup = await checkpointer.aget_tuple(config)
            if tup and "__previous__" in (tup.checkpoint.get("channel_values") or {}):
                messages = messages_from_checkpoint(tup.checkpoint)
        if not messages:
            state_snapshot = await agent.aget_state(config=config)
            messages = state_snapshot.values["messages"]
        chat_messages: list[ChatMessage] = [langchain_to_chat_message(m) for m in messages]
        return ChatHistory(messages=chat_messages)
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")


@router.get("/{agent_id}/threads", operation_id="threads_with_agent_id")
@router.get("/threads")
async def threads(
    input: UserThreadsInput = Depends(), agent_id: str = DEFAULT_AGENT
) -> UserThreads:
    """
    List a user's conversation threads for an agent, most recently updated first.

    `user_id` is asserted by the caller and not checked against the credentials on the
    request, so any holder of the bearer token can list any user's threads - the same
    trust model as /history. Put your own authorization in front of this before end
    users can reach it.
    """
    agent: AgentGraph = get_agent(agent_id)
    checkpointer = getattr(agent, "checkpointer", None)
    if not checkpointer:
        return UserThreads(threads=[])

    try:
        summaries = await list_user_threads(checkpointer, input.user_id, agent_id, input.limit)
    except Exception as e:
        logger.error(f"An exception occurred: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error")

    return UserThreads(threads=summaries)


@app.get("/health")
async def health_check():
    """Health check endpoint."""

    health_status = {"status": "ok"}

    if settings.LANGFUSE_TRACING:
        try:
            langfuse = Langfuse()
            health_status["langfuse"] = "connected" if langfuse.auth_check() else "disconnected"
        except Exception as e:
            logger.error(f"Langfuse connection error: {e}")
            health_status["langfuse"] = "disconnected"

    return health_status


app.include_router(router)
