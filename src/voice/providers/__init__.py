"""Voice provider implementations."""

from voice.providers.openai_stt import OpenAISTT
from voice.providers.openai_tts import OpenAITTS

# 未来可以在这里导入其他服务商实现：
# from voice.providers.deepgram_stt import DeepgramSTT
# from voice.providers.elevenlabs_tts import ElevenLabsTTS

__all__ = ["OpenAISTT", "OpenAITTS"]
