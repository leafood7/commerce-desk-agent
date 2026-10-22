from enum import StrEnum, auto


class Provider(StrEnum):
    OPENAI = auto()
    OPENAI_COMPATIBLE = auto()
    AZURE_OPENAI = auto()
    DEEPSEEK = auto()
    ANTHROPIC = auto()
    GOOGLE = auto()
    VERTEXAI = auto()
    GROQ = auto()
    AWS = auto()
    OLLAMA = auto()
    OPENROUTER = auto()
    FAKE = auto()


class OpenAIModelName(StrEnum):
    """https://developers.openai.com/api/docs/models"""

    GPT_5_NANO = "gpt-5-nano"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_1 = "gpt-5.1"
    GPT_56_LUNA = "gpt-5.6-luna"
    GPT_56_TERRA = "gpt-5.6-terra"
    GPT_56_SOL = "gpt-5.6-sol"


class AzureOpenAIModelName(StrEnum):
    """https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure"""

    # 这些值也用于 Azure 部署名称和 required_models 的键；改名会破坏现有部署映射
    AZURE_GPT_5 = "azure-gpt-5"
    AZURE_GPT_5_MINI = "azure-gpt-5-mini"


class DeepseekModelName(StrEnum):
    """https://api-docs.deepseek.com/quick_start/pricing"""

    DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"


class AnthropicModelName(StrEnum):
    """https://platform.claude.com/docs/en/docs/about-claude/models"""

    HAIKU_45 = "claude-haiku-4-5"
    SONNET_45 = "claude-sonnet-4-5"
    SONNET_5 = "claude-sonnet-5"


class GoogleModelName(StrEnum):
    """https://ai.google.dev/gemini-api/docs/models/gemini"""

    GEMINI_31_FLASH_LITE = "gemini-3.1-flash-lite"
    GEMINI_35_FLASH = "gemini-3.5-flash"
    GEMINI_35_FLASH_LITE = "gemini-3.5-flash-lite"
    GEMINI_36_FLASH = "gemini-3.6-flash"
    GEMINI_37_FLASH = "gemini-3.7-flash"
    GEMINI_38_FLASH = "gemini-3.8-flash"
    # gemini-3-pro-preview 已于 2026-03-09 关闭；当前预览版 Pro 模型为 3.1
    GEMINI_31_PRO_PREVIEW = "gemini-3.1-pro-preview"


class VertexAIModelName(StrEnum):
    """https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models"""

    # models/ 前缀用于区分与 GoogleModelName 对应成员相同的值；
    # Vertex 会将它解析到与不带前缀名称相同的资源路径
    GEMINI_31_FLASH_LITE = "models/gemini-3.1-flash-lite"
    GEMINI_35_FLASH = "models/gemini-3.5-flash"
    GEMINI_35_FLASH_LITE = "models/gemini-3.5-flash-lite"
    GEMINI_36_FLASH = "models/gemini-3.6-flash"
    GEMINI_37_FLASH = "models/gemini-3.7-flash"
    GEMINI_38_FLASH = "models/gemini-3.8-flash"
    # gemini-3-pro-preview 已于 2026-03-09 关闭；当前预览版 Pro 模型为 3.1
    GEMINI_31_PRO_PREVIEW = "models/gemini-3.1-pro-preview"


class GroqModelName(StrEnum):
    """https://console.groq.com/docs/models"""

    GPT_OSS_20B = "openai/gpt-oss-20b"
    GPT_OSS_120B = "openai/gpt-oss-120b"
    GPT_OSS_SAFEGUARD_20B = "openai/gpt-oss-safeguard-20b"


class AWSModelName(StrEnum):
    """https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html"""

    # 这些值是全局跨区域推理配置编号；新版 Bedrock Claude 不接受直接使用按需模型编号，单区域部署可能需要 us./eu. 前缀
    BEDROCK_HAIKU = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    BEDROCK_SONNET = "global.anthropic.claude-sonnet-5"


class OllamaModelName(StrEnum):
    """https://ollama.com/search"""

    OLLAMA_GENERIC = "ollama"


class OpenRouterModelName(StrEnum):
    """https://openrouter.ai/models"""

    GEMINI_35_FLASH = "google/gemini-3.5-flash"
    GEMINI_36_FLASH = "google/gemini-3.6-flash"
    GEMINI_37_FLASH = "google/gemini-3.7-flash"
    GEMINI_38_FLASH = "google/gemini-3.8-flash"


class OpenAICompatibleName(StrEnum):
    """https://platform.openai.com/docs/guides/text-generation"""

    OPENAI_COMPATIBLE = "openai-compatible"


class FakeModelName(StrEnum):
    """Fake model for testing."""

    FAKE = "fake"


type AllModelEnum = (
    OpenAIModelName
    | OpenAICompatibleName
    | AzureOpenAIModelName
    | DeepseekModelName
    | AnthropicModelName
    | GoogleModelName
    | VertexAIModelName
    | GroqModelName
    | AWSModelName
    | OllamaModelName
    | OpenRouterModelName
    | FakeModelName
)
