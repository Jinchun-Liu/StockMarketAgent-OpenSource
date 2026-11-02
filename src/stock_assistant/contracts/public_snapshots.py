"""Strict contracts for the same-origin public snapshot data plane."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    ConfigDict,
    Field,
    WithJsonSchema,
    model_validator,
)

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.event_views import EventEvidenceViewV1_2
from stock_assistant.contracts.ingestion import SourceRuntimeModeV1_2
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
GitCommitSha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


def _validate_site_root_relative_json_path(value: str) -> str:
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must be site-root-relative without empty or traversal segments")
    return value


SiteRootRelativeJsonPath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1024,
        pattern=r"^[a-zA-Z0-9._/-]+\.json$",
    ),
    AfterValidator(_validate_site_root_relative_json_path),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "maxLength": 1024,
            "pattern": r"^[a-zA-Z0-9._/-]+\.json$",
            "not": {
                "anyOf": [
                    {"pattern": r"^/"},
                    {"pattern": r"//"},
                    {"pattern": r"(^|/)\.{1,2}(/|$)"},
                ]
            },
        }
    ),
]


class PublicSnapshotDeliveryMode(StrEnum):
    """How a snapshot reaches the browser, independent of its data origin."""

    PUBLIC_STATIC = "public_static"


class PublicSnapshotOriginMode(StrEnum):
    """Permitted origins for the development-only public preview."""

    DEV_LIVE = "dev_live"
    DEMO_REPLAY = "demo_replay"


class PublicSnapshotCompleteness(StrEnum):
    """Whether the snapshot contains all expected capability outputs."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class PublicSnapshotAssetKind(StrEnum):
    """Bounded public asset roles understood by the snapshot consumer."""

    BRIEFING = "briefing"
    EVIDENCE_INDEX = "evidence_index"
    EVIDENCE = "evidence"
    RUN_STATUS = "run_status"


class PublicEvidenceIndexEntryV1(ContractModel):
    """One public evidence document addressable from its snapshot root."""

    event_id: UUID
    path: SiteRootRelativeJsonPath
    model_config = ConfigDict(
        json_schema_extra={"x-runtime-invariants": ["path equals evidence/{event_id}.json"]}
    )

    @model_validator(mode="after")
    def path_must_match_event_id(self) -> "PublicEvidenceIndexEntryV1":
        if self.path != f"evidence/{self.event_id}.json":
            raise ValueError("evidence index path must match its event_id")
        return self


class PublicEvidenceIndexV1(ContractModel):
    """Strict public index joining evidence event IDs to immutable JSON assets."""

    schema_version: Literal["1.0"] = "1.0"
    events: list[PublicEvidenceIndexEntryV1] = Field(max_length=1000)
    model_config = ConfigDict(
        json_schema_extra={"x-runtime-invariants": ["event_id and path values are each unique"]}
    )

    @model_validator(mode="after")
    def events_must_be_unique(self) -> "PublicEvidenceIndexV1":
        event_ids = [item.event_id for item in self.events]
        paths = [item.path for item in self.events]
        if len(event_ids) != len(set(event_ids)) or len(paths) != len(set(paths)):
            raise ValueError("evidence index event IDs and paths must be unique")
        return self


class PublicSnapshotAsset(ContractModel):
    """One immutable JSON asset addressed from the site root without a leading slash."""

    kind: PublicSnapshotAssetKind
    path: SiteRootRelativeJsonPath
    sha256: Sha256
    media_type: Literal["application/json"] = "application/json"
    size_bytes: int = Field(ge=0, le=10 * 1024 * 1024)
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"kind": {"const": "briefing"}}},
                    "then": {"properties": {"path": {"pattern": r"/briefing\.json$"}}},
                },
                {
                    "if": {"properties": {"kind": {"const": "evidence_index"}}},
                    "then": {"properties": {"path": {"pattern": r"/evidence-index\.json$"}}},
                },
                {
                    "if": {"properties": {"kind": {"const": "evidence"}}},
                    "then": {
                        "properties": {
                            "path": {
                                "pattern": (
                                    r"/evidence/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
                                    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                                    r"[0-9a-fA-F]{12}\.json$"
                                )
                            }
                        }
                    },
                },
                {
                    "if": {"properties": {"kind": {"const": "run_status"}}},
                    "then": {"properties": {"path": {"pattern": r"/run-status\.json$"}}},
                },
            ]
        }
    )

    @model_validator(mode="after")
    def path_must_match_kind(self) -> "PublicSnapshotAsset":
        expected_filename = {
            PublicSnapshotAssetKind.BRIEFING: "briefing.json",
            PublicSnapshotAssetKind.EVIDENCE_INDEX: "evidence-index.json",
            PublicSnapshotAssetKind.RUN_STATUS: "run-status.json",
        }
        if (
            self.kind in expected_filename
            and self.path.rsplit("/", 1)[-1] != expected_filename[self.kind]
        ):
            raise ValueError("snapshot asset path must match its kind")
        if self.kind is PublicSnapshotAssetKind.EVIDENCE:
            parts = self.path.rsplit("/", 2)
            if len(parts) != 3 or parts[-2] != "evidence":
                raise ValueError("evidence assets must remain in the evidence directory")
            event_id_text = parts[-1].removesuffix(".json")
            try:
                event_id = UUID(event_id_text)
            except ValueError as exc:
                raise ValueError("evidence asset filename must be its event UUID") from exc
            if event_id_text != str(event_id):
                raise ValueError("evidence asset filename must use the canonical event UUID")
        return self


class PublicSnapshotPointerV1(ContractModel):
    """Small mutable pointer switched only after an immutable snapshot is complete."""

    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: UUID
    manifest_path: SiteRootRelativeJsonPath
    manifest_sha256: Sha256
    product_commit: GitCommitSha
    published_at: AwareDatetime
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "manifest_path equals data/snapshots/{snapshot_id}/manifest.json",
                "manifest_sha256 equals the SHA-256 digest of the exact manifest bytes",
                "snapshot_id, product_commit, and published_at match the manifest",
            ]
        }
    )

    @model_validator(mode="after")
    def manifest_path_must_match_snapshot(self) -> "PublicSnapshotPointerV1":
        expected = f"data/snapshots/{self.snapshot_id}/manifest.json"
        if self.manifest_path != expected:
            raise ValueError("manifest_path must identify this snapshot's immutable manifest")
        return self


class PublicSnapshotManifestV1_1(ContractModel):
    """Immutable inventory and freshness boundary for one public preview snapshot."""

    schema_version: Literal["1.1"] = "1.1"
    snapshot_id: UUID
    product_commit: GitCommitSha
    delivery_mode: Literal[PublicSnapshotDeliveryMode.PUBLIC_STATIC]
    origin_mode: PublicSnapshotOriginMode
    release_tier: Literal["dev_only"] = "dev_only"
    data_as_of: AwareDatetime
    generated_at: AwareDatetime
    published_at: AwareDatetime
    fresh_until: AwareDatetime
    last_successful_run_at: AwareDatetime | None = None
    completeness: PublicSnapshotCompleteness
    disclaimer_code: Literal[
        "DEV_ONLY_UNVERIFIED_SOURCE",
        "HISTORICAL_DEMO_REPLAY",
    ]
    assets: list[PublicSnapshotAsset] = Field(min_length=3, max_length=1003)
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"origin_mode": {"const": "dev_live"}}},
                    "then": {
                        "properties": {"disclaimer_code": {"const": "DEV_ONLY_UNVERIFIED_SOURCE"}}
                    },
                },
                {
                    "if": {"properties": {"origin_mode": {"const": "demo_replay"}}},
                    "then": {
                        "properties": {"disclaimer_code": {"const": "HISTORICAL_DEMO_REPLAY"}}
                    },
                },
                {
                    "properties": {
                        "assets": {
                            "contains": {
                                "properties": {"kind": {"const": "briefing"}},
                                "required": ["kind"],
                            },
                            "minContains": 1,
                            "maxContains": 1,
                        }
                    }
                },
                {
                    "properties": {
                        "assets": {
                            "contains": {
                                "properties": {"kind": {"const": "run_status"}},
                                "required": ["kind"],
                            },
                            "minContains": 1,
                            "maxContains": 1,
                        }
                    }
                },
                {
                    "properties": {
                        "assets": {
                            "contains": {
                                "properties": {"kind": {"const": "evidence_index"}},
                                "required": ["kind"],
                            },
                            "minContains": 1,
                            "maxContains": 1,
                        }
                    }
                },
            ],
            "x-runtime-invariants": [
                "data_as_of <= generated_at <= published_at < fresh_until",
                "last_successful_run_at is null or <= published_at",
                "asset paths are unique and rooted at data/snapshots/{snapshot_id}/",
                "asset sizes and SHA-256 digests match the exact published bytes",
                "the evidence index exactly names every evidence asset event ID and path",
                "completeness matches the validated PublicRunStatusV1 terminal state",
            ],
        }
    )

    @model_validator(mode="after")
    def timeline_and_assets_must_be_consistent(self) -> "PublicSnapshotManifestV1_1":
        if not (self.data_as_of <= self.generated_at <= self.published_at < self.fresh_until):
            raise ValueError(
                "snapshot times must satisfy data_as_of <= generated_at <= "
                "published_at < fresh_until"
            )
        if (
            self.last_successful_run_at is not None
            and self.last_successful_run_at > self.published_at
        ):
            raise ValueError("last_successful_run_at cannot be later than published_at")
        expected_disclaimer = (
            "DEV_ONLY_UNVERIFIED_SOURCE"
            if self.origin_mode is PublicSnapshotOriginMode.DEV_LIVE
            else "HISTORICAL_DEMO_REPLAY"
        )
        if self.disclaimer_code != expected_disclaimer:
            raise ValueError("disclaimer_code must match origin_mode")

        paths = [asset.path for asset in self.assets]
        if len(paths) != len(set(paths)):
            raise ValueError("snapshot asset paths must be unique")
        prefix = f"data/snapshots/{self.snapshot_id}/"
        if any(not path.startswith(prefix) for path in paths):
            raise ValueError("every snapshot asset path must remain inside its immutable snapshot")

        kinds = [asset.kind for asset in self.assets]
        if kinds.count(PublicSnapshotAssetKind.BRIEFING) != 1:
            raise ValueError("snapshot assets require exactly one briefing")
        if kinds.count(PublicSnapshotAssetKind.EVIDENCE_INDEX) != 1:
            raise ValueError("snapshot assets require exactly one evidence index")
        if kinds.count(PublicSnapshotAssetKind.RUN_STATUS) != 1:
            raise ValueError("snapshot assets require exactly one run status")

        canonical_singletons = {
            PublicSnapshotAssetKind.BRIEFING: f"{prefix}briefing.json",
            PublicSnapshotAssetKind.EVIDENCE_INDEX: f"{prefix}evidence-index.json",
            PublicSnapshotAssetKind.RUN_STATUS: f"{prefix}run-status.json",
        }
        for kind, expected_path in canonical_singletons.items():
            asset = next(item for item in self.assets if item.kind is kind)
            if asset.path != expected_path:
                raise ValueError(f"{kind.value} asset path must be canonical for this snapshot")
        return self


class PublicCapabilityRunState(StrEnum):
    """Publicly safe per-capability state without provider-owned details."""

    SUCCEEDED = "succeeded"
    NO_CHANGE = "no_change"
    GAP = "gap"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"


class PublicSourceGrade(StrEnum):
    """Stable public quality disclosure without provider-private state."""

    FREE_OFFICIAL_UNVERIFIED = "free_official_unverified"
    FREE_STRUCTURED_UNVERIFIED = "free_structured_unverified"
    DEV_WEB_UNVERIFIED = "dev_web_unverified"
    DEMO_REPLAY = "demo_replay"


class PublicCapabilityRunStatus(ContractModel):
    """Safe status projection; it deliberately excludes cursors, URLs and free text."""

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,99}$")
    capability: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    source_grade: PublicSourceGrade
    state: PublicCapabilityRunState
    safe_reason_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{1,63}$",
    )
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"state": {"enum": ["succeeded", "no_change"]}}},
                    "then": {"properties": {"safe_reason_code": {"type": "null"}}},
                    "else": {
                        "required": ["safe_reason_code"],
                        "properties": {
                            "safe_reason_code": {
                                "type": "string",
                                "pattern": r"^[A-Z][A-Z0-9_]{1,63}$",
                            }
                        },
                    },
                }
            ]
        }
    )

    @model_validator(mode="after")
    def reason_code_must_match_state(self) -> "PublicCapabilityRunStatus":
        problem_states = {
            PublicCapabilityRunState.GAP,
            PublicCapabilityRunState.BLOCKED,
            PublicCapabilityRunState.FAILED,
            PublicCapabilityRunState.STALE,
        }
        if (self.state in problem_states) != (self.safe_reason_code is not None):
            raise ValueError(
                "problem states require one safe reason code, healthy states forbid it"
            )
        return self


class PublicRunState(StrEnum):
    """Terminal state of one bounded refresh run."""

    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class PublicRunStatusV1(ContractModel):
    """Sanitized run status published beside one immutable snapshot."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    snapshot_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    status: PublicRunState
    capabilities: list[PublicCapabilityRunStatus] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "succeeded"}}},
                    "then": {
                        "properties": {
                            "capabilities": {
                                "items": {
                                    "properties": {"state": {"enum": ["succeeded", "no_change"]}}
                                }
                            }
                        }
                    },
                },
                {
                    "if": {"properties": {"status": {"const": "failed"}}},
                    "then": {
                        "properties": {
                            "capabilities": {
                                "items": {
                                    "properties": {
                                        "state": {"enum": ["gap", "blocked", "failed", "stale"]}
                                    }
                                },
                                "contains": {
                                    "properties": {"state": {"enum": ["gap", "blocked", "failed"]}},
                                    "required": ["state"],
                                },
                            }
                        }
                    },
                },
                {
                    "if": {"properties": {"status": {"const": "degraded"}}},
                    "then": {
                        "properties": {
                            "capabilities": {
                                "contains": {
                                    "properties": {
                                        "state": {"enum": ["gap", "blocked", "failed", "stale"]}
                                    },
                                    "required": ["state"],
                                }
                            }
                        },
                        "anyOf": [
                            {
                                "properties": {
                                    "capabilities": {
                                        "contains": {
                                            "properties": {
                                                "state": {"enum": ["succeeded", "no_change"]}
                                            },
                                            "required": ["state"],
                                        }
                                    }
                                }
                            },
                            {
                                "properties": {
                                    "capabilities": {
                                        "items": {"properties": {"state": {"const": "stale"}}}
                                    }
                                }
                            },
                        ],
                    },
                },
            ],
            "x-runtime-invariants": [
                "completed_at >= started_at",
                "(source_id, capability) identities are unique",
                "snapshot_id matches the pointer and manifest",
                "completed_at <= manifest.generated_at",
                "source_grade values match manifest.origin_mode",
            ],
        }
    )

    @model_validator(mode="after")
    def terminal_state_must_match_capabilities(self) -> "PublicRunStatusV1":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")
        identities = [(item.source_id, item.capability) for item in self.capabilities]
        if len(identities) != len(set(identities)):
            raise ValueError("capability run identities must be unique")

        healthy = {
            PublicCapabilityRunState.SUCCEEDED,
            PublicCapabilityRunState.NO_CHANGE,
        }
        healthy_count = sum(item.state in healthy for item in self.capabilities)
        if self.status is PublicRunState.SUCCEEDED and healthy_count != len(self.capabilities):
            raise ValueError("succeeded runs require every capability to be healthy")
        hard_problem = {
            PublicCapabilityRunState.GAP,
            PublicCapabilityRunState.BLOCKED,
            PublicCapabilityRunState.FAILED,
        }
        if self.status is PublicRunState.FAILED and (
            healthy_count != 0 or not any(item.state in hard_problem for item in self.capabilities)
        ):
            raise ValueError("failed runs require only unhealthy capabilities and a hard problem")
        if self.status is PublicRunState.DEGRADED:
            unhealthy_count = len(self.capabilities) - healthy_count
            all_stale = all(
                item.state is PublicCapabilityRunState.STALE for item in self.capabilities
            )
            if unhealthy_count == 0 or (healthy_count == 0 and not all_stale):
                raise ValueError(
                    "degraded runs require a problem plus healthy data, or an all-stale LKG"
                )
        return self


class PublicRunStatusV1_1(ContractModel):
    """Run status that permits an explicit all-unhealthy degraded preview."""

    schema_version: Literal["1.1"] = "1.1"
    run_id: UUID
    snapshot_id: UUID
    started_at: AwareDatetime
    completed_at: AwareDatetime
    status: PublicRunState
    capabilities: list[PublicCapabilityRunStatus] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "succeeded"}}},
                    "then": {
                        "properties": {
                            "capabilities": {
                                "items": {
                                    "properties": {"state": {"enum": ["succeeded", "no_change"]}}
                                }
                            }
                        }
                    },
                },
                {
                    "if": {"properties": {"status": {"const": "failed"}}},
                    "then": {
                        "properties": {
                            "capabilities": {
                                "items": {
                                    "properties": {
                                        "state": {"enum": ["gap", "blocked", "failed", "stale"]}
                                    }
                                },
                                "contains": {
                                    "properties": {"state": {"enum": ["gap", "blocked", "failed"]}},
                                    "required": ["state"],
                                },
                            }
                        }
                    },
                },
                {
                    "if": {"properties": {"status": {"const": "degraded"}}},
                    "then": {
                        "properties": {
                            "capabilities": {
                                "contains": {
                                    "properties": {
                                        "state": {"enum": ["gap", "blocked", "failed", "stale"]}
                                    },
                                    "required": ["state"],
                                }
                            }
                        }
                    },
                },
            ],
            "x-runtime-invariants": [
                "completed_at >= started_at",
                "(source_id, capability) identities are unique",
                "snapshot_id matches the pointer and manifest",
                "completed_at <= manifest.generated_at",
                "source_grade values match manifest.origin_mode",
            ],
        }
    )

    @model_validator(mode="after")
    def terminal_state_must_match_capabilities(self) -> "PublicRunStatusV1_1":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")
        identities = [(item.source_id, item.capability) for item in self.capabilities]
        if len(identities) != len(set(identities)):
            raise ValueError("capability run identities must be unique")

        healthy = {
            PublicCapabilityRunState.SUCCEEDED,
            PublicCapabilityRunState.NO_CHANGE,
        }
        healthy_count = sum(item.state in healthy for item in self.capabilities)
        if self.status is PublicRunState.SUCCEEDED and healthy_count != len(self.capabilities):
            raise ValueError("succeeded runs require every capability to be healthy")
        hard_problem = {
            PublicCapabilityRunState.GAP,
            PublicCapabilityRunState.BLOCKED,
            PublicCapabilityRunState.FAILED,
        }
        if self.status is PublicRunState.FAILED and (
            healthy_count != 0 or not any(item.state in hard_problem for item in self.capabilities)
        ):
            raise ValueError("failed runs require only unhealthy capabilities and a hard problem")
        if self.status is PublicRunState.DEGRADED:
            unhealthy_count = len(self.capabilities) - healthy_count
            if unhealthy_count == 0:
                raise ValueError("degraded runs require at least one unhealthy capability")
        return self


@dataclass(frozen=True, slots=True)
class PublicSnapshotAssetContract:
    """Frozen model and published schema identity for one public asset kind."""

    model: type[ContractModel]
    schema_id: str
    schema_version: str


PUBLIC_SNAPSHOT_ASSET_CONTRACTS: Final[
    Mapping[PublicSnapshotAssetKind, PublicSnapshotAssetContract]
] = MappingProxyType(
    {
        PublicSnapshotAssetKind.BRIEFING: PublicSnapshotAssetContract(
            model=PremarketBriefingViewV1_3,
            schema_id="schema:briefings.premarket-view:v1.3",
            schema_version="1.3",
        ),
        PublicSnapshotAssetKind.EVIDENCE_INDEX: PublicSnapshotAssetContract(
            model=PublicEvidenceIndexV1,
            schema_id="schema:public-snapshots.evidence-index:v1.0",
            schema_version="1.0",
        ),
        PublicSnapshotAssetKind.EVIDENCE: PublicSnapshotAssetContract(
            model=EventEvidenceViewV1_2,
            schema_id="schema:events.event-evidence-view:v1.2",
            schema_version="1.2",
        ),
        PublicSnapshotAssetKind.RUN_STATUS: PublicSnapshotAssetContract(
            model=PublicRunStatusV1_1,
            schema_id="schema:public-snapshots.run-status:v1.1",
            schema_version="1.1",
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedPublicSnapshotBundle:
    """Typed result returned only after cross-document and digest validation."""

    pointer: PublicSnapshotPointerV1
    manifest: PublicSnapshotManifestV1_1
    briefing: PremarketBriefingViewV1_2 | PremarketBriefingViewV1_3
    evidence_index: PublicEvidenceIndexV1
    evidence_by_event_id: Mapping[UUID, EventEvidenceViewV1_2]
    run_status: PublicRunStatusV1_1


def _decode_json_document(label: str, document: bytes, *, max_bytes: int) -> object:
    if len(document) > max_bytes:
        raise ValueError(f"{label} exceeds its byte limit")
    try:
        return json.loads(document)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be one UTF-8 JSON document") from exc


def validate_public_snapshot_bundle(
    *,
    pointer_document: bytes,
    manifest_document: bytes,
    asset_documents: Mapping[str, bytes],
) -> ValidatedPublicSnapshotBundle:
    """Validate one exact public bundle before an S3 pointer switch or browser use."""

    pointer = PublicSnapshotPointerV1.model_validate(
        _decode_json_document("pointer", pointer_document, max_bytes=64 * 1024)
    )
    if hashlib.sha256(manifest_document).hexdigest() != pointer.manifest_sha256:
        raise ValueError("manifest bytes do not match pointer.manifest_sha256")
    manifest = PublicSnapshotManifestV1_1.model_validate(
        _decode_json_document("manifest", manifest_document, max_bytes=2 * 1024 * 1024)
    )
    if (
        pointer.snapshot_id != manifest.snapshot_id
        or pointer.product_commit != manifest.product_commit
        or pointer.published_at != manifest.published_at
    ):
        raise ValueError("pointer identity does not match the manifest")

    expected_paths = {asset.path for asset in manifest.assets}
    if set(asset_documents) != expected_paths:
        raise ValueError("asset documents must exactly match the manifest inventory")
    validated_assets: dict[str, ContractModel] = {}
    for asset in manifest.assets:
        document = asset_documents[asset.path]
        if len(document) != asset.size_bytes:
            raise ValueError(f"asset size does not match manifest: {asset.path}")
        if hashlib.sha256(document).hexdigest() != asset.sha256:
            raise ValueError(f"asset digest does not match manifest: {asset.path}")
        contract = PUBLIC_SNAPSHOT_ASSET_CONTRACTS[asset.kind]
        payload = _decode_json_document(
            f"{asset.kind.value} asset",
            document,
            max_bytes=10 * 1024 * 1024,
        )
        model = contract.model
        if (
            asset.kind is PublicSnapshotAssetKind.BRIEFING
            and isinstance(payload, dict)
            and payload.get("schema_version") == "1.2"
        ):
            model = PremarketBriefingViewV1_2
        try:
            validated_assets[asset.path] = model.model_validate(payload)
        except ValueError as exc:
            accepted_schema = (
                "schema:briefings.premarket-view:v1.2 or schema:briefings.premarket-view:v1.3"
                if asset.kind is PublicSnapshotAssetKind.BRIEFING
                else contract.schema_id
            )
            raise ValueError(f"{asset.kind.value} asset does not match {accepted_schema}") from exc

    def validated_singleton(kind: PublicSnapshotAssetKind) -> ContractModel:
        path = next(asset.path for asset in manifest.assets if asset.kind is kind)
        return validated_assets[path]

    briefing_value = validated_singleton(PublicSnapshotAssetKind.BRIEFING)
    evidence_index_value = validated_singleton(PublicSnapshotAssetKind.EVIDENCE_INDEX)
    run_status_value = validated_singleton(PublicSnapshotAssetKind.RUN_STATUS)
    if not isinstance(briefing_value, (PremarketBriefingViewV1_2, PremarketBriefingViewV1_3)):
        raise ValueError("briefing asset contract mapping is invalid")
    if not isinstance(evidence_index_value, PublicEvidenceIndexV1):
        raise ValueError("evidence index asset contract mapping is invalid")
    if not isinstance(run_status_value, PublicRunStatusV1_1):
        raise ValueError("run status asset contract mapping is invalid")
    briefing = briefing_value
    evidence_index = evidence_index_value
    run_status = run_status_value

    snapshot_prefix = f"data/snapshots/{manifest.snapshot_id}/"
    evidence_assets = {
        asset.path.removeprefix(snapshot_prefix): asset
        for asset in manifest.assets
        if asset.kind is PublicSnapshotAssetKind.EVIDENCE
    }
    indexed_paths = {item.path for item in evidence_index.events}
    if indexed_paths != set(evidence_assets):
        raise ValueError("evidence index must exactly match the manifest evidence assets")
    evidence_by_event_id: dict[UUID, EventEvidenceViewV1_2] = {}
    for item in evidence_index.events:
        asset = evidence_assets[item.path]
        evidence_value = validated_assets[asset.path]
        if not isinstance(evidence_value, EventEvidenceViewV1_2):
            raise ValueError("evidence asset contract mapping is invalid")
        if evidence_value.event_id != item.event_id:
            raise ValueError("evidence index event_id does not match its evidence document")
        evidence_by_event_id[item.event_id] = evidence_value

    if run_status.snapshot_id != manifest.snapshot_id:
        raise ValueError("run status snapshot_id does not match the manifest")
    if run_status.completed_at > manifest.generated_at:
        raise ValueError("run status completed_at cannot be later than manifest.generated_at")

    expected_status = {
        PublicSnapshotCompleteness.COMPLETE: PublicRunState.SUCCEEDED,
        PublicSnapshotCompleteness.PARTIAL: PublicRunState.DEGRADED,
        PublicSnapshotCompleteness.UNAVAILABLE: PublicRunState.FAILED,
    }[manifest.completeness]
    if run_status.status is not expected_status:
        raise ValueError("manifest completeness does not match run status")

    grades = {item.source_grade for item in run_status.capabilities}
    if manifest.origin_mode is PublicSnapshotOriginMode.DEMO_REPLAY:
        if grades != {PublicSourceGrade.DEMO_REPLAY}:
            raise ValueError("demo replay manifests require demo replay source grades")
    elif PublicSourceGrade.DEMO_REPLAY in grades:
        raise ValueError("dev live manifests cannot contain demo replay source grades")

    expected_runtime_mode = SourceRuntimeModeV1_2(manifest.origin_mode.value)
    if briefing.runtime_mode is not expected_runtime_mode:
        raise ValueError("briefing runtime_mode does not match the snapshot origin")
    if briefing.as_of != manifest.data_as_of:
        raise ValueError("briefing as_of does not match manifest.data_as_of")
    if any(
        item.runtime_mode is not briefing.runtime_mode for item in evidence_by_event_id.values()
    ):
        raise ValueError("evidence runtime_mode does not match the briefing")
    if any(item.as_of != manifest.data_as_of for item in evidence_by_event_id.values()):
        raise ValueError("evidence as_of does not match manifest.data_as_of")

    return ValidatedPublicSnapshotBundle(
        pointer=pointer,
        manifest=manifest,
        briefing=briefing,
        evidence_index=evidence_index,
        evidence_by_event_id=MappingProxyType(evidence_by_event_id),
        run_status=run_status,
    )


__all__ = [
    "PUBLIC_SNAPSHOT_ASSET_CONTRACTS",
    "PublicCapabilityRunState",
    "PublicCapabilityRunStatus",
    "PublicEvidenceIndexEntryV1",
    "PublicEvidenceIndexV1",
    "PublicRunState",
    "PublicRunStatusV1",
    "PublicRunStatusV1_1",
    "PublicSnapshotAsset",
    "PublicSnapshotAssetContract",
    "PublicSnapshotAssetKind",
    "PublicSnapshotCompleteness",
    "PublicSnapshotDeliveryMode",
    "PublicSnapshotManifestV1_1",
    "PublicSnapshotOriginMode",
    "PublicSnapshotPointerV1",
    "PublicSourceGrade",
    "ValidatedPublicSnapshotBundle",
    "validate_public_snapshot_bundle",
]
