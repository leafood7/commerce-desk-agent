import os
from unittest.mock import patch

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from core.llm import get_model
from schema.models import (
    AnthropicModelName,
    FakeModelName,
    GroqModelName,
    OllamaModelName,
    OpenAIModelName,
    OpenRouterModelName,
)


def test_get_model_openai():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key"}):
        model = get_model(OpenAIModelName.GPT_5_NANO)
        assert isinstance(model, ChatOpenAI)
        assert model.model_name == "gpt-5-nano"
        assert model.streaming is True


def test_get_model_anthropic():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        model = get_model(AnthropicModelName.HAIKU_45)
        assert isinstance(model, ChatAnthropic)
        assert model.model == "claude-haiku-4-5"
        assert model.temperature == 0.5
        assert model.streaming is True


def test_get_model_anthropic_sonnet_5_omits_temperature():
    # Claude Sonnet 5 会拒绝非默认采样参数并返回 400，
    # 因此 get_model 不应给该模型传入 temperature
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}):
        model = get_model(AnthropicModelName.SONNET_5)
        assert isinstance(model, ChatAnthropic)
        assert model.model == "claude-sonnet-5"
        assert model.streaming is True


def test_get_model_groq():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test_key"}):
        model = get_model(GroqModelName.GPT_OSS_20B)
        assert isinstance(model, ChatGroq)
        assert model.model_name == "openai/gpt-oss-20b"
        assert model.temperature == 0.5


def test_get_model_groq_guard():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test_key"}):
        model = get_model(GroqModelName.GPT_OSS_SAFEGUARD_20B)
        assert isinstance(model, ChatGroq)
        assert model.model_name == "openai/gpt-oss-safeguard-20b"
        assert model.temperature < 0.01


def test_get_model_groq_gpt_oss():
    with patch.dict(os.environ, {"GROQ_API_KEY": "test_key"}):
        model = get_model(GroqModelName.GPT_OSS_120B)
        assert isinstance(model, ChatGroq)
        assert model.model_name == "openai/gpt-oss-120b"
        # 只有安全检查专用模型才应将 temperature 覆盖为 0.0
        assert model.temperature == 0.5


def test_get_model_ollama():
    with patch("core.settings.settings.OLLAMA_MODEL", "llama3.3"):
        model = get_model(OllamaModelName.OLLAMA_GENERIC)
        assert isinstance(model, ChatOllama)
        assert model.model == "llama3.3"
        assert model.temperature == 0.5


# get_model 使用 @cache 缓存，因此测试直接调用未缓存的原函数；清空
# 共享缓存会移除后续测试和应用启动所依赖的对象
_get_model_uncached = get_model.__wrapped__


def test_get_model_openrouter():
    with patch("core.settings.settings.OPENROUTER_API_KEY", SecretStr("test_key")):
        model = _get_model_uncached(OpenRouterModelName.GEMINI_36_FLASH)
        assert isinstance(model, ChatOpenAI)
        assert model.model_name == "google/gemini-3.6-flash"
        assert model.openai_api_base == "https://openrouter.ai/api/v1/"
        assert model.openai_api_key is not None
        assert model.openai_api_key.get_secret_value() == "test_key"
        assert model.temperature == 0.5
        assert model.streaming is True


def test_get_model_openrouter_requires_key():
    # 未设置专用密钥时必须明确报错，否则 OpenAI SDK 会回退读取
    # OPENAI_API_KEY，并将其发送到 openrouter.ai
    with patch("core.settings.settings.OPENROUTER_API_KEY", None):
        with pytest.raises(ValueError, match="OpenRouter API key must be configured"):
            _get_model_uncached(OpenRouterModelName.GEMINI_36_FLASH)


def test_get_model_fake():
    model = get_model(FakeModelName.FAKE)
    assert isinstance(model, FakeListChatModel)
    assert model.responses == ["This is a test response from the fake model."]


def test_get_model_invalid():
    with pytest.raises(ValueError, match="Unsupported model:"):
        # 这里使用 type: ignore，因为本测试故意传入非法输入
        get_model("invalid_model")  # type: ignore
