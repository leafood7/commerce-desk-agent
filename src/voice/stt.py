"""Speech-to-text factory.

This module provides a factory class that loads the appropriate STT provider
based on configuration.
"""

import logging
import os
from typing import BinaryIO, Literal, cast

logger = logging.getLogger(__name__)

Provider = Literal["openai", "deepgram"]


class SpeechToText:
    """Speech-to-text factory.

    Loads and delegates to specific STT provider implementations.

    Example:
        >>> stt = SpeechToText(provider="openai")
        >>> text = stt.transcribe(audio_file)
        >>>
        >>> # Or from environment
        >>> stt = SpeechToText.from_env()
        >>> if stt:
        ...     text = stt.transcribe(audio_file)
    """

    def __init__(self, provider: Provider = "openai", api_key: str | None = None, **config):
        """Initialize STT with specified provider.

        Args:
            provider: Provider name ("openai", "deepgram", etc.)
            api_key: API key (uses env var if not provided)
            **config: Provider-specific configuration

        Raises:
            ValueError: If provider is unknown
        """
        self._provider_name = provider

        # 从参数或环境变量获取接口密钥
        resolved_api_key = self._get_api_key(provider, api_key)

        # 加载并配置服务商实现
        self._provider = self._load_provider(provider, resolved_api_key, config)

        logger.info(f"SpeechToText created with provider={provider}")

    def _get_api_key(self, provider: Provider, api_key: str | None) -> str | None:
        """Get API key from parameter or environment.

        Args:
            provider: Provider name
            api_key: API key from parameter (takes precedence)

        Returns:
            Resolved API key or None
        """
        # 显式传入了密钥时，优先使用该密钥
        if api_key:
            return api_key

        # 否则根据服务商从环境变量读取密钥
        match provider:
            case "openai":
                return os.getenv("OPENAI_API_KEY")
            case "deepgram":
                return os.getenv("DEEPGRAM_API_KEY")
            case _:
                return None

    def _load_provider(self, provider: Provider, api_key: str | None, config: dict):
        """Load the appropriate STT provider implementation.

        Args:
            provider: Provider name
            api_key: Resolved API key
            config: Provider-specific configuration

        Returns:
            Provider instance

        Raises:
            ValueError: If provider is unknown
            NotImplementedError: If provider not yet implemented
        """
        match provider:
            case "openai":
                from voice.providers.openai_stt import OpenAISTT

                return OpenAISTT(api_key=api_key, **config)

            case "deepgram":
                # 扩展示例：实现 DeepgramSTT 服务商后，取消下面代码的注释即可添加 Deepgram 支持
                # from voice.providers.deepgram_stt import DeepgramSTT
                # return DeepgramSTT(api_key=api_key, **config)
                raise NotImplementedError("Deepgram STT provider not yet implemented")

            case _:
                # 统一处理未知服务商
                raise ValueError(f"Unknown STT provider: {provider}. Available providers: openai")

    @property
    def provider(self) -> str:
        """Get the provider name.

        Returns:
            Provider name string
        """
        return self._provider_name

    @classmethod
    def from_env(cls) -> "SpeechToText | None":
        """Create STT from environment variables.

        Reads VOICE_STT_PROVIDER env var to determine which provider to use.
        Returns None if not configured.

        Returns:
            SpeechToText instance or None

        Example:
            >>> # In .env: VOICE_STT_PROVIDER=openai
            >>> stt = SpeechToText.from_env()
            >>> if stt:
            ...     text = stt.transcribe(audio_file)
        """
        provider = os.getenv("VOICE_STT_PROVIDER")

        # 未指定服务商时，禁用语音功能
        if not provider:
            logger.debug("VOICE_STT_PROVIDER not set, STT disabled")
            return None

        try:
            # 根据环境变量中的服务商名称创建实例
            # 校验服务商，不合法时抛出 ValueError
            return cls(provider=cast(Provider, provider))
        except Exception as e:
            # 只记录错误，不让应用崩溃；允许在没有语音功能时继续运行
            logger.error(f"Failed to create STT provider: {e}", exc_info=True)
            return None

    def transcribe(self, audio_file: BinaryIO) -> str:
        """Transcribe audio to text.

        Delegates to the underlying provider implementation.

        Args:
            audio_file: Binary audio file

        Returns:
            Transcribed text (empty string on failure)
        """
        return self._provider.transcribe(audio_file)
