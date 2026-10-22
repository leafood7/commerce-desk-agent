"""OpenAI text-to-speech implementation."""

import logging

from openai import OpenAI

logger = logging.getLogger(__name__)


class OpenAITTS:
    """OpenAI TTS provider."""

    # 接口限制
    MAX_TEXT_LENGTH = 4096
    MIN_TEXT_LENGTH = 3

    # 可用的配置选项
    VALID_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    VALID_MODELS = ["tts-1", "tts-1-hd"]

    def __init__(self, api_key: str | None = None, voice: str = "alloy", model: str = "tts-1"):
        """Initialize OpenAI TTS.

        Args:
            api_key: OpenAI API key (uses env var if not provided)
            voice: Voice name (alloy, echo, fable, onyx, nova, shimmer)
            model: Model name (tts-1 or tts-1-hd)

        Raises:
            ValueError: If voice or model is invalid
            Exception: If OpenAI client initialization fails
        """
        # 校验音色参数
        if voice not in self.VALID_VOICES:
            raise ValueError(f"Invalid voice '{voice}'. Must be one of {self.VALID_VOICES}")

        # 校验模型参数
        if model not in self.VALID_MODELS:
            raise ValueError(f"Invalid model '{model}'. Must be one of {self.VALID_MODELS}")

        # 使用传入的密钥或环境变量创建 OpenAI 客户端
        self.client = OpenAI(api_key=api_key) if api_key else OpenAI()
        self.voice = voice
        self.model = model

        logger.info(f"OpenAI TTS initialized: voice={voice}, model={model}")

    def _validate_and_prepare_text(self, text: str) -> str | None:
        """Validate and prepare text for TTS generation.

        Args:
            text: Raw text input

        Returns:
            Prepared text ready for TTS, or None if text is too short

        Note:
            - Strips whitespace
            - Returns None if text is below minimum length
            - Truncates text if above maximum length
        """
        # 移除文本首尾空白字符
        text = text.strip()

        # 跳过过短文本，避免不值得的接口调用
        if len(text) < self.MIN_TEXT_LENGTH:
            logger.debug(f"OpenAI TTS: skipping short text ({len(text)} chars)")
            return None

        # 必要时截断文本，避免超过接口长度限制
        if len(text) > self.MAX_TEXT_LENGTH:
            logger.warning(
                f"OpenAI TTS: truncating from {len(text)} to {self.MAX_TEXT_LENGTH} chars"
            )
            text = text[: self.MAX_TEXT_LENGTH]

        return text

    def generate(self, text: str) -> bytes | None:
        """Generate speech from text.

        Args:
            text: Text to convert to speech

        Returns:
            MP3 audio bytes, or None if text is too short or generation fails

        Note:
            - Text shorter than 3 chars returns None
            - Text longer than 4096 chars is truncated
            - Errors are logged but not raised - returns None instead
        """
        # 校验并预处理文本
        prepared_text = self._validate_and_prepare_text(text)
        if not prepared_text:
            return None

        try:
            # 调用 OpenAI 文字转语音接口
            response = self.client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=prepared_text,
                response_format="mp3",
            )

            # 从响应中提取音频字节
            audio_bytes = response.content
            logger.info(f"OpenAI TTS: generated {len(audio_bytes)} bytes")
            return audio_bytes

        except Exception as e:
            # 记录包含完整调用栈的错误，方便调试
            logger.error(f"OpenAI TTS failed: {e}", exc_info=True)
            # 失败时返回 None，使应用可以降级继续运行
            return None

    def get_format(self) -> str:
        """Get audio format (MIME type).

        Returns:
            MIME type string for generated audio
        """
        return "audio/mp3"
