"""OpenAI Whisper speech-to-text implementation."""

import logging
from typing import BinaryIO

from openai import OpenAI

logger = logging.getLogger(__name__)


class OpenAISTT:
    """OpenAI Whisper STT provider."""

    def __init__(self, api_key: str | None = None):
        """Initialize OpenAI STT.

        Args:
            api_key: OpenAI API key (uses env var if not provided)

        Raises:
            Exception: If OpenAI client initialization fails
        """
        # 使用传入的密钥或环境变量创建 OpenAI 客户端
        self.client = OpenAI(api_key=api_key) if api_key else OpenAI()
        logger.info("OpenAI STT initialized")

    def transcribe(self, audio_file: BinaryIO) -> str:
        """Transcribe audio using OpenAI Whisper.

        Args:
            audio_file: Binary audio file

        Returns:
            Transcribed text (empty string on failure)

        Note:
            Errors are logged but not raised - returns empty string instead.
            This allows graceful degradation in user-facing applications.
        """
        try:
            # 将文件指针移回开头，因为其他代码可能已经读取过文件
            audio_file.seek(0)

            # 调用 OpenAI Whisper 接口进行语音转写
            result = self.client.audio.transcriptions.create(
                model="whisper-1", file=audio_file, response_format="text"
            )

            # 清理结果两侧的空白字符
            transcribed = result.strip()
            logger.info(f"OpenAI STT: transcribed {len(transcribed)} chars")
            return transcribed

        except Exception as e:
            # 记录包含完整调用栈的错误，方便调试
            logger.error(f"OpenAI STT failed: {e}", exc_info=True)
            # 失败时返回空字符串，使应用可以降级继续运行
            return ""
