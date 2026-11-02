from datetime import date
from pathlib import Path

import pytest
from scripts.check_data_rights import validate, validate_document


@pytest.mark.contract
def test_enabled_sources_have_rights_records() -> None:
    assert validate(Path("configs/sources.yaml")) == []


@pytest.mark.contract
def test_enabled_source_with_pending_or_invalid_rights_fails_closed() -> None:
    document = {
        "sources": [
            {
                "id": "unsafe",
                "authority": 1.2,
                "enabled": True,
                "capabilities": ["news"],
                "data_rights": {
                    "owner": "test",
                    "official_url": "https://example.invalid",
                    "access_method": "api",
                    "license_id": "pending-contract",
                    "expiry": "2020-01-01",
                    "quota": "contract",
                    "cache_allowed": True,
                    "fulltext_allowed": False,
                    "embedding_allowed": True,
                    "training_allowed": False,
                    "redistribution_allowed": False,
                    "retention_days": -1,
                },
            }
        ]
    }

    errors = validate_document(document)
    assert any("authority" in error for error in errors)
    assert any("expired" in error for error in errors)
    assert any("unresolved rights" in error for error in errors)
    assert any("embedding" in error for error in errors)
    assert any("retention_days" in error for error in errors)


@pytest.mark.contract
def test_r1_r6_research_profile_does_not_require_r7_contract_approval() -> None:
    document = {
        "sources": [
            {
                "id": "owner-research-preview",
                "authority": 0.25,
                "enabled": True,
                "capabilities": ["news"],
                "dev_live": {
                    "runtime_mode": "dev_live",
                    "release_tier": "dev_only",
                    "rights_status": "unverified",
                },
                "data_rights": {
                    "owner": "project-owner",
                    "official_url": "https://example.invalid/docs",
                    "access_method": "owner-selected-research-api",
                    "license_id": "pending-r7-review",
                    "expiry": None,
                    "quota": "bounded",
                    "cache_allowed": "allowlisted-fields-only",
                    "fulltext_allowed": False,
                    "embedding_allowed": False,
                    "training_allowed": False,
                    "redistribution_allowed": False,
                    "retention_days": 30,
                },
            }
        ]
    }

    assert validate_document(document) == []


@pytest.mark.contract
@pytest.mark.parametrize(
    ("enabled", "expiry", "expected"),
    [
        (False, "2026-09-19", []),
        (True, "2026-09-19", ["source: rights record is expired"]),
        (True, "2026-10-17", []),
        (False, "invalid", ["source: expiry must be null or ISO-8601 date/datetime"]),
        (False, "2026-09-19T00:00:00", ["source: expiry datetime must include a timezone"]),
        (
            "false",
            "2026-09-19",
            ["source: enabled must be a boolean", "source: rights record is expired"],
        ),
        (
            None,
            "2026-09-19",
            ["source: enabled must be a boolean", "source: rights record is expired"],
        ),
        (
            0,
            "2026-09-19",
            ["source: enabled must be a boolean", "source: rights record is expired"],
        ),
    ],
)
def test_only_explicitly_disabled_sources_may_keep_expired_rights(
    enabled: object, expiry: str, expected: list[str]
) -> None:
    document = {
        "sources": [
            {
                "id": "source",
                "authority": 0.25,
                "enabled": enabled,
                "capabilities": ["news"],
                "data_rights": {
                    "owner": "test",
                    "official_url": "https://example.invalid",
                    "access_method": "api",
                    "license_id": "test-license",
                    "expiry": expiry,
                    "quota": "bounded",
                    "cache_allowed": False,
                    "fulltext_allowed": False,
                    "embedding_allowed": False,
                    "training_allowed": False,
                    "redistribution_allowed": False,
                    "retention_days": 0,
                },
            }
        ]
    }

    assert validate_document(document, today=date(2026, 10, 17)) == expected
