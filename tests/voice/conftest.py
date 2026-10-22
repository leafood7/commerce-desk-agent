"""Shared fixtures for voice module tests."""

import io
from unittest.mock import Mock

import pytest


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for TTS/STT tests."""
    client = Mock()

    # 模拟文字转语音响应，返回带有 .content 属性的对象
    mock_audio_response = Mock()
    mock_audio_response.content = b"fake audio data"
    client.audio.speech.create.return_value = mock_audio_response

    # 模拟语音转文字响应；response_format="text" 时直接返回字符串
    client.audio.transcriptions.create.return_value = "transcribed text"

    return client


@pytest.fixture
def mock_audio_file():
    """BytesIO mock audio file for STT tests."""
    return io.BytesIO(b"fake audio bytes")
