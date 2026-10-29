from collections.abc import Mapping
from typing import Any, cast

from fastapi import HTTPException
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.messages import (
    ChatMessage as LangchainChatMessage,
)

from core import settings
from schema import ChatMessage

PRIVATE_ANSWER_STATE_KEYS = frozenset(
    {"answer_draft", "answer_audit", "answer_public", "policy_check"}
)


def private_answer_event(metadata: Mapping[str, Any] | None = None, tags=()) -> bool:
    """内部检查与草稿事件不能成为公开文本，包括旧模式的前置检查。"""
    metadata = metadata or {}
    event_tags = set(tags or ()) | set(metadata.get("tags") or ())
    return bool(
        metadata.get("answer_guard_private")
        or "answer_guard_private" in event_tags
        or "skip_stream" in event_tags
        or metadata.get("langgraph_node") == "policy_check"
    )


def public_message(message: Any, *, guarded: bool = False) -> BaseMessage | None:
    """只返回可公开消息；工具调用保留参数，但绝不携带生成中的正文。"""
    if not isinstance(message, BaseMessage):
        return None
    private = bool(message.additional_kwargs.get("answer_guard_private"))
    if isinstance(message, AIMessage):
        published = bool(message.additional_kwargs.get("answer_guard_published"))
        if message.tool_calls and (guarded or private):
            return message.model_copy(
                update={"content": "", "additional_kwargs": {}, "response_metadata": {}}
            )
        if private or (guarded and not published):
            return None
        if published:
            # 发布文本由程序生成，供应商元数据不属于公开回答契约。
            return message.model_copy(update={"response_metadata": {}})
    elif private:
        return None
    elif guarded and not isinstance(message, (HumanMessage, ToolMessage)):
        return None
    return message


def public_messages(messages, *, guarded: bool = False) -> list[BaseMessage]:
    return [
        visible
        for message in messages
        if (visible := public_message(message, guarded=guarded)) is not None
    ]


def public_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """模式切回 off 时也隐藏检查点里残留的私有数据。"""
    result = {key: value for key, value in state.items() if key not in PRIVATE_ANSWER_STATE_KEYS}
    if "messages" in result:
        result["messages"] = public_messages(result["messages"] or [])
    return result


def ensure_model_available(model: Any) -> None:
    """Raise 400 if `model` isn't in the operator's AVAILABLE_MODELS allowlist."""
    if model not in settings.AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not available. "
            f"Allowed: {[m.value for m in settings.AVAILABLE_MODELS]}",
        )


def convert_message_content_to_string(content: str | list[str | dict]) -> str:
    if isinstance(content, str):
        return content
    text: list[str] = []
    for content_item in content:
        if isinstance(content_item, str):
            text.append(content_item)
            continue
        if content_item["type"] == "text":
            text.append(content_item["text"])
    return "".join(text)


def langchain_to_chat_message(message: BaseMessage) -> ChatMessage:
    """Create a ChatMessage from a LangChain message."""
    match message:
        case HumanMessage():
            human_message = ChatMessage(
                type="human",
                content=convert_message_content_to_string(message.content),
            )
            return human_message
        case AIMessage():
            ai_message = ChatMessage(
                type="ai",
                content=convert_message_content_to_string(message.content),
            )
            if message.tool_calls:
                ai_message.tool_calls = message.tool_calls
            if message.response_metadata:
                ai_message.response_metadata = message.response_metadata
            return ai_message
        case ToolMessage():
            tool_message = ChatMessage(
                type="tool",
                content=convert_message_content_to_string(message.content),
                tool_call_id=message.tool_call_id,
            )
            return tool_message
        case LangchainChatMessage():
            if message.role == "custom":
                custom_message = ChatMessage(
                    type="custom",
                    content="",
                    custom_data=cast(dict[str, Any], message.content[0]),
                )
                return custom_message
            else:
                raise ValueError(f"Unsupported chat message role: {message.role}")
        case _:
            raise ValueError(f"Unsupported message type: {message.__class__.__name__}")


def messages_from_checkpoint(checkpoint: Mapping[str, Any]) -> list[BaseMessage]:
    """Extract a thread's conversation from a raw checkpoint.

    Graph-state agents keep the conversation in the `messages` channel. Functional-API
    (`@entrypoint`) agents keep it in `__previous__` instead, which `aget_state` does not
    surface - it only returns the entrypoint's final value.
    """
    channel_values = checkpoint.get("channel_values") or {}
    messages = channel_values.get("messages")
    if not messages:
        previous = channel_values.get("__previous__")
        if isinstance(previous, Mapping):
            messages = previous.get("messages")
        elif isinstance(previous, list):
            messages = previous
    return [m for m in (messages or []) if isinstance(m, BaseMessage)]


def remove_tool_calls(content: str | list[str | dict]) -> str | list[str | dict]:
    """Remove tool calls from content."""
    if isinstance(content, str):
        return content
    # 目前只有 Anthropic 模型会流式输出工具调用，内容项类型为 tool_use
    return [
        content_item
        for content_item in content
        if isinstance(content_item, str) or content_item["type"] != "tool_use"
    ]
