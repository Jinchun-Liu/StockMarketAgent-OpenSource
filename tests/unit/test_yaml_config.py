import pytest

from stock_assistant.config.yaml_config import load_versioned_config


def test_versioned_config_exposes_version_and_hash() -> None:
    config = load_versioned_config("configs/models.yaml")
    assert config.version == "2026-08-07.1"
    assert len(config.content_hash) == 64


def test_versioned_config_rejects_path_escape() -> None:
    with pytest.raises(ValueError, match="inside the project"):
        load_versioned_config("../outside.yaml")
