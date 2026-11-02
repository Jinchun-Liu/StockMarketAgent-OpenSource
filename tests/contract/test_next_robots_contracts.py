"""A robots denial is a bounded origin observation, never a content verdict."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from stock_assistant.contracts.discovery import (
    ResearchAcquisitionState,
    RobotsOriginStop,
    SourceDiscoveryResult,
)

NOW = datetime(2026, 9, 15, tzinfo=UTC)


def _stop(origin: str = "https://www.cninfo.com.cn", status: int = 403) -> RobotsOriginStop:
    return RobotsOriginStop.model_validate(
        {"origin": origin, "http_status": status, "observed_at": NOW}
    )


@pytest.mark.parametrize("status", [401, 403, 429])
def test_stop_roundtrip_and_canonical_default_port(status: int) -> None:
    stop = _stop("https://WWW.CNINFO.COM.CN:443/", status)
    assert stop.origin == "https://www.cninfo.com.cn"
    assert RobotsOriginStop.model_validate_json(stop.model_dump_json()) == stop


@pytest.mark.parametrize(
    "origin",
    [
        "http://www.cninfo.com.cn",
        "https://u:p@www.cninfo.com.cn",
        "https://www.cninfo.com.cn:444",
        "https://www.cninfo.com.cn/page",
        "https://www.cninfo.com.cn/?x=1",
        "https://www.cninfo.com.cn/#x",
        "https://",
        "https://[::1]",
    ],
)
def test_stop_rejects_non_origin_urls(origin: str) -> None:
    with pytest.raises(ValidationError):
        _stop(origin)


@pytest.mark.parametrize("status", [200, 404, 410, 500])
def test_stop_cannot_encode_other_responses(status: int) -> None:
    with pytest.raises(ValidationError):
        _stop(status=status)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2"])
def test_discovery_and_state_legacy_empty_observation_compatibility(version: str) -> None:
    result = SourceDiscoveryResult.model_validate(
        dict(
            schema_version=version,
            provider="fixture",
            status="failed",
            request_sha256="a" * 64,
            searched_at=NOW,
        )
    )
    assert result.robots_origin_stop is None
    state = ResearchAcquisitionState.model_validate(
        dict(
            schema_version=version,
            rule_version="legacy",
            instrument_id="XSHG:688307",
            requested_as_of=NOW,
            deadline_at=NOW,
            input_sha256="a" * 64,
        )
    )
    assert state.robots_origin_stops == []
    data = result.model_dump(mode="python") | {"robots_origin_stop": _stop()}
    if version == "1.2":
        assert SourceDiscoveryResult.model_validate(data).robots_origin_stop == _stop()
    else:
        with pytest.raises(ValidationError):
            SourceDiscoveryResult.model_validate(data)
    with pytest.raises(ValidationError):
        SourceDiscoveryResult.model_validate(data | {"status": "no_results"})
    with pytest.raises(ValidationError):
        ResearchAcquisitionState.model_validate(
            state.model_dump(mode="python") | {"robots_origin_stops": [_stop(), _stop()]}
        )


def test_stop_timezone_uniqueness_capacity_and_independent_defaults() -> None:
    with pytest.raises(ValidationError):
        RobotsOriginStop.model_validate(
            _stop().model_dump() | {"observed_at": datetime(2026, 9, 15)}
        )
    data = dict(
        schema_version="1.2",
        rule_version="current",
        instrument_id="XSHG:688307",
        requested_as_of=NOW,
        deadline_at=NOW,
        input_sha256="a" * 64,
    )
    first, second = ResearchAcquisitionState(**data), ResearchAcquisitionState(**data)
    first.robots_origin_stops = [_stop()]
    assert second.robots_origin_stops == []
    with pytest.raises(ValidationError):
        ResearchAcquisitionState.model_validate(
            data | {"robots_origin_stops": [_stop(f"https://source{i}.example") for i in range(16)]}
        )
