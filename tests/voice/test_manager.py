"""Tests for VoiceManager core logic (no Streamlit UI tests)."""

from unittest.mock import Mock, patch

from voice.manager import VoiceManager


def test_init_with_both_stt_and_tts():
    """Test creating VoiceManager with both STT and TTS."""
    mock_stt = Mock()
    mock_tts = Mock()
    manager = VoiceManager(stt=mock_stt, tts=mock_tts)
    # 语音转文字和文字转语音组件都正确赋值则通过
    assert manager.stt == mock_stt
    assert manager.tts == mock_tts


def test_init_with_only_tts():
    """Test creating VoiceManager with only TTS (STT=None)."""
    mock_tts = Mock()
    manager = VoiceManager(stt=None, tts=mock_tts)
    # 仅启用部分语音功能（只启用文字转语音）时仍能工作则通过
    assert manager.stt is None
    assert manager.tts == mock_tts


def test_from_env_both_configured():
    """Test from_env when both STT and TTS are configured."""
    mock_stt = Mock()
    mock_tts = Mock()
    with patch("voice.manager.SpeechToText.from_env", return_value=mock_stt):
        with patch("voice.manager.TextToSpeech.from_env", return_value=mock_tts):
            manager = VoiceManager.from_env()
            # 同时配置语音转文字和文字转语音后，成功创建 VoiceManager 则通过
            assert manager is not None
            assert manager.stt == mock_stt
            assert manager.tts == mock_tts


def test_from_env_only_tts_configured():
    """Test from_env when only TTS is configured."""
    mock_tts = Mock()
    with patch("voice.manager.SpeechToText.from_env", return_value=None):
        with patch("voice.manager.TextToSpeech.from_env", return_value=mock_tts):
            manager = VoiceManager.from_env()
            # 仅配置文字转语音后成功创建 VoiceManager 则通过，允许 STT=None
            assert manager is not None
            assert manager.stt is None
            assert manager.tts == mock_tts


def test_from_env_neither_configured():
    """Test from_env when neither STT nor TTS are configured."""
    with patch("voice.manager.SpeechToText.from_env", return_value=None):
        with patch("voice.manager.TextToSpeech.from_env", return_value=None):
            manager = VoiceManager.from_env()
            # 未配置语音功能时返回 None 则通过
            assert manager is None


def test_transcribe_audio_stt_not_configured():
    """Test _transcribe_audio returns None when STT not configured."""
    manager = VoiceManager(stt=None, tts=Mock())
    mock_audio = Mock()
    # 模拟 Streamlit 的 st.error，避免实际调用界面
    with patch("voice.manager.st.error"):
        result = manager._transcribe_audio(mock_audio)
        # 未配置语音转文字时，防御性检查返回 None 则通过
        assert result is None
