"""Tests for TextToSpeech factory class."""

import os
from unittest.mock import patch

import pytest

from voice.tts import TextToSpeech


def test_init_with_openai_provider(mock_openai_client):
    """Test creating TTS with openai provider and explicit API key."""
    with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
        tts = TextToSpeech(provider="openai", api_key="test-key")
        # provider 属性返回 "openai" 则通过
        assert tts.provider == "openai"


def test_init_with_invalid_provider():
    """Test that invalid provider raises ValueError."""
    # 抛出带预期信息的 ValueError 则通过
    with pytest.raises(ValueError, match="Unknown TTS provider: invalid"):
        TextToSpeech(provider="invalid", api_key="test-key")


def test_init_with_unimplemented_provider():
    """Test that unimplemented provider raises NotImplementedError."""
    # 抛出 NotImplementedError 则通过，因为尚未实现 ElevenLabs
    with pytest.raises(NotImplementedError, match="ElevenLabs TTS provider not yet implemented"):
        TextToSpeech(provider="elevenlabs", api_key="test-key")


def test_from_env_provider_not_set():
    """Test from_env returns None when VOICE_TTS_PROVIDER is not set."""
    with patch.dict(os.environ, {}, clear=True):
        result = TextToSpeech.from_env()
        # 环境变量未设置时返回 None 则通过
        assert result is None


def test_from_env_valid_provider(mock_openai_client):
    """Test from_env creates TTS instance with valid provider."""
    with patch.dict(os.environ, {"VOICE_TTS_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"}):
        with patch("voice.providers.openai_tts.OpenAI", return_value=mock_openai_client):
            tts = TextToSpeech.from_env()
            # 使用正确服务商创建 TextToSpeech 实例则通过
            assert tts is not None
            assert tts.provider == "openai"


def test_from_env_invalid_provider_returns_none():
    """Test from_env returns None (and logs error) for invalid provider."""
    with patch.dict(os.environ, {"VOICE_TTS_PROVIDER": "invalid"}):
        result = TextToSpeech.from_env()
        # 返回 None 而非崩溃则通过
        assert result is None


def test_get_api_key_from_param(mock_openai_client):
    """Test that explicit api_key parameter takes precedence over env var."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
        with patch(
            "voice.providers.openai_tts.OpenAI", return_value=mock_openai_client
        ) as mock_openai:
            TextToSpeech(provider="openai", api_key="param-key")
            # OpenAI 客户端使用参数传入的密钥而非环境变量密钥则通过
            mock_openai.assert_called_once_with(api_key="param-key")


def test_get_api_key_from_env(mock_openai_client):
    """Test that API key is loaded from environment when not provided."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
        with patch(
            "voice.providers.openai_tts.OpenAI", return_value=mock_openai_client
        ) as mock_openai:
            TextToSpeech(provider="openai")
            # OpenAI 客户端使用环境变量密钥初始化则通过
            mock_openai.assert_called_once_with(api_key="env-key")
