from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class WorkbenchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_", env_file=ROOT / ".env", extra="ignore"
    )
    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr("sqlite:///data/workbench/business.sqlite3")
    mcp_url: str = "http://127.0.0.1:8083/mcp"
    mcp_bind_host: str = "127.0.0.1"
    mcp_port: int = 8083
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "testserver"]
    mcp_warm_models: bool = True
    mcp_timeout_seconds: float = 30
    mcp_read_retries: int = Field(default=1, ge=0, le=2)
    mcp_retry_delay_seconds: float = Field(default=0.5, ge=0, le=5)
    agent_active_seconds: float = Field(default=120, ge=1, le=300)
    agent_tool_limit: int = Field(default=10, ge=1, le=30)
    queue_owner_limit: int = Field(default=16, ge=1, le=256)
    queue_global_limit: int = Field(default=128, ge=1, le=4096)
    run_tool_limit: int = Field(default=40, ge=1, le=1000)
    run_model_token_limit: int = Field(default=16384, ge=256, le=1000000)
    run_model_reserve_tokens: int = Field(default=8192, ge=256, le=32768)
    chroma_url: str = "http://127.0.0.1:8005"
    storage_root: str = "data/workbench/files"
    upload_limit_bytes: int = 20 * 1024 * 1024
    context_chars: int = 4000
    retrieval_context_mode: Literal["neighbors", "section"] = "neighbors"
    vector_max_distance: float = 0.65
    rerank_threshold: float = -2.0
    run_dispatch_mode: Literal["inline", "worker"] = "inline"
    agent_router_max_output_tokens: Literal[128, 256] = 256
    agent_router_mode: Literal["rules", "model"] = "rules"
    agent_router_url: str | None = None
    agent_router_model: str | None = None
    agent_router_key: SecretStr | None = None
    session_minutes: int = 480
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    def model_post_init(self, __context):
        if not 5 <= self.session_minutes <= 1440:
            raise ValueError("Session lifetime must be between 5 and 1440 minutes")
        from urllib.parse import urlparse

        if self.agent_router_mode == "model":
            endpoint = urlparse(self.agent_router_url or "")
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username
                or endpoint.fragment
                or not self.agent_router_model
                or not self.agent_router_key
            ):
                raise ValueError(
                    "Model routing requires an explicit HTTPS chat-completions endpoint, model and key"
                )

        for endpoint in (self.mcp_url, self.chroma_url):
            parsed = urlparse(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.fragment
            ):
                raise ValueError(
                    "Service endpoint must be a configured HTTP(S) URL without credentials"
                )
        if self.environment == "production" and not self.mcp_url.startswith("https://"):
            raise ValueError("Production MCP transport requires HTTPS")
        if not 1 <= self.mcp_timeout_seconds <= 120:
            raise ValueError("MCP timeout must be between 1 and 120 seconds")
        url = self.database_url.get_secret_value()
        if not url.startswith(("sqlite:///", "mysql+pymysql://")):
            raise ValueError("Use sqlite:/// for development or mysql+pymysql://")
        if self.environment == "production" and not url.startswith("mysql+pymysql://"):
            raise ValueError("Production business storage requires MySQL; no SQLite fallback")
