"""Tests for OpenAI TTS provider."""

from unittest.mock import patch

import pytest

from voice.providers.openai_tts import OpenAITTS


def test_init_with_valid_params(mock_openai_client):
    """Test creating OpenAITTS with valid parameters."""
    with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
        tts = OpenAITTS(api_key="test-key", voice="nova", model="tts-1")
        # 客户端初始化未报错则通过
        assert tts.client == mock_openai_client


def test_init_with_invalid_voice():
    """Test that invalid voice raises ValueError."""
    # 非法音色触发 ValueError 则通过
    with pytest.raises(ValueError, match="Invalid voice"):
        OpenAITTS(api_key="test-key", voice="invalid", model="tts-1")


def test_init_with_invalid_model():
    """Test that invalid model raises ValueError."""
    # 非法模型触发 ValueError 则通过
    with pytest.raises(ValueError, match="Invalid model"):
        OpenAITTS(api_key="test-key", voice="nova", model="invalid")


def test_validate_text_too_short(mock_openai_client):
    """Test that text shorter than MIN_LENGTH returns None."""
    with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
        tts = OpenAITTS(api_key="test-key")
        result = tts._validate_and_prepare_text("ab")  # 文本仅 2 个字符，小于最小长度 MIN_LENGTH（3）
        # 文本过短时返回 None 则通过
        assert result is None


def test_validate_text_too_long(mock_openai_client):
    """Test that text longer than MAX_LENGTH is truncated."""
    with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
        tts = OpenAITTS(api_key="test-key")
        long_text = "a" * 5000  # 文本长度超过最大长度 MAX_LENGTH（4096）
        result = tts._validate_and_prepare_text(long_text)
        # 文本截断至 MAX_LENGTH 则通过
        assert result is not None
        assert len(result) == 4096


def test_generate_success(mock_openai_client):
    """Test successful audio generation."""
    with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
        tts = OpenAITTS(api_key="test-key")
        result = tts.generate("Hello world")
        # 返回音频字节数据则通过
        assert result == b"fake audio data"


def test_generate_api_error(mock_openai_client):
    """Test that API errors are handled gracefully."""
    # 让模拟对象抛出异常
    mock_openai_client.audio.speech.create.side_effect = Exception("API Error")
    with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
        tts = OpenAITTS(api_key="test-key")
        result = tts.generate("Hello world")
        # 返回 None 而非崩溃则通过
        assert result is None
