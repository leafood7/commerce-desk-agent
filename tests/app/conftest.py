from unittest.mock import patch

import pytest

from schema import AgentInfo, ServiceMetadata
from schema.models import OpenAIModelName


@pytest.fixture
def mock_agent_client(mock_env):
    """Fixture for creating a mock AgentClient with a clean environment."""

    mock_info = ServiceMetadata(
        default_agent="test-agent",
        agents=[
            AgentInfo(key="test-agent", description="Test agent"),
            AgentInfo(key="supportpilot", description="Chatbot"),
        ],
        default_model=OpenAIModelName.GPT_5_NANO,
        models=[OpenAIModelName.GPT_5_NANO, OpenAIModelName.GPT_5_MINI],
    )

    with (
        patch("client.AgentClient") as mock_agent_client,
        patch("voice.VoiceManager.from_env", return_value=None),
        patch("shipping_ui.draw_shipping_console"),
    ):
        mock_agent_client_instance = mock_agent_client.return_value
        mock_agent_client_instance.info = mock_info
        # 为模拟对象设置一个固定选中的智能体
        mock_agent_client_instance.agent = "test-agent"
        yield mock_agent_client_instance
