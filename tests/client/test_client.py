import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from httpx import Request, Response

from client import AgentClient, AgentClientError
from schema import AgentInfo, ChatHistory, ChatMessage, ServiceMetadata, UserThreads
from schema.models import OpenAIModelName


def test_init(mock_env):
    """Test client initialization with different parameters."""
    # 测试默认值
    client = AgentClient(get_info=False)
    assert client.base_url == "http://0.0.0.0"
    assert client.timeout is None

    # 测试自定义值
    client = AgentClient(
        base_url="http://test",
        timeout=30.0,
        get_info=False,
    )
    assert client.base_url == "http://test"
    assert client.timeout == 30.0
    client.update_agent("test-agent", verify=False)
    assert client.agent == "test-agent"


def test_headers(mock_env):
    """Test header generation with and without auth."""
    # 测试未配置身份验证的情况
    client = AgentClient(get_info=False)
    assert client._headers == {}

    # 测试配置身份验证的情况
    with patch.dict(os.environ, {"AUTH_SECRET": "test-secret"}, clear=True):
        client = AgentClient(get_info=False)
        assert client._headers == {"Authorization": "Bearer test-secret"}


def test_invoke(agent_client):
    """Test synchronous invocation."""
    QUESTION = "What is the weather?"
    ANSWER = "The weather is sunny."

    # 模拟成功响应
    mock_request = Request("POST", "http://test/invoke")
    mock_response = Response(
        200,
        json={"type": "ai", "content": ANSWER},
        request=mock_request,
    )
    with patch("httpx.post", return_value=mock_response):
        response = agent_client.invoke(QUESTION)
        assert isinstance(response, ChatMessage)
        assert response.type == "ai"
        assert response.content == ANSWER

    # 测试指定模型和 thread_id 的情况
    with patch("httpx.post", return_value=mock_response) as mock_post:
        response = agent_client.invoke(
            QUESTION,
            model="gpt-5-nano",
            thread_id="test-thread",
        )
        assert isinstance(response, ChatMessage)
        # 验证请求内容
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["message"] == QUESTION
        assert kwargs["json"]["model"] == "gpt-5-nano"
        assert kwargs["json"]["thread_id"] == "test-thread"

    # 测试错误响应
    error_response = Response(500, text="Internal Server Error", request=mock_request)
    with patch("httpx.post", return_value=error_response):
        with pytest.raises(AgentClientError) as exc:
            agent_client.invoke(QUESTION)
        assert "500 Internal Server Error" in str(exc.value)


@pytest.mark.asyncio
async def test_ainvoke(agent_client):
    """Test asynchronous invocation."""
    QUESTION = "What is the weather?"
    ANSWER = "The weather is sunny."

    # 测试成功响应
    mock_request = Request("POST", "http://test/invoke")
    mock_response = Response(200, json={"type": "ai", "content": ANSWER}, request=mock_request)
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        response = await agent_client.ainvoke(QUESTION)
        assert isinstance(response, ChatMessage)
        assert response.type == "ai"
        assert response.content == ANSWER

    # 测试指定模型和 thread_id 的情况
    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        response = await agent_client.ainvoke(
            QUESTION,
            model="gpt-5-nano",
            thread_id="test-thread",
        )
        assert isinstance(response, ChatMessage)
        assert response.type == "ai"
        assert response.content == ANSWER
        # 验证请求内容
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["message"] == QUESTION
        assert kwargs["json"]["model"] == "gpt-5-nano"
        assert kwargs["json"]["thread_id"] == "test-thread"

    # 测试错误响应
    error_response = Response(500, text="Internal Server Error", request=mock_request)
    with patch("httpx.AsyncClient.post", return_value=error_response):
        with pytest.raises(AgentClientError) as exc:
            await agent_client.ainvoke(QUESTION)
        assert "500 Internal Server Error" in str(exc.value)


def test_stream(agent_client):
    """Test synchronous streaming."""
    QUESTION = "What is the weather?"
    TOKENS = ["The", " weather", " is", " sunny", "."]
    FINAL_ANSWER = "The weather is sunny."

    # 创建包含流式事件的模拟响应
    events = (
        [f"data: {json.dumps({'type': 'token', 'content': token})}" for token in TOKENS]
        + [
            f"data: {json.dumps({'type': 'message', 'content': {'type': 'ai', 'content': FINAL_ANSWER}})}"
        ]
        + ["data: [DONE]"]
    )

    # 模拟流式响应
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.iter_lines.return_value = events
    mock_response.request = Request("POST", "http://test/stream")
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=None)

    with patch("httpx.stream", return_value=mock_response):
        # 收集所有流式返回结果
        responses = list(agent_client.stream(QUESTION))

        # 确认文本片段已逐步输出
        assert len(responses) == len(TOKENS) + 1  # 文本片段加上最终消息
        for i, token in enumerate(TOKENS):
            assert responses[i] == token

        # 验证最终消息
        final_message = responses[-1]
        assert isinstance(final_message, ChatMessage)
        assert final_message.type == "ai"
        assert final_message.content == FINAL_ANSWER

    # 测试错误响应
    error_response = Response(
        500, text="Internal Server Error", request=Request("POST", "http://test/stream")
    )
    error_response_mock = Mock()
    error_response_mock.__enter__ = Mock(return_value=error_response)
    error_response_mock.__exit__ = Mock(return_value=None)
    with patch("httpx.stream", return_value=error_response_mock):
        with pytest.raises(AgentClientError) as exc:
            list(agent_client.stream(QUESTION))
        assert "500 Internal Server Error" in str(exc.value)


@pytest.mark.asyncio
async def test_astream(agent_client):
    """Test asynchronous streaming."""
    QUESTION = "What is the weather?"
    TOKENS = ["The", " weather", " is", " sunny", "."]
    FINAL_ANSWER = "The weather is sunny."

    # 创建包含流式事件的模拟响应
    events = (
        [f"data: {json.dumps({'type': 'token', 'content': token})}" for token in TOKENS]
        + [
            f"data: {json.dumps({'type': 'message', 'content': {'type': 'ai', 'content': FINAL_ANSWER}})}"
        ]
        + ["data: [DONE]"]
    )

    # 为事件创建异步迭代器
    async def async_events():
        for event in events:
            yield event

    # 模拟流式响应
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.request = Request("POST", "http://test/stream")
    mock_response.raise_for_status = Mock()
    mock_response.aiter_lines = Mock(return_value=async_events())
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.stream = Mock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        # 收集所有流式返回结果
        responses = []
        async for response in agent_client.astream(QUESTION):
            responses.append(response)

        # 确认文本片段已逐步输出
        assert len(responses) == len(TOKENS) + 1  # 文本片段加上最终消息
        for i, token in enumerate(TOKENS):
            assert responses[i] == token

        # 验证最终消息
        final_message = responses[-1]
        assert isinstance(final_message, ChatMessage)
        assert final_message.type == "ai"
        assert final_message.content == FINAL_ANSWER

    # 测试错误响应
    error_response = Response(
        500, text="Internal Server Error", request=Request("POST", "http://test/stream")
    )
    error_response_mock = AsyncMock()
    error_response_mock.__aenter__ = AsyncMock(return_value=error_response)

    mock_client.stream.return_value = error_response_mock

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(AgentClientError) as exc:
            async for _ in agent_client.astream(QUESTION):
                pass
        assert "500 Internal Server Error" in str(exc.value)


@pytest.mark.asyncio
async def test_acreate_feedback(agent_client):
    """Test asynchronous feedback creation."""
    RUN_ID = "test-run"
    KEY = "test-key"
    SCORE = 0.8
    KWARGS = {"comment": "Great response!"}

    # 测试成功响应
    mock_response = Response(200, json={}, request=Request("POST", "http://test/feedback"))
    with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
        await agent_client.acreate_feedback(RUN_ID, KEY, SCORE, KWARGS)
        # 验证请求内容
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["run_id"] == RUN_ID
        assert kwargs["json"]["key"] == KEY
        assert kwargs["json"]["score"] == SCORE
        assert kwargs["json"]["kwargs"] == KWARGS

    # 测试错误响应
    error_response = Response(
        500, text="Internal Server Error", request=Request("POST", "http://test/feedback")
    )
    with patch("httpx.AsyncClient.post", return_value=error_response):
        with pytest.raises(AgentClientError) as exc:
            await agent_client.acreate_feedback(RUN_ID, KEY, SCORE)
        assert "500 Internal Server Error" in str(exc.value)


def test_get_history(agent_client):
    """Test chat history retrieval."""
    THREAD_ID = "test-thread"
    HISTORY = {
        "messages": [
            {"type": "human", "content": "What is the weather?"},
            {"type": "ai", "content": "The weather is sunny."},
        ]
    }

    # 模拟成功响应，默认使用客户端当前选中的智能体
    mock_response = Response(200, json=HISTORY, request=Request("POST", "http://test/history"))
    with patch("httpx.post", return_value=mock_response) as mock_post:
        history = agent_client.get_history(THREAD_ID)
        assert isinstance(history, ChatHistory)
        assert len(history.messages) == 2
        assert history.messages[0].type == "human"
        assert history.messages[1].type == "ai"
        # 使用客户端当前选中的智能体限定历史查询范围
        assert mock_post.call_args.args[0] == "http://test/test-agent/history"

    # 显式指定智能体时，覆盖客户端当前选择
    with patch("httpx.post", return_value=mock_response) as mock_post:
        agent_client.get_history(THREAD_ID, agent="chatbot")
        assert mock_post.call_args.args[0] == "http://test/chatbot/history"

    # 测试错误响应
    error_response = Response(
        500, text="Internal Server Error", request=Request("POST", "http://test/history")
    )
    with patch("httpx.post", return_value=error_response):
        with pytest.raises(AgentClientError) as exc:
            agent_client.get_history(THREAD_ID)
        assert "500 Internal Server Error" in str(exc.value)


def test_info(agent_client):
    assert agent_client.info is None
    assert agent_client.agent == "test-agent"

    # 模拟服务信息响应
    test_info = ServiceMetadata(
        default_agent="custom-agent",
        agents=[AgentInfo(key="custom-agent", description="Custom agent")],
        default_model=OpenAIModelName.GPT_5_NANO,
        models=[OpenAIModelName.GPT_5_NANO, OpenAIModelName.GPT_5_MINI],
    )
    test_response = Response(
        200, json=test_info.model_dump(), request=Request("GET", "http://test/info")
    )

    # 使用服务信息更新已有客户端
    with patch("httpx.get", return_value=test_response):
        agent_client.retrieve_info()

    assert agent_client.info == test_info
    assert agent_client.agent == "custom-agent"

    # 测试 update_agent 接收非法值的情况
    with pytest.raises(AgentClientError) as exc:
        agent_client.update_agent("unknown-agent")
    assert "Agent unknown-agent not found in available agents: custom-agent" in str(exc.value)

    # 测试通过服务信息创建全新客户端
    with patch("httpx.get", return_value=test_response):
        agent_client = AgentClient(base_url="http://test")
    assert agent_client.info == test_info
    assert agent_client.agent == "custom-agent"

    # 未设置智能体时，测试 invoke 是否报错
    agent_client = AgentClient(base_url="http://test", get_info=False)
    with pytest.raises(AgentClientError) as exc:
        agent_client.invoke("test")
    assert "No agent selected. Use update_agent() to select an agent." in str(exc.value)


def test_get_user_threads(agent_client):
    """Test user threads retrieval under various configurations."""
    USER_ID = "user-123"

    MOCK_THREADS_RESPONSE = {
        "threads": [
            {
                "thread_id": "thread-1",
                "user_id": USER_ID,
                "agent_id": "test-agent",
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "metadata": {"title": "First Chat"},
            },
            {
                "thread_id": "thread-2",
                "user_id": USER_ID,
                "agent_id": "test-agent",
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "metadata": {"title": "Second Chat"},
            },
        ]
    }

    mock_request = Request("GET", "http://test/test-agent/threads")
    mock_response = Response(200, json=MOCK_THREADS_RESPONSE, request=mock_request)

    with patch("httpx.get", return_value=mock_response) as mock_get:
        result = agent_client.get_user_threads(USER_ID)

        assert isinstance(result, UserThreads)
        assert len(result.threads) == 2
        assert result.threads[0].thread_id == "thread-1"

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "http://test/test-agent/threads"
        assert kwargs["params"] == {
            "user_id": USER_ID,
            "limit": 20,
        }

    with patch("httpx.get", return_value=mock_response) as mock_get:
        agent_client.get_user_threads(USER_ID, agent="custom-agent", limit=50)

        args, kwargs = mock_get.call_args
        assert args[0] == "http://test/custom-agent/threads"
        assert kwargs["params"] == {
            "user_id": USER_ID,
            "limit": 50,
        }

    agent_client.agent = None
    with patch("httpx.get", return_value=mock_response) as mock_get:
        agent_client.get_user_threads(USER_ID)

        args, kwargs = mock_get.call_args
        assert args[0] == "http://test/threads"
        assert kwargs["params"] == {"user_id": USER_ID, "limit": 20}

    agent_client.agent = "test-agent"

    error_response = Response(500, text="Internal Server Error", request=mock_request)
    with patch("httpx.get", return_value=error_response):
        with pytest.raises(AgentClientError) as exc:
            agent_client.get_user_threads(USER_ID)
        assert "Error:" in str(exc.value)
        assert "500 Internal Server Error" in str(exc.value)


@pytest.mark.asyncio
async def test_aget_user_threads(agent_client):
    """Test async user threads retrieval."""
    USER_ID = "user-123"

    mock_request = Request("GET", "http://test/test-agent/threads")
    mock_response = Response(
        200,
        json={
            "threads": [
                {
                    "thread_id": "thread-1",
                    "agent_id": "test-agent",
                    "updated_at": datetime.now(UTC).isoformat(),
                    "title": "First Chat",
                }
            ]
        },
        request=mock_request,
    )

    with patch("httpx.AsyncClient.get", return_value=mock_response) as mock_get:
        result = await agent_client.aget_user_threads(USER_ID, agent="custom-agent", limit=50)

        assert isinstance(result, UserThreads)
        assert result.threads[0].thread_id == "thread-1"

        args, kwargs = mock_get.call_args
        assert args[0] == "http://test/custom-agent/threads"
        assert kwargs["params"] == {"user_id": USER_ID, "limit": 50}

    error_response = Response(500, text="Internal Server Error", request=mock_request)
    with patch("httpx.AsyncClient.get", return_value=error_response):
        with pytest.raises(AgentClientError) as exc:
            await agent_client.aget_user_threads(USER_ID)
        assert "500 Internal Server Error" in str(exc.value)
