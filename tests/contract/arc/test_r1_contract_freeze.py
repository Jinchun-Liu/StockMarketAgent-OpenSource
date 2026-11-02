import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ValidationError
from scripts.generate_contract_schemas import SCHEMA_TARGETS

from stock_assistant.contracts.active_targets import (
    ActiveTargetMutationResult,
    ActiveTargetSelectionRequest,
    ActiveTargetState,
)
from stock_assistant.contracts.api import ProblemDetails
from stock_assistant.contracts.event_views import (
    EventEvidenceRevisionSelector,
    EventEvidenceView,
)
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingView,
    SourceAdvanceState,
    SourceCoverageStatus,
    SourceDataMode,
    SourceRole,
)
from stock_assistant.contracts.public_changes import map_public_change_type
from stock_assistant.contracts.r1_http import (
    ACTIVE_TARGET_REVISION_CONFLICT,
    IDEMPOTENCY_KEY_REUSE_CONFLICT,
    R1_HTTP_CONTRACT_ROUTES,
)

FIXTURE_ROOT = Path("tests/contract/arc/fixtures/r1")
MODEL_BY_NAME: dict[str, type[BaseModel]] = {
    "ActiveTargetSelectionRequest": ActiveTargetSelectionRequest,
    "ActiveTargetState": ActiveTargetState,
    "ActiveTargetMutationResult": ActiveTargetMutationResult,
    "PremarketBriefingView": PremarketBriefingView,
    "EventEvidenceView": EventEvidenceView,
    "EventEvidenceRevisionSelector": EventEvidenceRevisionSelector,
}
SCHEMA_BY_MODEL = {target.model.__name__: target.output_path for target in SCHEMA_TARGETS}
FROZEN_DIGESTS = {
    "schemas/briefings/briefing.v1.0.schema.json": (
        "bb503d6005b34e9239daf4c05e3a27f450f6819707529b4f1561198d854eb1a9"
    ),
    "schemas/events/market-event.v1.0.schema.json": (
        "28b22b48f59d758a8b0b20afa5eb5566241d670c629e19e8472d400a1b43f46d"
    ),
    "schemas/notifications/notification.v1.0.schema.json": (
        "1d7b0b01710dd42b61c09df49aa667edaff7b0b90a13de4db3d8a1abbb4f4c0e"
    ),
    "schemas/jobs/premarket-ingest-request.v1.0.schema.json": (
        "7418fed8dfe7bfca3cdc7f9d6b92236763ddcb9b389ccea7821615c1f5638b40"
    ),
    "schemas/jobs/premarket-ingest-result.v1.0.schema.json": (
        "5c707def79cf099950a63180aad96d1b041eccb0980013ce6038ca030671d55d"
    ),
    "schemas/jobs/premarket-generate-request.v1.0.schema.json": (
        "f18faea9262ce367039e2d3718e4c88b308e5c9efe6a07246a774ca6370deb8d"
    ),
    "schemas/jobs/premarket-generate-result.v1.0.schema.json": (
        "b7a31369c683c5fa55854d3f8ceb6cd7149d6b5be836e76ae8b6b21ba13afb96"
    ),
    # Aggregate OpenAPI and TypeScript artifacts are intentionally excluded:
    # compatible later-release additions regenerate them while the R1 schemas
    # above remain byte-for-byte frozen.
}
EXPECTED_VALID_CASES = {
    "selection-by-instrument-id",
    "selection-by-symbol-exchange",
    "no-active-target",
    "repeat-activation-noop",
    "idempotency-replay",
    "switch-target-revision",
    "statutory-announcement-new",
    "financial-correction",
    "numeric-contradiction",
    "macro-flash-timeliness-unverified",
    "single-source-outage",
    "premarket-briefing.v1.0",
    "current-evidence-revision-selector",
    "historical-evidence-revision-selector",
}
EXPECTED_INVALID_VECTORS = {
    "missing-version",
    "unknown-version",
    "extra-field",
    "json-number-decimal",
    "naive-datetime",
    "future-evidence",
    "duplicate-source-capability",
    "unknown-source-role",
    "ok-without-last-success",
    "advanced-without-watermark",
    "provider-cursor-forbidden",
    "mixed-source-data-mode",
    "briefing-primary-batch-mismatch",
    "future-revision-timeline",
    "timeline-identity-mismatch",
    "first-page-missing-selected",
    "unbounded-selector-timeline",
    "naive-selector-as-of",
    "correction-without-evidence",
    "contradiction-without-evidence",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted((FIXTURE_ROOT / "valid").glob("*.json")):
        value = _load(path)
        if isinstance(value, list):
            cases.extend(value)
        else:
            cases.append(
                {
                    "name": path.stem,
                    "model": "PremarketBriefingView",
                    "payload": value,
                }
            )
    return cases


def _case_from_collection(relative: str, name: str | None) -> dict[str, Any]:
    value = _load(FIXTURE_ROOT / relative)
    if isinstance(value, dict):
        return copy.deepcopy(value)
    matches = [item["payload"] for item in value if item["name"] == name]
    assert len(matches) == 1
    return copy.deepcopy(matches[0])


def _resolve_pointer(document: Any, pointer: str) -> tuple[Any, str]:
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]
    parent = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    return parent, parts[-1]


def _apply_invalid_vector(vector: dict[str, Any]) -> dict[str, Any]:
    payload = _case_from_collection(vector["base_fixture"], vector.get("base_case"))
    parent, key = _resolve_pointer(payload, vector["path"])
    operation = vector["operation"]
    if operation == "remove":
        if isinstance(parent, list):
            del parent[int(key)]
        else:
            del parent[key]
    elif operation in {"replace", "add"}:
        if isinstance(parent, list):
            parent[int(key)] = vector["value"]
        else:
            parent[key] = vector["value"]
    elif operation == "append-copy":
        source_parent, source_key = _resolve_pointer(payload, vector["from"])
        source = (
            source_parent[int(source_key)]
            if isinstance(source_parent, list)
            else source_parent[source_key]
        )
        target_parent, target_key = _resolve_pointer(payload, vector["path"])
        target = (
            target_parent[int(target_key)]
            if isinstance(target_parent, list)
            else target_parent[target_key]
        )
        target.append(copy.deepcopy(source))
    else:
        raise AssertionError(f"unsupported invalid fixture operation: {operation}")
    return payload


def _assert_internal_refs_resolve(schema: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                assert reference.startswith("#/")
                target: Any = schema
                for part in reference.removeprefix("#/").split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)


@pytest.mark.contract
def test_selector_rejects_ambiguous_and_partial_forms() -> None:
    invalid_payloads = (
        {
            "schema_version": "1.0",
            "instrument_id": "XSHG:600000",
            "symbol": "600000",
            "exchange": "XSHG",
        },
        {
            "schema_version": "1.0",
            "instrument_id": None,
            "symbol": "600000",
            "exchange": None,
        },
        {
            "schema_version": "1.0",
            "instrument_id": None,
            "symbol": None,
            "exchange": "XSHG",
        },
    )

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            ActiveTargetSelectionRequest.model_validate(payload)


@pytest.mark.contract
def test_idempotent_replay_cannot_claim_a_new_mutation() -> None:
    with pytest.raises(ValidationError, match="idempotent replay"):
        ActiveTargetMutationResult.model_validate(
            {
                "schema_version": "1.0",
                "state": {
                    "schema_version": "1.0",
                    "found": True,
                    "active_target": {
                        "instrument": {
                            "instrument_id": "XSHG:600000",
                            "symbol": "600000",
                            "exchange": "XSHG",
                            "market": "CN",
                            "asset_class": "equity",
                            "currency": "CNY",
                        },
                        "revision": 3,
                        "activated_at": "2026-07-25T00:58:00Z",
                    },
                    "as_of": "2026-07-25T01:00:00Z",
                },
                "previous_revision": 2,
                "created": False,
                "changed": True,
                "idempotent_replay": True,
                "stored_at": "2026-07-25T00:59:00Z",
            }
        )


@pytest.mark.contract
def test_public_change_vocabulary_and_r1_route_count_are_frozen() -> None:
    assert map_public_change_type("duplicate") is None
    mapped = [
        map_public_change_type(value) for value in ("new", "update", "correction", "contradiction")
    ]
    assert all(value is not None for value in mapped)
    assert {value.value for value in mapped if value is not None} == {
        "new",
        "update",
        "correction",
        "contradiction",
    }
    assert len(R1_HTTP_CONTRACT_ROUTES) == 4


@pytest.mark.contract
@pytest.mark.parametrize("case", _valid_cases(), ids=lambda case: case["name"])
def test_r1_wire_fixtures_pass_pydantic_schema_and_refs(case: dict[str, Any]) -> None:
    model = MODEL_BY_NAME[case["model"]]
    validated = model.model_validate(case["payload"])
    schema = _load(SCHEMA_BY_MODEL[case["model"]])
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        validated.model_dump(mode="json")
    )
    _assert_internal_refs_resolve(schema)


@pytest.mark.contract
@pytest.mark.parametrize(
    "vector",
    _load(FIXTURE_ROOT / "invalid/invalid-vectors.v1.0.json"),
    ids=lambda vector: vector["name"],
)
def test_r1_invalid_vectors_fail_closed(vector: dict[str, Any]) -> None:
    model = MODEL_BY_NAME[vector["model"]]
    with pytest.raises(ValidationError):
        model.model_validate(_apply_invalid_vector(vector))


@pytest.mark.contract
def test_r1_fixture_acceptance_and_negative_catalogs_are_exact() -> None:
    valid_names = [case["name"] for case in _valid_cases()]
    assert sorted(valid_names) == sorted(EXPECTED_VALID_CASES)
    invalid = _load(FIXTURE_ROOT / "invalid/invalid-vectors.v1.0.json")
    invalid_names = [vector["name"] for vector in invalid]
    assert sorted(invalid_names) == sorted(EXPECTED_INVALID_VECTORS)


@pytest.mark.contract
def test_premarket_view_has_exact_sections_roles_and_coverage_states() -> None:
    payload = _load(FIXTURE_ROOT / "valid/premarket-briefing.v1.0.json")
    view = PremarketBriefingView.model_validate(payload)

    assert set(type(view.sections).model_fields) == {
        "statutory_announcements",
        "financial_changes",
        "market_snapshot",
        "company_industry_news",
        "macro_flashes",
    }
    assert {role.value for role in SourceRole} == {
        "statutory",
        "professional_market",
        "professional_information",
        "discovery",
    }
    assert {status.value for status in SourceCoverageStatus} == {
        "ok",
        "delayed",
        "unavailable",
        "rights_blocked",
        "not_qualified",
        "timeliness_unverified",
    }
    assert {state.value for state in SourceAdvanceState} == {
        "advanced",
        "not_advanced",
    }
    assert {mode.value for mode in SourceDataMode} == {
        "real",
        "synthetic_development_only",
    }
    assert len(view.source_coverage) == len(
        {(item.source_id, item.capability) for item in view.source_coverage}
    )
    assert view.ingestion_progress.data_mode is SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY
    assert all(
        item.progress.data_mode is SourceDataMode.SYNTHETIC_DEVELOPMENT_ONLY
        for item in view.source_coverage
    )
    serialized = view.model_dump_json()
    assert "cursor" not in serialized
    assert "provider-cursor-sentinel" not in serialized


@pytest.mark.contract
def test_source_progress_fails_closed_without_success_or_business_watermark() -> None:
    payload = _load(FIXTURE_ROOT / "valid/premarket-briefing.v1.0.json")

    ok_without_success = copy.deepcopy(payload)
    ok_without_success["source_coverage"][0]["last_success_at"] = None
    with pytest.raises(ValidationError, match="ok coverage requires last_success_at"):
        PremarketBriefingView.model_validate(ok_without_success)

    advanced_without_watermark = copy.deepcopy(payload)
    advanced_without_watermark["source_coverage"][0]["progress"]["business_watermark"] = None
    with pytest.raises(ValidationError, match="requires business_watermark"):
        PremarketBriefingView.model_validate(advanced_without_watermark)

    leaked_provider_cursor = copy.deepcopy(payload)
    leaked_provider_cursor["source_coverage"][0]["progress"]["provider_cursor"] = (
        "provider-cursor-sentinel"
    )
    with pytest.raises(ValidationError):
        PremarketBriefingView.model_validate(leaked_provider_cursor)

    mixed_data_modes = copy.deepcopy(payload)
    mixed_data_modes["source_coverage"][1]["progress"]["data_mode"] = "real"
    with pytest.raises(ValidationError, match="data_mode values must match"):
        PremarketBriefingView.model_validate(mixed_data_modes)

    mismatched_primary_batch = copy.deepcopy(payload)
    mismatched_primary_batch["ingestion_progress"]["batch_audit_id"] = (
        "00000000-0000-0000-0000-000000004999"
    )
    with pytest.raises(ValidationError, match="must mirror"):
        PremarketBriefingView.model_validate(mismatched_primary_batch)


@pytest.mark.contract
def test_evidence_revision_selector_is_point_in_time_and_bounded() -> None:
    selector = EventEvidenceRevisionSelector.model_validate(
        {
            "schema_version": "1.0",
            "as_of": "2026-07-25T01:00:00Z",
            "revision": 2,
            "timeline_before_revision": 3,
            "timeline_limit": 50,
        }
    )
    assert selector.timeline_limit == 50
    terminal_selector = EventEvidenceRevisionSelector.model_validate(
        {
            **selector.model_dump(mode="json"),
            "timeline_before_revision": 1,
        }
    )
    assert terminal_selector.timeline_before_revision == 1

    with pytest.raises(ValidationError):
        EventEvidenceRevisionSelector.model_validate(
            {
                "schema_version": "1.0",
                "as_of": "2026-07-25T01:00:00Z",
                "timeline_limit": 51,
            }
        )
    with pytest.raises(ValidationError):
        EventEvidenceRevisionSelector.model_validate(
            {
                "schema_version": "1.0",
                "as_of": "2026-07-25T01:00:00",
            }
        )


@pytest.mark.contract
def test_evidence_revision_timeline_hides_future_corrections_and_traverses_old_revision() -> None:
    correction = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )
    correction["as_of"] = "2026-07-25T00:10:00Z"
    with pytest.raises(ValidationError, match="known after as_of"):
        EventEvidenceView.model_validate(correction)

    current = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )
    prior_identity = copy.deepcopy(current["revision_timeline"]["items"][1])
    prior = copy.deepcopy(current)
    prior.update(
        {
            "revision": 1,
            "change_type": "new",
            "title": "Synthetic original financial disclosure",
            "as_of": "2026-07-25T00:10:00Z",
            "revision_known_at": prior_identity["revision_known_at"],
            "supersedes_revision": None,
            "extraction_version": prior_identity["extraction_version"],
            "ruleset_version": prior_identity["ruleset_version"],
            "audit_id": prior_identity["audit_id"],
            "revision_timeline": {
                "before_revision": None,
                "items": [prior_identity],
                "has_older": False,
                "next_before_revision": None,
            },
            "facts": [
                {
                    "slot": "financial.revenue",
                    "value": {"kind": "decimal", "value": "120000000.00"},
                    "unit": "CNY",
                    "period": "2026-H1",
                    "evidence_ids": ["00000000-0000-0000-0000-000000002002"],
                }
            ],
            "evidence": [copy.deepcopy(current["evidence"][0])],
            "correction": None,
        }
    )
    view = EventEvidenceView.model_validate(prior)
    assert view.revision == 1
    assert [item.revision for item in view.revision_timeline.items] == [1]


@pytest.mark.contract
def test_revision_timeline_supports_two_pages_and_an_empty_terminal_page() -> None:
    current = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )
    selected_identity, prior_identity = copy.deepcopy(current["revision_timeline"]["items"])

    first_page = copy.deepcopy(current)
    first_page["revision_timeline"] = {
        "before_revision": None,
        "items": [selected_identity],
        "has_older": True,
        "next_before_revision": 2,
    }
    first_view = EventEvidenceView.model_validate(first_page)
    assert [item.revision for item in first_view.revision_timeline.items] == [2]

    continuation_page = copy.deepcopy(current)
    continuation_page["revision_timeline"] = {
        "before_revision": 2,
        "items": [prior_identity],
        "has_older": False,
        "next_before_revision": None,
    }
    continuation_view = EventEvidenceView.model_validate(continuation_page)
    assert [item.revision for item in continuation_view.revision_timeline.items] == [1]

    terminal_page = copy.deepcopy(current)
    terminal_page["revision_timeline"] = {
        "before_revision": 1,
        "items": [],
        "has_older": False,
        "next_before_revision": None,
    }
    terminal_view = EventEvidenceView.model_validate(terminal_page)
    assert terminal_view.revision_timeline.items == []

    first_page_without_selected = copy.deepcopy(current)
    first_page_without_selected["revision_timeline"] = {
        "before_revision": None,
        "items": [prior_identity],
        "has_older": False,
        "next_before_revision": None,
    }
    with pytest.raises(ValidationError, match="first timeline page"):
        EventEvidenceView.model_validate(first_page_without_selected)

    continuation_with_mismatched_selected = copy.deepcopy(current)
    mismatched_selected = copy.deepcopy(selected_identity)
    mismatched_selected["audit_id"] = "00000000-0000-0000-0000-000000005999"
    continuation_with_mismatched_selected["revision_timeline"] = {
        "before_revision": 3,
        "items": [mismatched_selected],
        "has_older": True,
        "next_before_revision": 2,
    }
    with pytest.raises(ValidationError, match="identity does not match"):
        EventEvidenceView.model_validate(continuation_with_mismatched_selected)


@pytest.mark.contract
def test_decimal_values_serialize_as_strings_and_compare_semantically() -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "numeric-contradiction",
    )
    view = EventEvidenceView.model_validate(payload)
    serialized = view.model_dump(mode="json")
    assert serialized["facts"][0]["value"]["value"] == "10.25"

    payload["facts"][1]["value"]["value"] = "10.250"
    with pytest.raises(ValidationError, match="requires conflicting values"):
        EventEvidenceView.model_validate(payload)


@pytest.mark.contract
@pytest.mark.parametrize(
    ("first_evidence", "second_evidence"),
    [
        (
            [
                "00000000-0000-0000-0000-000000002004",
                "00000000-0000-0000-0000-000000002005",
            ],
            [
                "00000000-0000-0000-0000-000000002004",
                "00000000-0000-0000-0000-000000002005",
            ],
        ),
        (
            ["00000000-0000-0000-0000-000000002004"],
            [
                "00000000-0000-0000-0000-000000002004",
                "00000000-0000-0000-0000-000000002005",
            ],
        ),
    ],
    ids=["identical-evidence-sets", "shared-only-first-claim"],
)
def test_contradiction_requires_exclusive_evidence_for_each_conflicting_value(
    first_evidence: list[str],
    second_evidence: list[str],
) -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "numeric-contradiction",
    )
    payload["facts"][0]["evidence_ids"] = first_evidence
    payload["facts"][1]["evidence_ids"] = second_evidence

    with pytest.raises(ValidationError, match="evidence exclusive to that claim"):
        EventEvidenceView.model_validate(payload)


@pytest.mark.contract
def test_correction_requires_directional_values_and_source_relation() -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )

    without_direction = copy.deepcopy(payload)
    del without_direction["correction"]
    del without_direction["evidence"][1]["correction_relation"]
    with pytest.raises(ValidationError, match="directional correction detail"):
        EventEvidenceView.model_validate(without_direction)

    without_prior_evidence = copy.deepcopy(payload)
    del without_prior_evidence["evidence"][0]
    with pytest.raises(ValidationError, match="references missing evidence"):
        EventEvidenceView.model_validate(without_prior_evidence)

    without_source_relation = copy.deepcopy(payload)
    del without_source_relation["evidence"][1]["correction_relation"]
    with pytest.raises(ValidationError, match="matching replacement/retraction relation"):
        EventEvidenceView.model_validate(without_source_relation)


@pytest.mark.contract
def test_correction_relations_form_a_closed_registered_evidence_graph() -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )
    additional_evidence_id = "00000000-0000-0000-0000-000000002008"

    dangling_prior = copy.deepcopy(payload)
    dangling_evidence = copy.deepcopy(dangling_prior["evidence"][1])
    dangling_evidence["evidence_id"] = additional_evidence_id
    dangling_evidence["correction_relation"]["prior_evidence_id"] = (
        "00000000-0000-0000-0000-000000002099"
    )
    dangling_prior["evidence"].append(dangling_evidence)
    with pytest.raises(ValidationError, match="prior evidence missing from the response"):
        EventEvidenceView.model_validate(dangling_prior)

    unregistered_current = copy.deepcopy(payload)
    unregistered_evidence = copy.deepcopy(unregistered_current["evidence"][1])
    unregistered_evidence["evidence_id"] = additional_evidence_id
    unregistered_current["evidence"].append(unregistered_evidence)
    with pytest.raises(ValidationError, match="registered in current_evidence_ids"):
        EventEvidenceView.model_validate(unregistered_current)

    self_reference = copy.deepcopy(payload)
    self_reference["evidence"][1]["correction_relation"]["prior_evidence_id"] = self_reference[
        "evidence"
    ][1]["evidence_id"]
    with pytest.raises(ValidationError, match="cannot reference its own evidence record"):
        EventEvidenceView.model_validate(self_reference)

    duplicate_evidence_id = copy.deepcopy(payload)
    duplicate_evidence_id["evidence"].append(copy.deepcopy(duplicate_evidence_id["evidence"][0]))
    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        EventEvidenceView.model_validate(duplicate_evidence_id)

    ordinary_additional_evidence = copy.deepcopy(payload)
    ordinary_evidence = copy.deepcopy(ordinary_additional_evidence["evidence"][1])
    ordinary_evidence["evidence_id"] = additional_evidence_id
    del ordinary_evidence["correction_relation"]
    ordinary_additional_evidence["evidence"].append(ordinary_evidence)
    EventEvidenceView.model_validate(ordinary_additional_evidence)


@pytest.mark.contract
def test_replacement_facts_cannot_mix_previous_and_current_evidence_directions() -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )

    mixed_current_fact = copy.deepcopy(payload)
    mixed_current_fact["facts"][0]["evidence_ids"].append("00000000-0000-0000-0000-000000002002")
    with pytest.raises(ValidationError, match="current replacement fact cannot reference"):
        EventEvidenceView.model_validate(mixed_current_fact)

    previous_fact_using_current_evidence = copy.deepcopy(payload)
    previous_fact_using_current_evidence["facts"].append(
        {
            "slot": "financial.revenue",
            "value": {"kind": "decimal", "value": "120000000.00"},
            "unit": "CNY",
            "period": "2026-H1",
            "evidence_ids": ["00000000-0000-0000-0000-000000002003"],
        }
    )
    with pytest.raises(ValidationError, match="previous replacement fact cannot reference"):
        EventEvidenceView.model_validate(previous_fact_using_current_evidence)


@pytest.mark.contract
def test_retraction_correction_is_directional_without_a_current_fact_value() -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        "financial-correction",
    )
    payload["title"] = "Synthetic revenue retraction"
    payload["correction"]["kind"] = "retraction"
    payload["correction"]["current_value"] = None
    payload["evidence"][1]["correction_relation"]["kind"] = "retraction"

    with pytest.raises(ValidationError, match="retraction correction must not expose"):
        EventEvidenceView.model_validate(payload)

    payload["facts"] = []
    view = EventEvidenceView.model_validate(payload)

    assert view.correction is not None
    assert view.correction.kind == "retraction"
    assert view.correction.current_value is None


@pytest.mark.contract
@pytest.mark.parametrize(
    ("case_name", "revision", "supersedes_revision"),
    [
        ("statutory-announcement-new", 2, None),
        ("statutory-announcement-new", 1, 1),
        ("single-source-outage", 1, None),
        ("single-source-outage", 2, None),
        ("financial-correction", 2, None),
        ("numeric-contradiction", 2, None),
    ],
)
def test_public_change_revision_semantics_fail_closed(
    case_name: str,
    revision: int,
    supersedes_revision: int | None,
) -> None:
    payload = _case_from_collection(
        "valid/event-evidence-scenarios.v1.0.json",
        case_name,
    )
    payload["revision"] = revision
    payload["supersedes_revision"] = supersedes_revision

    with pytest.raises(ValidationError, match="revision"):
        EventEvidenceView.model_validate(payload)


@pytest.mark.contract
def test_r1_route_contracts_use_success_and_rfc_9457_error_contracts() -> None:
    actual = {
        (route.method, route.path): (
            route.request_contract,
            route.response_data_contract,
            route.request_binding,
            route.success_envelope,
            route.error_contract,
            route.error_media_type,
            route.requires_idempotency_key,
            route.idempotency_key_header,
        )
        for route in R1_HTTP_CONTRACT_ROUTES
    }
    assert actual == {
        ("GET", "/api/v1/active-target"): (
            None,
            "stock_assistant.contracts.active_targets.ActiveTargetState",
            "none",
            "stock_assistant.contracts.api.ApiResponse",
            "stock_assistant.contracts.api.ProblemDetails",
            "application/problem+json",
            False,
            None,
        ),
        ("PUT", "/api/v1/active-target"): (
            "stock_assistant.contracts.active_targets.ActiveTargetSelectionRequest",
            "stock_assistant.contracts.active_targets.ActiveTargetMutationResult",
            "body",
            "stock_assistant.contracts.api.ApiResponse",
            "stock_assistant.contracts.api.ProblemDetails",
            "application/problem+json",
            True,
            "Idempotency-Key",
        ),
        ("GET", "/api/v1/briefings/premarket/latest"): (
            None,
            "stock_assistant.contracts.r1_http.PremarketBriefingHttpData",
            "none",
            "stock_assistant.contracts.api.ApiResponse",
            "stock_assistant.contracts.api.ProblemDetails",
            "application/problem+json",
            False,
            None,
        ),
        ("GET", "/api/v1/events/{event_id}/evidence"): (
            "stock_assistant.contracts.event_views.EventEvidenceRevisionSelector",
            "stock_assistant.contracts.r1_http.EventEvidenceHttpData",
            "query",
            "stock_assistant.contracts.api.ApiResponse",
            "stock_assistant.contracts.api.ProblemDetails",
            "application/problem+json",
            False,
            None,
        ),
    }
    assert {"type", "title", "status", "detail", "instance"} <= set(ProblemDetails.model_fields)
    put_route = next(
        route
        for route in R1_HTTP_CONTRACT_ROUTES
        if route.method == "PUT" and route.path == "/api/v1/active-target"
    )
    assert put_route.problems == (
        ACTIVE_TARGET_REVISION_CONFLICT,
        IDEMPOTENCY_KEY_REUSE_CONFLICT,
    )
    assert {(problem.status, problem.code) for problem in put_route.problems} == {
        (409, "ACTIVE_TARGET_REVISION_CONFLICT"),
        (409, "IDEMPOTENCY_KEY_REUSE_CONFLICT"),
    }

    registry = _load(Path("governance/interfaces.json"))
    registered_r1_routes = {
        (route["method"], route["path"])
        for route in registry["http_endpoints"]
        if route["path"] in {path for _method, path in actual}
    }
    assert registered_r1_routes == {
        ("GET", "/api/v1/active-target"),
        ("GET", "/api/v1/briefings/premarket/latest"),
        ("GET", "/api/v1/events/{event_id}/evidence"),
    }
    assert {(route["method"], route["path"]) for route in registry["http_endpoints"]} >= {
        ("GET", "/api/v1/today"),
        (
            "GET",
            "/api/v1/instruments/{instrument_id}/behavior-pressure-snapshots",
        ),
        ("GET", "/api/v1/today/stream"),
    }


@pytest.mark.contract
def test_new_schema_registry_is_exact_and_existing_bytes_remain_frozen() -> None:
    registry = _load(Path("governance/interfaces.json"))
    schema_ids = {item["id"] for item in registry["schemas"]}
    assert {
        "schema:active-targets.selection-request:v1.0",
        "schema:active-targets.state:v1.0",
        "schema:active-targets.mutation-result:v1.0",
        "schema:briefings.premarket-view:v1.0",
        "schema:events.event-evidence-view:v1.0",
        "schema:events.event-evidence-selector:v1.0",
        "schema:ingestion.rights-check:v1.1",
        "schema:ingestion.rights-decision:v1.1",
        "schema:ingestion.source-provenance:v1.1",
        "schema:jobs.premarket-ingest-request:v1.1",
        "schema:jobs.premarket-ingest-result:v1.1",
        "schema:briefings.premarket-view:v1.1",
        "schema:events.event-evidence-view:v1.1",
        "schema:ingestion.source-provenance:v1.2",
        "schema:jobs.premarket-ingest-request:v1.2",
        "schema:jobs.premarket-ingest-result:v1.2",
        "schema:briefings.premarket-view:v1.2",
        "schema:events.event-evidence-view:v1.2",
        "schema:briefings.premarket-view:v1.3",
        "schema:behavior.behavior-pressure-snapshot:v1.0",
        "schema:jobs.behavior-compute-request:v1.0",
        "schema:events.behavior-band-transition:v1.0",
        "schema:public-snapshots.pointer:v1.0",
        "schema:public-snapshots.manifest:v1.1",
        "schema:public-snapshots.evidence-index:v1.0",
        "schema:public-snapshots.run-status:v1.0",
        "schema:public-snapshots.run-status:v1.1",
    } <= schema_ids
    assert {
        "schema:instruments.search-result:v1.0",
        "schema:instruments.search-result:v1.1",
        "schema:jobs.research-generate-request:v1.0",
        "schema:jobs.research-generate-request:v1.1",
        "schema:jobs.research-generate-result:v1.0",
        "schema:events.research-report-ready:v1.0",
        "schema:research.report:v1.0",
        "schema:jobs.policy-refresh-request:v1.0",
        "schema:jobs.policy-refresh-result:v1.0",
    } <= schema_ids
    assert {item["name"] for item in registry["events"]} == {
        "com.stockassistant.behavior.band_transition.v1",
        "com.stockassistant.research.report.ready.v1",
    }
    assert {item["id"] for item in _load(Path("governance/metrics.json"))["metrics"]} >= {
        "metric:product.attention-risk-index:v1",
        "metric:product.behavior-pressure-research:v1",
    }

    for relative, expected in FROZEN_DIGESTS.items():
        assert hashlib.sha256(Path(relative).read_bytes()).hexdigest() == expected


@pytest.mark.contract
def test_generated_schemas_declare_cross_record_runtime_invariants() -> None:
    selection = _load(Path("schemas/active-targets/selection-request.v1.0.schema.json"))
    state = _load(Path("schemas/active-targets/state.v1.0.schema.json"))
    mutation = _load(Path("schemas/active-targets/mutation-result.v1.0.schema.json"))
    premarket = _load(Path("schemas/briefings/premarket-view.v1.0.schema.json"))
    event = _load(Path("schemas/events/event-evidence-view.v1.0.schema.json"))
    selector = _load(Path("schemas/events/event-evidence-selector.v1.0.schema.json"))

    assert selection["x-runtime-invariants"] == [
        "provide exactly one selector: instrument_id or symbol + exchange"
    ]
    assert state["x-runtime-invariants"] == [
        "found is true if and only if active_target is present",
        "active_target.activated_at is <= as_of when active_target is present",
    ]
    assert mutation["x-runtime-invariants"][-1] == (
        "idempotent_replay=true requires created=false and changed=false"
    )
    assert premarket["x-runtime-invariants"] == [
        "comparison_since is <= as_of",
        "generated_at is >= as_of",
        "source_coverage entries are unique by source_id + capability",
        "all source_coverage.observed_at values are <= generated_at",
        "ingestion_progress.business_watermark is <= as_of when present",
        "all source_coverage.progress.data_mode values equal ingestion_progress.data_mode",
        (
            "ingestion_progress exactly mirrors the progress for its registered "
            "source_id + capability"
        ),
        "all section item first_seen_at values are <= as_of",
    ]
    assert selector["properties"]["timeline_limit"]["maximum"] == 50
    assert selector["required"] == ["schema_version", "as_of"]
    assert (
        "selected revision_known_at is <= as_of and covers all selected evidence"
        in event["x-runtime-invariants"]
    )
    assert (
        "the first revision_timeline page contains the selected revision identity"
        in event["x-runtime-invariants"]
    )
    assert (
        "any revision_timeline page containing the selected revision matches its identity exactly"
    ) in event["x-runtime-invariants"]
    assert event["x-runtime-invariants"][-1] == (
        "contradiction requires semantically distinct values for the same "
        "slot/period/unit and every conflicting value has evidence not used by the "
        "other conflicting values"
    )
    assert "new requires revision=1 and supersedes_revision=null" in event["x-runtime-invariants"]
    assert (
        "update, correction and contradiction require revision>1 and supersedes_revision<revision"
    ) in event["x-runtime-invariants"]
    assert (
        "correction requires directional replacement/retraction values and "
        "matching evidence relations; replacement facts bind only to their previous or "
        "current evidence side"
    ) in event["x-runtime-invariants"]
    assert (
        "every correction_relation is a closed edge from a registered current evidence "
        "record to a distinct registered previous evidence record in this response"
    ) in event["x-runtime-invariants"]
