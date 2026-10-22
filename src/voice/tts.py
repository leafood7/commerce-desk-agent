"""Text-to-speech factory.

This module provides a factory class that loads the appropriate TTS provider
based on configuration.
"""

import logging
import os
from typing import Literal, cast

logger = logging.getLogger(__name__)

Provider = Literal["openai", "elevenlabs"]


class TextToSpeech:
    """Text-to-speech factory.

    Loads and delegates to specific TTS provider implementations.

    Example:
        >>> tts = TextToSpeech(provider="openai", voice="nova")
        >>> audio = tts.generate("Hello world")
        >>>
        >>> # Or from environment
        >>> tts = TextToSpeech.from_env()
        >>> if tts:
        ...     audio = tts.generate("Hello world")
    """

    def __init__(self, provider: Provider = "openai", api_key: str | None = None, **config):
        """Initialize TTS with specified provider.

        Args:
            provider: Provider name ("openai", "elevenlabs", etc.)
            api_key: API key (uses env var if not provided)
            **config: Provider-specific configuration
                OpenAI: voice="alloy", model="tts-1"
                ElevenLabs: voice_id="...", model_id="..."

        Raises:
            ValueError: If provider is unknown
        """
        self._provider_name = provider

        # 从参数或环境变量获取接口密钥
        resolved_api_key = self._get_api_key(provider, api_key)

        # 加载并配置服务商实现
        self._provider = self._load_provider(provider, resolved_api_key, config)

        logger.info(f"TextToSpeech created with provider={provider}")

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
            case "elevenlabs":
                return os.getenv("ELEVENLABS_API_KEY")
            case _:
                return None

    def _load_provider(self, provider: Provider, api_key: str | None, config: dict):
        """Load the appropriate TTS provider implementation.

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
                from voice.providers.openai_tts import OpenAITTS

                # 读取 OpenAI 专用配置，并提供默认值
                voice = config.get("voice", "alloy")
                model = config.get("model", "tts-1")

                return OpenAITTS(api_key=api_key, voice=voice, model=model)

            case "elevenlabs":
                # 扩展示例：实现 ElevenLabsTTS 服务商后，取消下面代码的注释即可添加 ElevenLabs 支持
                # from voice.providers.elevenlabs_tts import ElevenLabsTTS
                # voice_id = config.get("voice_id")
                # model_id = config.get("model_id", "eleven_monolingual_v1")
                # return ElevenLabsTTS(api_key=api_key, voice_id=voice_id, model_id=model_id)
                raise NotImplementedError("ElevenLabs TTS provider not yet implemented")

            case _:
                # 统一处理未知服务商
                raise ValueError(f"Unknown TTS provider: {provider}. Available providers: openai")

    @property
    def provider(self) -> str:
        """Get the provider name.

        Returns:
            Provider name string
        """
        return self._provider_name

    @classmethod
    def from_env(cls) -> "TextToSpeech | None":
        """Create TTS from environment variables.

        Reads VOICE_TTS_PROVIDER env var to determine which provider to use.
        Returns None if not configured.

        Returns:
            TextToSpeech instance or None

        Example:
            >>> # In .env: VOICE_TTS_PROVIDER=openai
            >>> tts = TextToSpeech.from_env()
            >>> if tts:
            ...     audio = tts.generate("Hello world")
        """
        provider = os.getenv("VOICE_TTS_PROVIDER")

        # 未指定服务商时，禁用语音功能
        if not provider:
            logger.debug("VOICE_TTS_PROVIDER not set, TTS disabled")
            return None

        try:
            # 根据环境变量中的服务商名称创建实例
            # 校验服务商，不合法时抛出 ValueError
            return cls(provider=cast(Provider, provider))
        except Exception as e:
            # 只记录错误，不让应用崩溃；允许在没有语音功能时继续运行
            logger.error(f"Failed to create TTS provider: {e}", exc_info=True)
            return None

    def generate(self, text: str) -> bytes | None:
        """Generate speech from text.

        Delegates to the underlying provider implementation.

        Args:
            text: Text to convert to speech

        Returns:
            Audio bytes (format depends on provider), or None on failure
        """
        return self._provider.generate(text)

    def get_format(self) -> str:
        """Get audio format (MIME type) for this provider.

        Returns:
            MIME type string (e.g., "audio/mp3")
        """
        return self._provider.get_format()
