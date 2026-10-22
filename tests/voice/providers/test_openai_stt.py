"""Tests for OpenAI STT provider."""

from unittest.mock import patch

from voice.providers.openai_stt import OpenAISTT


def test_init_with_api_key(mock_openai_client):
    """Test creating OpenAISTT with API key."""
    with patch("voice.providers.openai_stt.OpenAI", return_value=mock_openai_client):
        stt = OpenAISTT(api_key="test-key")
        # 客户端初始化未报错则通过
        assert stt.client == mock_openai_client


def test_transcribe_success(mock_openai_client, mock_audio_file):
    """Test successful audio transcription."""
    with patch("voice.providers.openai_stt.OpenAI", return_value=mock_openai_client):
        stt = OpenAISTT(api_key="test-key")
        result = stt.transcribe(mock_audio_file)
        # 返回去除首尾空白的转写文本则通过
        assert result == "transcribed text"


def test_transcribe_seeks_file_to_beginning(mock_openai_client, mock_audio_file):
    """Test that transcribe seeks file to beginning before reading."""
    # 移动文件指针，模拟文件已被其他代码读取过
    mock_audio_file.seek(100)
    with patch("voice.providers.openai_stt.OpenAI", return_value=mock_openai_client):
        stt = OpenAISTT(api_key="test-key")
        stt.transcribe(mock_audio_file)
        # 转写前文件位置重置到 0 则通过
        assert mock_audio_file.tell() == 0


def test_transcribe_strips_whitespace(mock_openai_client, mock_audio_file):
    """Test that transcription result has whitespace stripped."""
    # 模拟接口返回带有首尾空白的文本
    mock_openai_client.audio.transcriptions.create.return_value = "  text with spaces  "
    with patch("voice.providers.openai_stt.OpenAI", return_value=mock_openai_client):
        stt = OpenAISTT(api_key="test-key")
        result = stt.transcribe(mock_audio_file)
        # 结果去除首尾空白则通过
        assert result == "text with spaces"


def test_transcribe_api_error(mock_openai_client, mock_audio_file):
    """Test that API errors are handled gracefully."""
    # 让模拟对象抛出异常
    mock_openai_client.audio.transcriptions.create.side_effect = Exception("API Error")
    with patch("voice.providers.openai_stt.OpenAI", return_value=mock_openai_client):
        stt = OpenAISTT(api_key="test-key")
        result = stt.transcribe(mock_audio_file)
        # 返回空字符串而非崩溃则通过
        assert result == ""
