import pytest
from pydantic import SecretStr

from stock_assistant.config.settings import Settings, get_settings
from stock_assistant.contracts.ingestion import SourceRuntimeMode


def test_development_defaults_are_loadable() -> None:
    settings = Settings(_env_file=None)
    settings.assert_safe_production()
    assert settings.app_env == "development"
    assert settings.enable_r1_fixture is False
    assert settings.r1_runtime_mode is SourceRuntimeMode.DEMO_REPLAY
    assert settings.enable_r1_dev_live is False
    assert settings.enable_r2_synthetic_research is False
    assert settings.r1_demo_replay_scenario == "success"
    assert settings.r1_demo_replay_capability is None
    assert settings.r1_research_data_api_token is None
    assert settings.r1_promax_api_key is None
    assert settings.r1_qinghong_api_key is None
    assert settings.r1_qinghong_api_base_url is None


def test_research_data_credentials_are_secret_and_base_urls_are_fixed() -> None:
    settings = Settings(
        r1_research_data_api_token=SecretStr("research-token-sentinel"),
        r1_promax_api_key=SecretStr("promax-key-sentinel"),
        _env_file=None,
    )

    assert "research-token-sentinel" not in repr(settings)
    assert "promax-key-sentinel" not in repr(settings)
    assert str(settings.r1_research_data_api_base_url) == "https://quantdata888.duckdns.org"
    assert str(settings.r1_promax_api_base_url) == "https://pcd.mobcvb.cn/tushare/pro/"

    with pytest.raises(ValueError):
        Settings(
            r1_research_data_api_base_url="https://example.invalid/api",
            _env_file=None,
        )
    with pytest.raises(ValueError):
        Settings(
            r1_promax_api_base_url="https://example.invalid/tushare/pro/",
            _env_file=None,
        )


def test_qinghong_key_is_secret_and_plaintext_origin_is_rejected() -> None:
    settings = Settings(
        r1_qinghong_api_key=SecretStr("qinghong-key-sentinel"),
        _env_file=None,
    )

    assert "qinghong-key-sentinel" not in repr(settings)

    with pytest.raises(ValueError, match="HTTPS"):
        Settings(
            r1_qinghong_api_base_url="http://124.223.180.129:3220",
            _env_file=None,
        )

    secure = Settings(
        r1_qinghong_api_base_url="https://124.223.180.129:3220",
        _env_file=None,
    )
    assert str(secure.r1_qinghong_api_base_url) == "https://124.223.180.129:3220/"


def test_non_success_replay_scenario_requires_one_capability() -> None:
    with pytest.raises(ValueError, match="capability is required"):
        Settings(
            r1_demo_replay_scenario="source_failure",
            _env_file=None,
        )


def test_special_replay_scenario_requires_its_allowlisted_capability() -> None:
    with pytest.raises(ValueError, match=r"company_industry\.news"):
        Settings(
            r1_demo_replay_scenario="media_unconfirmed",
            r1_demo_replay_capability="financial.changes",
            _env_file=None,
        )


def test_production_rejects_placeholder_secrets() -> None:
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.AUTHORIZED,
        _env_file=None,
    )
    with pytest.raises(ValueError, match="development credentials"):
        settings.assert_safe_production()


def test_production_rejects_enabled_r1_fixture_before_other_runtime_checks() -> None:
    settings = Settings(
        app_env="production",
        enable_r1_fixture=True,
        database_url=SecretStr("postgresql+asyncpg://app:secret@db:5432/app"),
        minio_secret_key=SecretStr("non-placeholder-secret"),
        _env_file=None,
    )
    with pytest.raises(ValueError, match="development source modes"):
        settings.assert_safe_production()


def test_production_rejects_dev_live_mode() -> None:
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.DEV_LIVE,
        enable_r1_dev_live=True,
        database_url=SecretStr("postgresql+asyncpg://app:secret@db:5432/app"),
        minio_secret_key=SecretStr("non-placeholder-secret"),
        _env_file=None,
    )
    with pytest.raises(ValueError, match="development source modes"):
        settings.assert_safe_production()


def test_production_rejects_replay_fault_injection() -> None:
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.AUTHORIZED,
        r1_demo_replay_scenario="source_failure",
        r1_demo_replay_capability="financial.changes",
        database_url=SecretStr("postgresql+asyncpg://app:secret@db:5432/app"),
        minio_secret_key=SecretStr("non-placeholder-secret"),
        _env_file=None,
    )
    with pytest.raises(ValueError, match="development source modes"):
        settings.assert_safe_production()


def test_production_rejects_external_llm_without_key() -> None:
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.AUTHORIZED,
        database_url=SecretStr("postgresql+asyncpg://app:secret@db:5432/app"),
        minio_secret_key=SecretStr("non-placeholder-secret"),
        external_llm_enabled=True,
        _env_file=None,
    )
    with pytest.raises(ValueError, match="without an API key"):
        settings.assert_safe_production()


def test_production_accepts_replaced_secrets() -> None:
    settings = Settings(
        app_env="production",
        r1_runtime_mode=SourceRuntimeMode.AUTHORIZED,
        database_url=SecretStr("postgresql+asyncpg://app:secret@db:5432/app"),
        minio_secret_key=SecretStr("non-placeholder-secret"),
        _env_file=None,
    )
    settings.assert_safe_production()


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()


def test_private_r2_preview_requires_complete_shared_runtime_configuration() -> None:
    settings = Settings(
        app_env="preview",
        enable_r2_synthetic_research=True,
        enable_r2_private_preview=True,
        r2_artifact_bucket="stockmarketagent-r2-artifacts-preview",
        product_commit="a" * 40,
        aws_region="ap-northeast-1",
        _env_file=None,
    )

    assert settings.enable_r2_private_preview is True
    assert settings.cognito_jwks_proxy_url is None
    with pytest.raises(ValueError, match="complete runtime"):
        Settings(
            app_env="preview",
            enable_r2_synthetic_research=True,
            enable_r2_private_preview=True,
            product_commit="a" * 40,
            _env_file=None,
        )


def test_production_rejects_private_r2_preview() -> None:
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            enable_r2_synthetic_research=True,
            enable_r2_private_preview=True,
            _env_file=None,
        )
