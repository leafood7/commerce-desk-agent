from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.types import StateSnapshot

from service import app


@pytest.fixture
def test_client():
    """Fixture to create a FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def mock_agent():
    """Fixture to create a mock agent that can be configured for different test scenarios."""
    agent_mock = AsyncMock()
    agent_mock.ainvoke = AsyncMock(
        return_value=[("values", {"messages": [AIMessage(content="Test response")]})]
    )
    agent_mock.aget_state = AsyncMock(
        return_value=StateSnapshot(
            values={},
            next=(),
            config={},
            metadata=None,
            created_at=None,
            parent_config=None,
            tasks=(),
            interrupts=(),
        )
    )
    # 涉及读取检查点的测试需要显式设置此属性，否则 AsyncMock
    # 会自动创建一个看似可用的检查点对象属性
    agent_mock.checkpointer = None
    with patch("service.service.get_agent", Mock(return_value=agent_mock)):
        yield agent_mock


@pytest.fixture
def mock_settings(mock_env):
    """Fixture to ensure settings are clean for each test."""
    with patch("service.service.settings") as mock_settings:
        yield mock_settings


@pytest.fixture
def mock_httpx():
    """Exercise HTTP serialization without starting production model/MCP lifespans."""
    client = TestClient(app)

    def mock_stream(method: str, url: str, **kwargs):
        return client.stream(method, url.replace("http://0.0.0.0", ""), **kwargs)

    def mock_get(url: str, **kwargs):
        return client.get(url.replace("http://0.0.0.0", ""), **kwargs)

    try:
        with patch("httpx.stream", mock_stream), patch("httpx.get", mock_get):
            yield
    finally:
        client.close()
