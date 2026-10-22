"""VoiceManager - Streamlit integration layer.

This module provides Streamlit-specific UI integration for voice features.
All Streamlit dependencies are isolated here.
"""

import logging
from typing import Optional

import streamlit as st

from voice.stt import SpeechToText
from voice.tts import TextToSpeech

logger = logging.getLogger(__name__)


class VoiceManager:
    """Streamlit convenience layer for voice features.

    This class provides Streamlit-specific methods for voice input/output.
    It handles UI feedback (spinners, errors) while delegating actual
    voice processing to STT and TTS modules.

    Example:
        >>> voice = VoiceManager.from_env()
        >>>
        >>> if voice:
        ...     user_input = voice.get_chat_input()
        ...     if user_input:
        ...         with st.chat_message("ai"):
        ...             voice.render_message("Hello!")
    """

    def __init__(self, stt: SpeechToText | None = None, tts: TextToSpeech | None = None):
        """Initialize VoiceManager.

        Args:
            stt: SpeechToText instance (None to disable STT)
            tts: TextToSpeech instance (None to disable TTS)
        """
        self.stt = stt
        self.tts = tts

        logger.info(
            f"VoiceManager: STT={'enabled' if stt else 'disabled'}, "
            f"TTS={'enabled' if tts else 'disabled'}"
        )

    @classmethod
    def from_env(cls) -> Optional["VoiceManager"]:
        """Create VoiceManager from environment variables.

        Reads VOICE_STT_PROVIDER and VOICE_TTS_PROVIDER to configure
        speech-to-text and text-to-speech providers.

        Returns:
            VoiceManager if either STT or TTS is configured, None otherwise

        Example:
            >>> # In .env:
            >>> # VOICE_STT_PROVIDER=openai
            >>> # VOICE_TTS_PROVIDER=openai
            >>>
            >>> voice = VoiceManager.from_env()
            >>> # Returns configured VoiceManager or None if disabled
        """
        # 根据环境变量创建语音转文字和文字转语音组件
        stt = SpeechToText.from_env()
        tts = TextToSpeech.from_env()

        # 两者都未启用时返回 None，表示没有语音功能
        if not stt and not tts:
            logger.debug("Voice features not configured")
            return None

        return cls(stt=stt, tts=tts)

    def _transcribe_audio(self, audio) -> str | None:
        """Transcribe audio with UI feedback.

        Shows spinner during transcription and error message on failure.

        Args:
            audio: Audio file object from Streamlit chat input

        Returns:
            Transcribed text, or None if transcription failed
        """
        # 防御性检查，正常调用时不应触发
        if not self.stt:
            st.error("⚠️ Speech-to-text not configured.")
            return None

        # 转写过程中显示加载提示
        with st.spinner("🎤 Transcribing audio..."):
            transcribed = self.stt.transcribe(audio)

        # 检查转写是否成功
        if not transcribed:
            st.error("⚠️ Transcription failed. Please try again or type your message.")
            return None

        return transcribed

    def get_chat_input(self, placeholder: str = "Your message") -> str | None:
        """Get chat input with optional voice transcription.

        Handles Streamlit UI including audio input widget and transcription
        feedback (spinner, errors).

        Args:
            placeholder: Placeholder text for input

        Returns:
            User's message (transcribed if audio, otherwise text), or None if no input
        """
        # 未启用语音转文字时，使用普通文本输入
        if not self.stt:
            return st.chat_input(placeholder)

        # 启用语音转文字时，使用支持音频的输入框
        chat_value = st.chat_input(placeholder, accept_audio=True)

        if not chat_value:
            return None

        # 处理纯文本输入返回的字符串
        if isinstance(chat_value, str):
            return chat_value

        # 处理支持音频的输入框返回的对象或字典
        # 提取文字，同时兼容属性访问和字典访问
        text_content = None
        if hasattr(chat_value, "text"):
            text_content = chat_value.text
        elif isinstance(chat_value, dict):
            text_content = chat_value.get("text", "")

        # 提取音频，同时兼容属性访问和字典访问
        audio_content = None
        if hasattr(chat_value, "audio"):
            audio_content = chat_value.audio
        elif isinstance(chat_value, dict):
            audio_content = chat_value.get("audio")

        # 提供了音频时，将其转写为文字
        if audio_content:
            return self._transcribe_audio(audio_content)

        # 没有音频时，返回文本内容
        if text_content:
            return text_content

        # 既没有文字，也没有音频
        return None

    def render_message(self, content: str, container=None, audio_only: bool = False) -> None:
        """Render message with optional TTS audio.

        Handles Streamlit UI including text display and audio player.
        Saves generated audio in session state so it persists across reruns.

        Args:
            content: Message content to display
            container: Streamlit container (defaults to current context)
            audio_only: If True, only render audio (text already displayed)
        """
        if container is None:
            container = st

        # 除 audio_only 模式外都显示文本；该模式用于文本已流式展示的情况
        if not audio_only:
            container.write(content)

        # 启用文字转语音且正文非空时，附加音频
        if self.tts and content.strip():
            # 音频生成过程中显示占位提示
            placeholder = container.empty()
            with placeholder:
                st.caption("🎙️ Generating audio...")

            # 生成文字转语音音频
            audio = self.tts.generate(content)

            # 在会话状态中保存最后一条模型消息的音频
            # 这样可在 st.rerun() 重新运行后继续保留音频
            if audio:
                st.session_state.last_audio = {"data": audio, "format": self.tts.get_format()}

            # 将占位提示替换为音频播放器或错误信息
            if audio:
                placeholder.audio(audio, format=self.tts.get_format())
            else:
                placeholder.caption("🔇 Audio generation unavailable")
