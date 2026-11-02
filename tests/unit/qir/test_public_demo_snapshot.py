from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from scripts.public_demo_snapshot import FIXED_AS_OF, PublicSnapshotError, export_public_snapshot

_FIXTURE_ROOT = Path("tests/contract/arc/fixtures/r1/valid")
_EVENT_ID = "00000000-0000-0000-0000-000000001001"


def _provenance() -> dict[str, object]:
    return {
        "schema_version": "1.2",
        "provider": "fixture",
        "access_channel": "demo_replay",
        "adapter_name": "r1_demo_replay",
        "adapter_version": "demo-replay-v2",
        "endpoint_name": "statutory_announcements",
        "source_identifier": "synthetic-record",
        "runtime_mode": "demo_replay",
        "release_tier": "dev_only",
        "rights_status": "not_applicable",
        "quality_tier": "demo_replay",
        "evidence_role": "authoritative",
    }


def _envelopes() -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    meta = {
        "as_of": "2026-07-31T00:55:00Z",
        "correlation_id": "00000000-0000-4000-8000-000000000001",
        "request_id": "00000000-0000-4000-8000-000000000002",
        "generated_at": "2026-08-02T10:00:00Z",
        "contract_version": "1.0",
    }
    instrument = {
        "instrument_id": "XSHG:603986",
        "symbol": "603986.SH",
        "exchange": "SSE",
        "market": "CN",
        "asset_class": "equity",
        "currency": "CNY",
    }
    active = {
        "data": {
            "schema_version": "1.0",
            "found": True,
            "as_of": "2026-08-02T10:00:00Z",
            "active_target": {
                "revision": 1,
                "activated_at": "2026-08-02T10:00:00Z",
                "instrument": instrument,
            },
        },
        "meta": copy.deepcopy(meta),
    }
    briefing_data = json.loads(
        (_FIXTURE_ROOT / "premarket-briefing.v1.0.json").read_text(encoding="utf-8")
    )
    briefing_data["schema_version"] = "1.2"
    briefing_data["runtime_mode"] = "demo_replay"
    briefing_data["release_tier"] = "dev_only"
    briefing_data["instrument"] = instrument
    briefing_data["ingestion_progress"]["provenance"] = _provenance()
    for coverage in briefing_data["source_coverage"]:
        coverage["provenance"] = _provenance()
    briefing = {"data": briefing_data, "meta": copy.deepcopy(meta)}
    scenarios = json.loads(
        (_FIXTURE_ROOT / "event-evidence-scenarios.v1.0.json").read_text(encoding="utf-8")
    )
    evidence_by_event_id: dict[str, dict[str, object]] = {}
    for scenario in scenarios:
        evidence_data = scenario["payload"]
        evidence_data["schema_version"] = "1.2"
        evidence_data["runtime_mode"] = "demo_replay"
        evidence_data["instrument_ids"] = ["XSHG:603986"]
        for item in evidence_data["evidence"]:
            item["event_time"] = item["published_at"]
            item["provenance"] = _provenance()
        event_id = evidence_data["event_id"]
        evidence_by_event_id[event_id] = {
            "data": evidence_data,
            "meta": copy.deepcopy(meta),
        }
    return active, briefing, evidence_by_event_id


def test_export_public_snapshot_is_deterministic_and_redacted(tmp_path: Path) -> None:
    active, briefing, evidence = _envelopes()
    output = tmp_path / "snapshot"
    metadata = export_public_snapshot(
        active_target=active,
        latest_briefing=briefing,
        evidence_by_event_id=evidence,
        product_sha="a" * 40,
        output_dir=output,
    )
    assert metadata["generated_at"] == "2026-07-31T01:05:00Z"
    assert metadata["product_commit"] == "a" * 40
    rendered = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(output.rglob("*.json"))
    )
    assert "fixture.invalid" not in rendered
    assert '"runtime_mode": "public_static"' in rendered
    assert '"rights_status": "not_applicable"' in rendered
    assert '"release_tier": "dev_only"' in rendered
    assert "2026-08-02T10:00:00Z" not in rendered
    public_evidence = json.loads(
        (output / "evidence" / f"{_EVENT_ID}.json").read_text(encoding="utf-8")
    )
    assert public_evidence["data"]["as_of"] == FIXED_AS_OF
    index = json.loads((output / "evidence-index.json").read_text())
    assert len(index["events"]) == 5
    assert {item["event_id"] for item in index["events"]} == set(evidence)


def test_export_requires_exact_evidence_set_and_empty_output(tmp_path: Path) -> None:
    active, briefing, evidence = _envelopes()
    with pytest.raises(PublicSnapshotError, match="exactly match"):
        export_public_snapshot(
            active_target=active,
            latest_briefing=briefing,
            evidence_by_event_id={},
            product_sha="a" * 40,
            output_dir=tmp_path / "missing",
        )
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "old.json").write_text("{}")
    with pytest.raises(PublicSnapshotError, match="must be empty"):
        export_public_snapshot(
            active_target=active,
            latest_briefing=briefing,
            evidence_by_event_id=evidence,
            product_sha="a" * 40,
            output_dir=occupied,
        )


def test_export_rejects_unknown_secret_field_and_wrong_target(tmp_path: Path) -> None:
    active, briefing, evidence = _envelopes()
    evidence_data = evidence[_EVENT_ID]["data"]
    assert isinstance(evidence_data, dict)
    evidence_data["api_key"] = "forbidden"
    with pytest.raises(PublicSnapshotError, match="strict API envelope"):
        export_public_snapshot(
            active_target=active,
            latest_briefing=briefing,
            evidence_by_event_id=evidence,
            product_sha="a" * 40,
            output_dir=tmp_path / "body",
        )
    active, briefing, evidence = _envelopes()
    active_data = active["data"]
    assert isinstance(active_data, dict)
    target = active_data["active_target"]
    assert isinstance(target, dict)
    instrument = target["instrument"]
    assert isinstance(instrument, dict)
    instrument["instrument_id"] = "XSHE:000001"
    with pytest.raises(PublicSnapshotError, match="fixed R1 target"):
        export_public_snapshot(
            active_target=active,
            latest_briefing=briefing,
            evidence_by_event_id=evidence,
            product_sha="a" * 40,
            output_dir=tmp_path / "wrong-target",
        )


def test_export_rebuilds_private_urls_and_identifiers_from_allowlist(tmp_path: Path) -> None:
    active, briefing, evidence = _envelopes()
    first = evidence[_EVENT_ID]["data"]
    assert isinstance(first, dict)
    sources = first["evidence"]
    assert isinstance(sources, list)
    source = sources[0]
    assert isinstance(source, dict)
    source["source_url"] = "http://127.0.0.1/private"
    provenance = source["provenance"]
    assert isinstance(provenance, dict)
    provenance["source_identifier"] = "https://private.example/account/secret"

    output = tmp_path / "private"
    export_public_snapshot(
        active_target=active,
        latest_briefing=briefing,
        evidence_by_event_id=evidence,
        product_sha="a" * 40,
        output_dir=output,
    )
    rendered = (output / "evidence" / f"{_EVENT_ID}.json").read_text(encoding="utf-8")
    assert "127.0.0.1" not in rendered
    assert "private.example" not in rendered
    assert "public-static:evidence:" in rendered
