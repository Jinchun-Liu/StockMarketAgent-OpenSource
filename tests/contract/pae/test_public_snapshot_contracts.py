import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import ValidationError

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.ingestion import (
    SourceIngestionLease,
    SourceIngestionReadState,
)
from stock_assistant.contracts.public_snapshots import (
    PUBLIC_SNAPSHOT_ASSET_CONTRACTS,
    PublicEvidenceIndexV1,
    PublicRunStatusV1,
    PublicRunStatusV1_1,
    PublicSnapshotAssetKind,
    PublicSnapshotManifestV1_1,
    PublicSnapshotPointerV1,
    validate_public_snapshot_bundle,
)

SNAPSHOT_ID = UUID("10000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 4, 1, 5, tzinfo=UTC)
SNAPSHOT_ROOT = f"data/snapshots/{SNAPSHOT_ID}"
PACK_ROOT = Path("tests/contract/packs/public-snapshots")
SCHEMA_ROOT = Path("schemas/public-snapshots")
GENERATED_TYPES = Path("apps/web/lib/api/openapi.generated.ts")
INTERFACE_REGISTRY = Path("governance/interfaces.json")
R1_VALID_FIXTURE_ROOT = Path("tests/contract/arc/fixtures/r1/valid")


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


PACK = _load(PACK_ROOT / "scenarios.v1.json")
INVALID_VECTORS = _load(PACK_ROOT / "invalid-vectors.v1.json")
EVIDENCE_INDEX_FIXTURE = _load(PACK_ROOT / "evidence-index.v1.json")
assert isinstance(PACK, dict)
assert isinstance(PACK["cases"], list)
assert isinstance(INVALID_VECTORS, dict)
assert isinstance(INVALID_VECTORS["vectors"], list)
CASES = {
    item["case_id"]: item
    for item in PACK["cases"]
    if isinstance(item, dict) and isinstance(item.get("case_id"), str)
}
VECTORS = [
    item
    for item in INVALID_VECTORS["vectors"]
    if isinstance(item, dict) and isinstance(item.get("case_id"), str)
]


def _pointer() -> dict[str, object]:
    return copy.deepcopy(CASES["partial"]["pointer"])


def _manifest() -> dict[str, object]:
    return copy.deepcopy(CASES["partial"]["manifest"])


def _run_status() -> dict[str, object]:
    return copy.deepcopy(CASES["partial"]["run_status"])


def _all_failed_degraded_run_status(*, schema_version: str) -> dict[str, object]:
    payload = _run_status()
    payload["schema_version"] = schema_version
    capabilities_value = payload["capabilities"]
    assert isinstance(capabilities_value, list)
    capabilities = copy.deepcopy(capabilities_value)
    for capability in capabilities:
        assert isinstance(capability, dict)
        capability["state"] = "failed"
        capability["safe_reason_code"] = "SOURCE_UNAVAILABLE"
    payload["capabilities"] = capabilities
    return payload


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _snapshot_provenance(
    source_identifier: str,
    *,
    origin_mode: str,
) -> dict[str, object]:
    is_live = origin_mode == "dev_live"
    assert is_live or origin_mode == "demo_replay"
    return {
        "schema_version": "1.2",
        "provider": "stock_market_agent",
        "access_channel": "official_http" if is_live else "demo_replay",
        "adapter_name": "r1_public_snapshot",
        "adapter_version": "1.0.0",
        "endpoint_name": "snapshot_asset",
        "source_identifier": source_identifier,
        "runtime_mode": origin_mode,
        "release_tier": "dev_only",
        "rights_status": "unverified" if is_live else "not_applicable",
        "quality_tier": "free_official" if is_live else "demo_replay",
        "evidence_role": "authoritative",
    }


def _schema_for(document: str) -> dict[str, object]:
    filename = {
        "pointer": "pointer.v1.0.schema.json",
        "manifest": "manifest.v1.1.schema.json",
        "evidence_index": "evidence-index.v1.0.schema.json",
        "run_status": "run-status.v1.1.schema.json",
    }[document]
    value = _load(SCHEMA_ROOT / filename)
    assert isinstance(value, dict)
    return value


def _model_for(document: str) -> type[ContractModel]:
    models: dict[str, type[ContractModel]] = {
        "pointer": PublicSnapshotPointerV1,
        "manifest": PublicSnapshotManifestV1_1,
        "evidence_index": PublicEvidenceIndexV1,
        "run_status": PublicRunStatusV1_1,
    }
    return models[document]


def _mutate(base: dict[str, object], vector: dict[str, object]) -> dict[str, object]:
    payload = copy.deepcopy(base)
    path = vector["path"]
    assert isinstance(path, str) and path.startswith("/")
    parts = path.removeprefix("/").split("/")
    current: object = payload
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            assert isinstance(current, dict)
            current = current[part]
    operation = vector["operation"]
    if operation == "append_copy":
        assert isinstance(current, dict)
        target = current[parts[-1]]
        assert isinstance(target, list)
        copy_index = vector["copy_index"]
        assert isinstance(copy_index, int)
        target.append(copy.deepcopy(target[copy_index]))
    elif operation == "remove":
        if isinstance(current, list):
            del current[int(parts[-1])]
        else:
            assert isinstance(current, dict)
            del current[parts[-1]]
    else:
        assert operation in {"replace", "add"}
        if isinstance(current, list):
            current[int(parts[-1])] = copy.deepcopy(vector["value"])
        else:
            assert isinstance(current, dict)
            current[parts[-1]] = copy.deepcopy(vector["value"])
    return payload


def _materialize_bundle(
    case_id: str,
) -> tuple[bytes, bytes, dict[str, bytes]]:
    case = copy.deepcopy(CASES[case_id])
    pointer = case["pointer"]
    manifest = case["manifest"]
    run_status = case["run_status"]
    assert isinstance(pointer, dict)
    assert isinstance(manifest, dict)
    assert isinstance(run_status, dict)
    assets = manifest["assets"]
    assert isinstance(assets, list)

    briefing_value = _load(R1_VALID_FIXTURE_ROOT / "premarket-briefing.v1.0.json")
    evidence_scenarios = _load(R1_VALID_FIXTURE_ROOT / "event-evidence-scenarios.v1.0.json")
    assert isinstance(briefing_value, dict)
    assert isinstance(evidence_scenarios, list)
    assert isinstance(evidence_scenarios[0], dict)
    assert isinstance(evidence_scenarios[0]["payload"], dict)
    briefing = copy.deepcopy(briefing_value)
    evidence = copy.deepcopy(evidence_scenarios[0]["payload"])
    origin_mode = manifest["origin_mode"]
    assert isinstance(origin_mode, str)
    data_mode = "real" if origin_mode == "dev_live" else "synthetic_development_only"
    briefing["schema_version"] = "1.2"
    briefing["runtime_mode"] = origin_mode
    briefing["release_tier"] = "dev_only"
    briefing["as_of"] = manifest["data_as_of"]
    briefing["generated_at"] = manifest["generated_at"]
    ingestion_progress = briefing["ingestion_progress"]
    source_coverage = briefing["source_coverage"]
    assert isinstance(ingestion_progress, dict)
    assert isinstance(source_coverage, list)
    ingestion_progress["data_mode"] = data_mode
    ingestion_progress["provenance"] = _snapshot_provenance(
        "snapshot:primary", origin_mode=origin_mode
    )
    for index, coverage in enumerate(source_coverage):
        assert isinstance(coverage, dict)
        progress = coverage["progress"]
        assert isinstance(progress, dict)
        progress["data_mode"] = data_mode
        coverage["provenance"] = _snapshot_provenance(
            f"snapshot:coverage:{index}", origin_mode=origin_mode
        )

    evidence["schema_version"] = "1.2"
    evidence["runtime_mode"] = origin_mode
    evidence["as_of"] = manifest["data_as_of"]
    evidence_items = evidence["evidence"]
    assert isinstance(evidence_items, list)
    for index, item in enumerate(evidence_items):
        assert isinstance(item, dict)
        item["event_time"] = item["published_at"]
        item["provenance"] = _snapshot_provenance(
            f"snapshot:evidence:{index}", origin_mode=origin_mode
        )
    evidence_entries: list[dict[str, str]] = []
    for asset in assets:
        assert isinstance(asset, dict)
        if asset["kind"] != "evidence":
            continue
        path = asset["path"]
        assert isinstance(path, str)
        relative_path = path.removeprefix(f"data/snapshots/{manifest['snapshot_id']}/")
        event_id = relative_path.removeprefix("evidence/").removesuffix(".json")
        evidence_entries.append({"event_id": event_id, "path": relative_path})

    documents_by_kind = {
        "briefing": _canonical_bytes(briefing),
        "evidence_index": _canonical_bytes({"schema_version": "1.0", "events": evidence_entries}),
        "evidence": _canonical_bytes(evidence),
        "run_status": _canonical_bytes(run_status),
    }
    asset_documents: dict[str, bytes] = {}
    for asset in assets:
        assert isinstance(asset, dict)
        path = asset["path"]
        kind = asset["kind"]
        assert isinstance(path, str)
        assert isinstance(kind, str)
        document = documents_by_kind[kind]
        asset["size_bytes"] = len(document)
        asset["sha256"] = hashlib.sha256(document).hexdigest()
        asset_documents[path] = document

    manifest_document = _canonical_bytes(manifest)
    pointer["manifest_sha256"] = hashlib.sha256(manifest_document).hexdigest()
    return _canonical_bytes(pointer), manifest_document, asset_documents


def _replace_asset_and_reseal(
    pointer_document: bytes,
    manifest_document: bytes,
    asset_documents: dict[str, bytes],
    *,
    asset_path: str,
    replacement: bytes,
) -> tuple[bytes, bytes, dict[str, bytes]]:
    pointer = json.loads(pointer_document)
    manifest = json.loads(manifest_document)
    assert isinstance(pointer, dict)
    assert isinstance(manifest, dict)
    assets = manifest["assets"]
    assert isinstance(assets, list)
    asset = next(
        item for item in assets if isinstance(item, dict) and item.get("path") == asset_path
    )
    asset["size_bytes"] = len(replacement)
    asset["sha256"] = hashlib.sha256(replacement).hexdigest()
    documents = dict(asset_documents)
    documents[asset_path] = replacement
    sealed_manifest = _canonical_bytes(manifest)
    pointer["manifest_sha256"] = hashlib.sha256(sealed_manifest).hexdigest()
    return _canonical_bytes(pointer), sealed_manifest, documents


@pytest.mark.contract
@pytest.mark.parametrize("case_id", sorted(CASES))
def test_shared_document_scenarios_validate_in_python_and_json_schema(case_id: str) -> None:
    case = CASES[case_id]
    for document in ("pointer", "manifest", "run_status"):
        payload = case[document]
        _model_for(document).model_validate(payload)
        Draft202012Validator(
            _schema_for(document),
            format_checker=FormatChecker(),
        ).validate(payload)


@pytest.mark.contract
def test_run_status_v1_1_accepts_all_failed_degraded_in_python_and_json_schema() -> None:
    payload = _all_failed_degraded_run_status(schema_version="1.1")
    PublicRunStatusV1_1.model_validate(payload)
    errors = list(
        Draft202012Validator(
            _schema_for("run_status"),
            format_checker=FormatChecker(),
        ).iter_errors(payload)
    )
    assert errors == []


@pytest.mark.contract
def test_run_status_v1_1_rejects_all_healthy_degraded_in_python_and_json_schema() -> None:
    payload = copy.deepcopy(CASES["valid_dev_live"]["run_status"])
    assert isinstance(payload, dict)
    payload["schema_version"] = "1.1"
    payload["status"] = "degraded"
    with pytest.raises(ValidationError, match="at least one unhealthy"):
        PublicRunStatusV1_1.model_validate(payload)
    assert list(
        Draft202012Validator(
            _schema_for("run_status"),
            format_checker=FormatChecker(),
        ).iter_errors(payload)
    )


@pytest.mark.contract
def test_run_status_v1_0_remains_immutable_and_rejects_all_failed_degraded() -> None:
    schema_path = SCHEMA_ROOT / "run-status.v1.0.schema.json"
    assert hashlib.sha256(schema_path.read_bytes()).hexdigest() == (
        "59065782cfcd08410fa81fd791716bd06031a58e3a01187084e7b737dfb490df"
    )
    schema = _load(schema_path)
    assert isinstance(schema, dict)
    payload = _all_failed_degraded_run_status(schema_version="1.0")
    with pytest.raises(ValidationError, match="problem plus healthy data"):
        PublicRunStatusV1.model_validate(payload)
    assert list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(payload)
    )


@pytest.mark.contract
def test_shared_evidence_index_fixture_validates_in_python_and_json_schema() -> None:
    PublicEvidenceIndexV1.model_validate(EVIDENCE_INDEX_FIXTURE)
    Draft202012Validator(
        _schema_for("evidence_index"),
        format_checker=FormatChecker(),
    ).validate(EVIDENCE_INDEX_FIXTURE)


@pytest.mark.contract
@pytest.mark.parametrize("vector", VECTORS, ids=lambda vector: vector["case_id"])
def test_invalid_vectors_distinguish_schema_checks_from_runtime_invariants(
    vector: dict[str, object],
) -> None:
    base_case = vector["base_case"]
    document = vector["document"]
    validation_layer = vector["validation_layer"]
    assert isinstance(base_case, str)
    assert isinstance(document, str)
    assert isinstance(validation_layer, str)
    payload = _mutate(CASES[base_case][document], vector)
    schema = _schema_for(document)
    schema_errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)
    )

    with pytest.raises(ValidationError):
        _model_for(document).model_validate(payload)
    if validation_layer == "schema_and_runtime":
        assert schema_errors
    else:
        assert validation_layer == "runtime_invariant"
        assert schema_errors == []
        invariants = schema.get("x-runtime-invariants")
        assert isinstance(invariants, list) and invariants


@pytest.mark.contract
def test_strict_bundle_validation_checks_exact_bytes_and_cross_document_identity() -> None:
    pointer, manifest, assets = _materialize_bundle("valid_dev_live")
    result = validate_public_snapshot_bundle(
        pointer_document=pointer,
        manifest_document=manifest,
        asset_documents=assets,
    )
    assert result.manifest.completeness.value == "complete"
    assert result.run_status.status.value == "succeeded"
    assert result.briefing.schema_version == "1.2"
    assert result.evidence_index.schema_version == "1.0"
    assert set(result.evidence_by_event_id) == {UUID("00000000-0000-0000-0000-000000001001")}

    tampered_assets = dict(assets)
    briefing_path = next(path for path in assets if path.endswith("/briefing.json"))
    tampered_assets[briefing_path] = b'{"tampered":true}'
    with pytest.raises(ValueError, match=r"size|digest"):
        validate_public_snapshot_bundle(
            pointer_document=pointer,
            manifest_document=manifest,
            asset_documents=tampered_assets,
        )


@pytest.mark.contract
@pytest.mark.parametrize(
    ("replacement", "expected_error"),
    [
        (b"{}", "briefing asset does not match"),
        (b"{", "must be one UTF-8 JSON document"),
    ],
)
def test_bundle_rejects_hash_valid_empty_or_non_json_assets(
    replacement: bytes,
    expected_error: str,
) -> None:
    pointer, manifest, assets = _materialize_bundle("valid_dev_live")
    briefing_path = next(path for path in assets if path.endswith("/briefing.json"))
    pointer, manifest, assets = _replace_asset_and_reseal(
        pointer,
        manifest,
        assets,
        asset_path=briefing_path,
        replacement=replacement,
    )
    with pytest.raises(ValueError, match=expected_error):
        validate_public_snapshot_bundle(
            pointer_document=pointer,
            manifest_document=manifest,
            asset_documents=assets,
        )


@pytest.mark.contract
@pytest.mark.parametrize("mutation", ["wrong_version", "extra_field", "wrong_asset_schema"])
def test_bundle_rejects_hash_valid_contract_invalid_assets(mutation: str) -> None:
    pointer, manifest, assets = _materialize_bundle("valid_dev_live")
    briefing_path = next(path for path in assets if path.endswith("/briefing.json"))
    evidence_path = next(path for path in assets if "/evidence/" in path)
    briefing = json.loads(assets[briefing_path])
    assert isinstance(briefing, dict)
    if mutation == "wrong_version":
        briefing["schema_version"] = "1.1"
        asset_path = briefing_path
        replacement = _canonical_bytes(briefing)
    elif mutation == "extra_field":
        briefing["unexpected"] = "hash-valid-but-contract-invalid"
        asset_path = briefing_path
        replacement = _canonical_bytes(briefing)
    else:
        asset_path = evidence_path
        replacement = assets[briefing_path]
    pointer, manifest, assets = _replace_asset_and_reseal(
        pointer,
        manifest,
        assets,
        asset_path=asset_path,
        replacement=replacement,
    )
    with pytest.raises(ValueError, match="asset does not match"):
        validate_public_snapshot_bundle(
            pointer_document=pointer,
            manifest_document=manifest,
            asset_documents=assets,
        )


@pytest.mark.contract
def test_bundle_rejects_missing_asset_and_index_document_identity_mismatch() -> None:
    pointer, manifest, assets = _materialize_bundle("valid_dev_live")
    missing = dict(assets)
    missing.pop(next(path for path in missing if path.endswith("/evidence-index.json")))
    with pytest.raises(ValueError, match="exactly match the manifest inventory"):
        validate_public_snapshot_bundle(
            pointer_document=pointer,
            manifest_document=manifest,
            asset_documents=missing,
        )

    evidence_path = next(path for path in assets if "/evidence/" in path)
    evidence = json.loads(assets[evidence_path])
    assert isinstance(evidence, dict)
    evidence["event_id"] = "20000000-0000-4000-8000-000000000002"
    pointer, manifest, assets = _replace_asset_and_reseal(
        pointer,
        manifest,
        assets,
        asset_path=evidence_path,
        replacement=_canonical_bytes(evidence),
    )
    with pytest.raises(ValueError, match="event_id does not match"):
        validate_public_snapshot_bundle(
            pointer_document=pointer,
            manifest_document=manifest,
            asset_documents=assets,
        )


@pytest.mark.contract
def test_asset_kind_mapping_is_complete_and_bound_to_published_contracts() -> None:
    assert set(PUBLIC_SNAPSHOT_ASSET_CONTRACTS) == set(PublicSnapshotAssetKind)
    assert {
        kind.value: (contract.model.__name__, contract.schema_id, contract.schema_version)
        for kind, contract in PUBLIC_SNAPSHOT_ASSET_CONTRACTS.items()
    } == {
        "briefing": (
            "PremarketBriefingViewV1_3",
            "schema:briefings.premarket-view:v1.3",
            "1.3",
        ),
        "evidence_index": (
            "PublicEvidenceIndexV1",
            "schema:public-snapshots.evidence-index:v1.0",
            "1.0",
        ),
        "evidence": (
            "EventEvidenceViewV1_2",
            "schema:events.event-evidence-view:v1.2",
            "1.2",
        ),
        "run_status": (
            "PublicRunStatusV1_1",
            "schema:public-snapshots.run-status:v1.1",
            "1.1",
        ),
    }


@pytest.mark.contract
def test_openapi_and_generated_types_include_current_and_legacy_public_models() -> None:
    openapi = _load(Path("schemas/http/openapi.v1.json"))
    assert isinstance(openapi, dict)
    components = openapi["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    expected = {
        "PublicSnapshotPointerV1",
        "PublicSnapshotManifestV1_1",
        "PublicEvidenceIndexV1",
        "PublicRunStatusV1",
        "PublicRunStatusV1_1",
    }
    assert expected <= set(schemas)

    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    for model_name in expected:
        assert f"{model_name}:" in generated

    paths = openapi["paths"]
    assert isinstance(paths, dict)
    serialized_paths = json.dumps(paths, sort_keys=True)
    assert all(model_name not in serialized_paths for model_name in expected)


@pytest.mark.contract
def test_registry_truthfully_publishes_only_the_immutable_document_schemas() -> None:
    registry = _load(INTERFACE_REGISTRY)
    assert isinstance(registry, dict)
    schemas = registry["schemas"]
    assert isinstance(schemas, list)
    expected_ids = {
        "schema:public-snapshots.pointer:v1.0",
        "schema:public-snapshots.manifest:v1.1",
        "schema:public-snapshots.evidence-index:v1.0",
        "schema:public-snapshots.run-status:v1.0",
        "schema:public-snapshots.run-status:v1.1",
    }
    entries = {
        entry["id"]: entry
        for entry in schemas
        if isinstance(entry, dict) and entry.get("id") in expected_ids
    }
    assert set(entries) == expected_ids
    assert all(entry["status"] == "published" for entry in entries.values())
    assert all(entry["owner"] == "public-snapshots" for entry in entries.values())
    assert all("runtime_consumer_status" not in entry for entry in entries.values())


@pytest.mark.contract
def test_public_snapshot_contracts_accept_a_strict_valid_bundle() -> None:
    pointer = PublicSnapshotPointerV1.model_validate(_pointer())
    manifest = PublicSnapshotManifestV1_1.model_validate(_manifest())
    status = PublicRunStatusV1_1.model_validate(_run_status())

    assert pointer.snapshot_id == manifest.snapshot_id == status.snapshot_id
    assert manifest.origin_mode.value == "dev_live"
    assert status.status.value == "degraded"


@pytest.mark.contract
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manifest_path", "../manifest.json"),
        ("manifest_path", f"{SNAPSHOT_ROOT}/other.json"),
        ("manifest_sha256", "A" * 64),
        ("product_commit", "b" * 39),
    ],
)
def test_pointer_rejects_unsafe_paths_and_noncanonical_hashes(
    field: str,
    value: str,
) -> None:
    payload = _pointer()
    payload[field] = value
    with pytest.raises(ValidationError):
        PublicSnapshotPointerV1.model_validate(payload)


@pytest.mark.contract
def test_asset_paths_are_site_root_relative_not_manifest_relative() -> None:
    pointer = PublicSnapshotPointerV1.model_validate(_pointer())
    assert not pointer.manifest_path.startswith("/")
    assert pointer.manifest_path.startswith("data/snapshots/")

    payload = _manifest()
    asset_value = payload["assets"]
    assert isinstance(asset_value, list)
    assets = copy.deepcopy(asset_value)
    assert isinstance(assets[0], dict)
    assets[0] = {**assets[0], "path": "briefing.json"}
    payload["assets"] = assets
    with pytest.raises(ValidationError, match="immutable snapshot"):
        PublicSnapshotManifestV1_1.model_validate(payload)


@pytest.mark.contract
def test_manifest_rejects_cross_snapshot_assets_and_invalid_timeline() -> None:
    payload = _manifest()
    asset_value = payload["assets"]
    assert isinstance(asset_value, list)
    assets = copy.deepcopy(asset_value)
    assert isinstance(assets[0], dict)
    assets[0] = {
        **assets[0],
        "path": "data/snapshots/20000000-0000-4000-8000-000000000002/briefing.json",
    }
    payload["assets"] = assets
    with pytest.raises(ValidationError, match="immutable snapshot"):
        PublicSnapshotManifestV1_1.model_validate(payload)

    payload = _manifest()
    payload["fresh_until"] = NOW.isoformat()
    with pytest.raises(ValidationError, match="data_as_of"):
        PublicSnapshotManifestV1_1.model_validate(payload)

    payload = _manifest()
    payload["last_successful_run_at"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError, match="last_successful_run_at"):
        PublicSnapshotManifestV1_1.model_validate(payload)

    payload = _manifest()
    payload["disclaimer_code"] = "HISTORICAL_DEMO_REPLAY"
    with pytest.raises(ValidationError, match="disclaimer_code"):
        PublicSnapshotManifestV1_1.model_validate(payload)


@pytest.mark.contract
def test_run_status_rejects_free_text_urls_and_private_cursor_fields() -> None:
    for field in ("message", "url", "provider_cursor", "business_watermark", "last_success_at"):
        payload = _run_status()
        payload[field] = "cursor-sentinel-never-public"
        with pytest.raises(ValidationError):
            PublicRunStatusV1_1.model_validate(payload)

    payload = _run_status()
    capability_value = payload["capabilities"]
    assert isinstance(capability_value, list)
    capabilities = copy.deepcopy(capability_value)
    assert isinstance(capabilities[1], dict)
    capabilities[1] = {
        **capabilities[1],
        "safe_reason_code": "upstream said something unsafe",
    }
    payload["capabilities"] = capabilities
    with pytest.raises(ValidationError):
        PublicRunStatusV1_1.model_validate(payload)


@pytest.mark.contract
def test_internal_ingestion_state_carries_opaque_progress_without_public_leakage() -> None:
    internal = SourceIngestionReadState(
        source_id="gov_cn",
        stream="r1_premarket:dev_live:macro.flashes",
        cursor_version=7,
        provider_cursor="cursor-sentinel-never-public",
        business_watermark=NOW - timedelta(minutes=3),
        last_success_at=NOW,
    )
    lease = SourceIngestionLease(
        source_id=internal.source_id,
        stream=internal.stream,
        owner="refresh-task-1",
        expected_cursor_version=internal.cursor_version,
        lease_expires_at=NOW + timedelta(minutes=5),
        provider_cursor=internal.provider_cursor,
        business_watermark=internal.business_watermark,
        last_success_at=internal.last_success_at,
    )
    assert lease.as_read_state() == internal

    public_documents = (
        PublicSnapshotPointerV1.model_validate(_pointer()).model_dump(mode="json"),
        PublicSnapshotManifestV1_1.model_validate(_manifest()).model_dump(mode="json"),
        PublicRunStatusV1_1.model_validate(_run_status()).model_dump(mode="json"),
    )
    serialized = json.dumps(public_documents, sort_keys=True)
    assert "cursor-sentinel-never-public" not in serialized
    for forbidden_name in ("provider_cursor", "business_watermark", "last_success_at"):
        assert forbidden_name not in serialized


@pytest.mark.contract
def test_public_snapshot_schemas_exclude_internal_ingestion_state() -> None:
    schemas = (
        PublicSnapshotPointerV1.model_json_schema(),
        PublicSnapshotManifestV1_1.model_json_schema(),
        PublicEvidenceIndexV1.model_json_schema(),
        PublicRunStatusV1.model_json_schema(),
        PublicRunStatusV1_1.model_json_schema(),
    )
    serialized = json.dumps(schemas, sort_keys=True)
    for forbidden_name in (
        "provider_cursor",
        "business_watermark",
        "last_success_at",
        "message",
        "url",
    ):
        assert forbidden_name not in serialized
