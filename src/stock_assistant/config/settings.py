"""Typed settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from stock_assistant.contracts.ingestion import SourceRuntimeMode


class Settings(BaseSettings):
    """Runtime configuration shared by API, worker and scheduler."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "preview", "production"] = "development"
    app_name: str = "StockMarketAgent"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    timezone: str = "Asia/Shanghai"
    api_host: str = "0.0.0.0"  # noqa: S104 - container listener is intentional
    api_port: int = Field(default=8000, ge=1, le=65535)
    web_origin: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://stock_assistant:change-me@localhost:5432/stock_assistant"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    celery_broker_url: SecretStr = SecretStr("redis://localhost:6379/1")
    celery_result_backend: SecretStr = SecretStr("redis://localhost:6379/2")

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "stock-assistant"
    minio_secret_key: SecretStr = SecretStr("change-me-too")
    minio_secure: bool = False
    minio_evidence_bucket: str = "evidence"
    minio_artifact_bucket: str = "artifacts"

    local_llm_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8100/v1")
    local_llm_api_key: SecretStr = SecretStr("local-only")
    local_llm_model: str = "Qwen/Qwen3.5-9B"
    local_embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    local_reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"

    external_llm_enabled: bool = False
    external_llm_base_url: AnyHttpUrl | None = None
    external_llm_api_key: SecretStr | None = None
    external_llm_model: str | None = None

    deepseek_enabled: bool = False
    deepseek_base_url: Literal["https://api.deepseek.com"] = "https://api.deepseek.com"
    deepseek_api_key: SecretStr | None = None
    deepseek_flash_model: Literal["deepseek-v4-flash"] = "deepseek-v4-flash"
    deepseek_pro_model: Literal["deepseek-v4-pro"] = "deepseek-v4-pro"
    enable_r2_deepseek_research: bool = False
    enable_r2_web_fallback: bool = False
    # Deployment caps only; provider commercial/account quotas remain external.
    r2_deepseek_concurrency: int = Field(default=2, ge=1, le=8)
    r2_tavily_concurrency: int = Field(default=1, ge=1, le=4)
    research_worker_concurrency: int = Field(default=2, ge=1, le=8)
    tavily_base_url: Literal["https://api.tavily.com"] = "https://api.tavily.com"
    tavily_api_key: SecretStr | None = None

    allow_external_holding_data: bool = False
    enable_social_source: bool = False
    enable_directional_forecast: bool = False
    enable_desktop_notifications: bool = True
    enable_r1_fixture: bool = False
    r1_runtime_mode: SourceRuntimeMode = SourceRuntimeMode.DEMO_REPLAY
    enable_r1_dev_live: bool = False
    enable_r2_synthetic_research: bool = False
    enable_r2_real_research: bool = False
    enable_r2_private_preview: bool = False
    cognito_issuer: AnyHttpUrl | None = None
    cognito_client_id: str | None = Field(default=None, min_length=1, max_length=128)
    cognito_jwks_url: AnyHttpUrl | None = None
    cognito_jwks_proxy_url: AnyHttpUrl | None = None
    cognito_username_claim: Literal["username", "cognito:username"] = "username"
    r2_artifact_bucket: str | None = Field(
        default=None,
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$",
    )
    r1_research_data_api_token: SecretStr | None = None
    r1_research_data_api_base_url: Literal["https://quantdata888.duckdns.org"] = (
        "https://quantdata888.duckdns.org"
    )
    r1_promax_api_key: SecretStr | None = None
    r1_promax_api_base_url: Literal["https://pcd.mobcvb.cn/tushare/pro/"] = (
        "https://pcd.mobcvb.cn/tushare/pro/"
    )
    r1_qinghong_api_key: SecretStr | None = None
    r1_qinghong_api_base_url: AnyHttpUrl | None = None
    r1_fixture_fail_source: Literal["cninfo_data_service", "choice_quant_api"] | None = None
    r1_demo_replay_scenario: Literal[
        "success",
        "no_change",
        "source_failure",
        "rights_blocked",
        "media_unconfirmed",
        "macro_insufficient",
    ] = "success"
    r1_demo_replay_capability: (
        Literal[
            "statutory.announcements",
            "financial.changes",
            "market.snapshot",
            "company_industry.news",
            "macro.flashes",
        ]
        | None
    ) = None
    aws_region: str = Field(default="ap-southeast-1", pattern=r"^[a-z]{2}-[a-z]+-\d$")
    public_snapshot_bucket: str | None = Field(
        default=None,
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$",
    )
    public_base_url: str | None = Field(
        default=None,
        max_length=253,
        pattern=r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?/?$",
    )
    product_commit: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    snapshot_freshness_grace_minutes: int = Field(default=15, ge=1, le=180)
    snapshot_advisory_lock_id: int = Field(default=1_105_503_150, ge=1)
    public_smoke_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def private_preview_requires_complete_runtime_configuration(self) -> "Settings":
        if not self.enable_r2_private_preview:
            return self
        if (
            self.app_env != "preview"
            or self.r2_artifact_bucket is None
            or self.product_commit is None
            or self.aws_region != "ap-northeast-1"
            or (self.enable_r2_deepseek_research and self.deepseek_api_key is None)
            or (self.enable_r2_web_fallback and self.tavily_api_key is None)
            or (self.enable_r2_web_fallback and not self.enable_r2_deepseek_research)
            or (self.enable_r2_real_research and self.enable_r2_synthetic_research)
        ):
            raise ValueError("R2 private preview requires complete runtime configuration")
        return self

    @model_validator(mode="after")
    def replay_scenario_requires_one_compatible_capability(self) -> "Settings":
        """Keep deterministic replay faults explicit, bounded, and development-only."""

        scenario = self.r1_demo_replay_scenario
        capability = self.r1_demo_replay_capability
        if (scenario == "success") != (capability is None):
            raise ValueError(
                "R1 demo replay capability is required exactly for a non-success scenario"
            )
        compatible = {
            "media_unconfirmed": "company_industry.news",
            "macro_insufficient": "macro.flashes",
        }
        if scenario in compatible and capability != compatible[scenario]:
            raise ValueError(f"{scenario} requires capability {compatible[scenario]}")
        return self

    @model_validator(mode="after")
    def preview_requires_the_explicit_dev_live_profile(self) -> "Settings":
        """Keep the deployable preview distinct from development and production."""

        if self.app_env != "preview" or self.enable_r2_private_preview:
            return self
        if (
            self.enable_r1_fixture
            or not self.enable_r1_dev_live
            or self.r1_runtime_mode is not SourceRuntimeMode.DEV_LIVE
            or self.public_snapshot_bucket is None
            or self.public_base_url is None
            or self.product_commit is None
            or self.external_llm_enabled
            or self.enable_social_source
            or self.enable_directional_forecast
        ):
            raise ValueError("preview requires the bounded DEV_LIVE snapshot profile")
        return self

    @model_validator(mode="after")
    def qinghong_requires_the_fixed_encrypted_origin(self) -> "Settings":
        """Never transmit the provider credential over its documented HTTP origin."""

        if self.r1_qinghong_api_base_url is None:
            return self
        try:
            parsed = urlsplit(str(self.r1_qinghong_api_base_url))
            port = parsed.port
        except ValueError as error:
            raise ValueError("Qinghong API base URL is invalid") from error
        if (
            parsed.scheme != "https"
            or parsed.hostname != "124.223.180.129"
            or port != 3220
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Qinghong API requires the fixed HTTPS origin")
        return self

    def assert_safe_production(self) -> None:
        """Fail fast on unsafe production defaults."""

        if self.app_env != "production":
            return
        if (
            self.enable_r1_fixture
            or self.enable_r1_dev_live
            or self.enable_r2_synthetic_research
            or self.enable_r2_real_research
            or self.enable_r2_private_preview
            or self.r1_runtime_mode is not SourceRuntimeMode.AUTHORIZED
            or self.r1_fixture_fail_source is not None
            or self.r1_demo_replay_scenario != "success"
            or self.r1_demo_replay_capability is not None
        ):
            raise ValueError("R1 development source modes cannot be enabled in production")
        unsafe_fragments = ("change-me", "localhost")
        values = (
            self.database_url.get_secret_value(),
            self.minio_secret_key.get_secret_value(),
        )
        if any(fragment in value for fragment in unsafe_fragments for value in values):
            raise ValueError("production configuration still contains development credentials")
        if self.external_llm_enabled and not self.external_llm_api_key:
            raise ValueError("external LLM is enabled without an API key")
        if self.deepseek_enabled and not self.deepseek_api_key:
            raise ValueError("DeepSeek is enabled without an API key")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""

    return Settings()
