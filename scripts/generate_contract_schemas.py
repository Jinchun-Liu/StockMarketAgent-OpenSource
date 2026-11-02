"""Generate versioned JSON Schemas from canonical Pydantic wire contracts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from stock_assistant.contracts.active_targets import (
    ActiveTargetMutationResult,
    ActiveTargetSelectionRequest,
    ActiveTargetState,
)
from stock_assistant.contracts.behavior import (
    BehaviorBandTransitionV1,
    BehaviorComputeRequest,
    BehaviorPressureSnapshotV1_0,
)
from stock_assistant.contracts.briefings import Briefing
from stock_assistant.contracts.discovery import ResearchAcquisitionState, SourceDiscoveryResult
from stock_assistant.contracts.event_views import (
    EventEvidenceRevisionSelector,
    EventEvidenceView,
    EventEvidenceViewV1_1,
    EventEvidenceViewV1_2,
)
from stock_assistant.contracts.events import MarketEvent
from stock_assistant.contracts.global_events import PolicyRefreshRequest, PolicyRefreshResult
from stock_assistant.contracts.ingestion import (
    RightsCheckV1_1,
    RightsDecisionV1_1,
    SourceProvenance,
    SourceProvenanceV1_2,
)
from stock_assistant.contracts.instruments import InstrumentSearchResult, InstrumentSearchResultV1_1
from stock_assistant.contracts.notifications import Notification
from stock_assistant.contracts.premarket_jobs import (
    PremarketBriefJobRequest,
    PremarketBriefJobResult,
    PremarketIngestJobRequest,
    PremarketIngestJobRequestV1_1,
    PremarketIngestJobRequestV1_2,
    PremarketIngestJobResult,
    PremarketIngestJobResultV1_1,
    PremarketIngestJobResultV1_2,
)
from stock_assistant.contracts.premarket_views import (
    PremarketBriefingView,
    PremarketBriefingViewV1_1,
    PremarketBriefingViewV1_2,
    PremarketBriefingViewV1_3,
)
from stock_assistant.contracts.public_snapshots import (
    PublicEvidenceIndexV1,
    PublicRunStatusV1,
    PublicRunStatusV1_1,
    PublicSnapshotManifestV1_1,
    PublicSnapshotPointerV1,
)
from stock_assistant.contracts.research import (
    ResearchAvailableResult,
    ResearchCheckpoint,
    ResearchEvidencePacket,
    ResearchGenerateRequest,
    ResearchGenerateRequestV1_1,
    ResearchGenerateResult,
    ResearchReportReadyV1,
    ResearchReportV1_1,
    ResearchReportV1_2,
    ResearchReportV2,
    ResearchReportV2_1,
    ResearchReportV2_2,
)
from stock_assistant.contracts.research_v1 import ResearchReport as ResearchReportV1_0
from stock_assistant.contracts.security import RuntimeRealWebConfig, RuntimeWebConfig

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_ROOT: Final = "https://stock-market-agent.local/schemas"


@dataclass(frozen=True, slots=True)
class SchemaTarget:
    """A published wire contract and its immutable versioned schema path."""

    model: type[BaseModel]
    version: str
    relative_path: Path

    @property
    def output_path(self) -> Path:
        return PROJECT_ROOT / self.relative_path

    @property
    def schema_id(self) -> str:
        return f"{SCHEMA_ID_ROOT}/{self.relative_path.relative_to('schemas').as_posix()}"

    @property
    def qualified_model_name(self) -> str:
        return f"{self.model.__module__}.{self.model.__qualname__}"


SCHEMA_TARGETS: Final = (
    SchemaTarget(
        model=Briefing,
        version="1.0",
        relative_path=Path("schemas/briefings/briefing.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=MarketEvent,
        version="1.0",
        relative_path=Path("schemas/events/market-event.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PolicyRefreshRequest,
        version="1.0",
        relative_path=Path("schemas/jobs/policy-refresh-request.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PolicyRefreshResult,
        version="1.0",
        relative_path=Path("schemas/jobs/policy-refresh-result.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=Notification,
        version="1.0",
        relative_path=Path("schemas/notifications/notification.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PremarketIngestJobRequest,
        version="1.0",
        relative_path=Path("schemas/jobs/premarket-ingest-request.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PremarketIngestJobResult,
        version="1.0",
        relative_path=Path("schemas/jobs/premarket-ingest-result.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PremarketBriefJobRequest,
        version="1.0",
        relative_path=Path("schemas/jobs/premarket-generate-request.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PremarketBriefJobResult,
        version="1.0",
        relative_path=Path("schemas/jobs/premarket-generate-result.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ActiveTargetSelectionRequest,
        version="1.0",
        relative_path=Path("schemas/active-targets/selection-request.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ActiveTargetState,
        version="1.0",
        relative_path=Path("schemas/active-targets/state.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ActiveTargetMutationResult,
        version="1.0",
        relative_path=Path("schemas/active-targets/mutation-result.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PremarketBriefingView,
        version="1.0",
        relative_path=Path("schemas/briefings/premarket-view.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=EventEvidenceView,
        version="1.0",
        relative_path=Path("schemas/events/event-evidence-view.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=EventEvidenceRevisionSelector,
        version="1.0",
        relative_path=Path("schemas/events/event-evidence-selector.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=RightsCheckV1_1,
        version="1.1",
        relative_path=Path("schemas/ingestion/rights-check.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=RightsDecisionV1_1,
        version="1.1",
        relative_path=Path("schemas/ingestion/rights-decision.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=SourceProvenance,
        version="1.1",
        relative_path=Path("schemas/ingestion/source-provenance.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=PremarketIngestJobRequestV1_1,
        version="1.1",
        relative_path=Path("schemas/jobs/premarket-ingest-request.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=PremarketIngestJobResultV1_1,
        version="1.1",
        relative_path=Path("schemas/jobs/premarket-ingest-result.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=PremarketBriefingViewV1_1,
        version="1.1",
        relative_path=Path("schemas/briefings/premarket-view.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=EventEvidenceViewV1_1,
        version="1.1",
        relative_path=Path("schemas/events/event-evidence-view.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=SourceProvenanceV1_2,
        version="1.2",
        relative_path=Path("schemas/ingestion/source-provenance.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=PremarketIngestJobRequestV1_2,
        version="1.2",
        relative_path=Path("schemas/jobs/premarket-ingest-request.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=PremarketIngestJobResultV1_2,
        version="1.2",
        relative_path=Path("schemas/jobs/premarket-ingest-result.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=PremarketBriefingViewV1_2,
        version="1.2",
        relative_path=Path("schemas/briefings/premarket-view.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=PremarketBriefingViewV1_3,
        version="1.3",
        relative_path=Path("schemas/briefings/premarket-view.v1.3.schema.json"),
    ),
    SchemaTarget(
        model=BehaviorPressureSnapshotV1_0,
        version="1.0",
        relative_path=Path("schemas/behavior/behavior-pressure-snapshot.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=BehaviorComputeRequest,
        version="1.0",
        relative_path=Path("schemas/jobs/behavior-compute-request.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=BehaviorBandTransitionV1,
        version="1.0",
        relative_path=Path("schemas/events/behavior-band-transition.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=EventEvidenceViewV1_2,
        version="1.2",
        relative_path=Path("schemas/events/event-evidence-view.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=PublicSnapshotPointerV1,
        version="1.0",
        relative_path=Path("schemas/public-snapshots/pointer.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PublicSnapshotManifestV1_1,
        version="1.1",
        relative_path=Path("schemas/public-snapshots/manifest.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=PublicEvidenceIndexV1,
        version="1.0",
        relative_path=Path("schemas/public-snapshots/evidence-index.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PublicRunStatusV1,
        version="1.0",
        relative_path=Path("schemas/public-snapshots/run-status.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=PublicRunStatusV1_1,
        version="1.1",
        relative_path=Path("schemas/public-snapshots/run-status.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=InstrumentSearchResult,
        version="1.0",
        relative_path=Path("schemas/instruments/search-result.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=InstrumentSearchResultV1_1,
        version="1.1",
        relative_path=Path("schemas/instruments/search-result.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=ResearchGenerateRequest,
        version="1.0",
        relative_path=Path("schemas/jobs/research-generate-request.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ResearchGenerateRequestV1_1,
        version="1.1",
        relative_path=Path("schemas/jobs/research-generate-request.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=ResearchGenerateResult,
        version="1.0",
        relative_path=Path("schemas/jobs/research-generate-result.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportReadyV1,
        version="1.0",
        relative_path=Path("schemas/events/research-report-ready.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportV1_0,
        version="1.0",
        relative_path=Path("schemas/research/report.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportV1_1,
        version="1.1",
        relative_path=Path("schemas/research/report.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportV1_2,
        version="1.2",
        relative_path=Path("schemas/research/report.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportV2,
        version="2.0",
        relative_path=Path("schemas/research/report.v2.0.schema.json"),
    ),
    SchemaTarget(
        model=RuntimeWebConfig,
        version="1.0",
        relative_path=Path("schemas/security/runtime-web-config.v1.0.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportV2_1,
        version="2.1",
        relative_path=Path("schemas/research/report.v2.1.schema.json"),
    ),
    SchemaTarget(
        model=RuntimeRealWebConfig,
        version="1.1",
        relative_path=Path("schemas/security/runtime-web-config.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=SourceDiscoveryResult,
        version="1.3",
        relative_path=Path("schemas/discovery/result.v1.3.schema.json"),
    ),
    SchemaTarget(
        model=ResearchAcquisitionState,
        version="1.3",
        relative_path=Path("schemas/research/acquisition-state.v1.3.schema.json"),
    ),
    SchemaTarget(
        model=ResearchReportV2_2,
        version="2.2",
        relative_path=Path("schemas/research/report.v2.2.schema.json"),
    ),
    SchemaTarget(
        model=ResearchAvailableResult,
        version="2.1",
        relative_path=Path("schemas/research/available-result.v2.1.schema.json"),
    ),
    SchemaTarget(
        model=SourceDiscoveryResult,
        version="1.2",
        relative_path=Path("schemas/discovery/result.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=ResearchAcquisitionState,
        version="1.2",
        relative_path=Path("schemas/research/acquisition-state.v1.2.schema.json"),
    ),
    SchemaTarget(
        model=ResearchCheckpoint,
        version="1.8",
        relative_path=Path("schemas/research/checkpoint.v1.8.schema.json"),
    ),
    SchemaTarget(
        model=ResearchEvidencePacket,
        version="1.1",
        relative_path=Path("schemas/research/packet.v1.1.schema.json"),
    ),
    SchemaTarget(
        model=ResearchCheckpoint,
        version="1.9",
        relative_path=Path("schemas/research/checkpoint.v1.9.schema.json"),
    ),
)


def _project_pre_formal_minor(schema: dict[str, object]) -> None:
    """Reproduce frozen minor shapes from Pydantic, retaining full drift checks."""
    import copy
    from typing import Any, cast

    root = cast(dict[str, Any], schema)
    fields = {
        "ResearchEvidenceCitation": ("formal_document",),
        "SourceDiscoveryCandidate": ("official_index", "direct_attachment"),
        "ResearchAcquisitionDocument": ("formal_document",),
        "ResearchAcquisitionState": ("verification_outcomes", "attachment_queue"),
    }
    versions = {
        "SourceDiscoveryCandidate": (["1.0"], "1.0"),
        "SourceDiscoveryResult": (["1.0", "1.1", "1.2"], "1.2"),
        "ResearchAcquisitionState": (["1.0", "1.1", "1.2"], "1.2"),
        "ResearchEvidencePacket": (["1.0"], "1.0"),
        "ResearchAvailableResult": (["1.0", "1.1", "2.0"], "2.0"),
        "ResearchReport": (["1.0", "1.1", "1.2", "2.0", "2.1"], "1.0"),
        "ResearchCheckpoint": (
            ["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"],
            "1.8",
        ),
    }
    for node in [root, *root.get("$defs", {}).values()]:
        title = node.get("title")
        props = node.get("properties", {})
        for field in fields.get(title, ()):
            props.pop(field, None)
        if title in versions:
            allowed, default = versions[title]
            version = props["schema_version"]
            version.pop("enum", None)
            version.pop("const", None)
            version["default"] = default
            version["const" if len(allowed) == 1 else "enum"] = (
                allowed[0] if len(allowed) == 1 else allowed
            )
    # Remove only unreachable type definitions after the explicit minor projection.
    definitions = root.pop("$defs", {})
    reachable: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in reachable:
                    reachable.add(name)
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(copy.deepcopy(root))
    if reachable:
        root["$defs"] = {name: definitions[name] for name in sorted(reachable)}


def render_schema(target: SchemaTarget) -> str:
    """Return the canonical, deterministic JSON representation for a target."""
    model_version = target.model.model_fields["schema_version"].default
    historical = (target.model, target.version) in {
        (SourceDiscoveryResult, "1.2"),
        (ResearchAcquisitionState, "1.2"),
        (ResearchCheckpoint, "1.8"),
    }
    if model_version != target.version and not historical:
        msg = (
            f"{target.qualified_model_name} declares schema_version={model_version!r}, "
            f"but its target declares {target.version!r}"
        )
        raise ValueError(msg)

    schema = target.model.model_json_schema(mode="serialization")
    if historical:
        _project_pre_formal_minor(schema)
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["$id"] = target.schema_id
    schema["x-contract-model"] = (
        "stock_assistant.contracts.research.ResearchReport"
        if target.model is ResearchReportV1_0
        else target.qualified_model_name
    )
    schema["x-schema-version"] = target.version
    if target.model in {ResearchReportV1_0, ResearchReportV1_1, ResearchReportV1_2}:
        schema["x-runtime-invariants"] = [
            "each of the ten report sections appears exactly once",
            "every claim is referenced by exactly one matching section",
            "fact claims have readable evidence and no calculation",
            "system inference calculation evidence resolves to readable claim evidence",
            "model hypotheses have support, counter-evidence and falsification conditions",
            "unattributed narrative cannot introduce numeric claims",
            "published narrative forbids trading advice, target prices and return promises",
            "all cited revisions are visible at or before as_of",
        ]
    elif target.model is ResearchGenerateRequestV1_1:
        schema["x-runtime-invariants"] = [
            "context.deadline_at is required and must be an absolute timezone-aware timestamp",
            "schema version 1.0 is interpreted as historical_replay and remains "
            "separately published",
        ]
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def find_schema_drift() -> tuple[Path, ...]:
    """Return generated schema paths that are missing or differ from their model."""
    drifted: list[Path] = []
    for target in SCHEMA_TARGETS:
        expected = render_schema(target)
        if not target.output_path.is_file():
            drifted.append(target.relative_path)
            continue
        if target.output_path.read_text(encoding="utf-8") != expected:
            drifted.append(target.relative_path)
    return tuple(drifted)


def write_schemas() -> None:
    """Write all generated schemas to their canonical versioned locations."""
    for target in SCHEMA_TARGETS:
        target.output_path.parent.mkdir(parents=True, exist_ok=True)
        target.output_path.write_text(render_schema(target), encoding="utf-8")
        print(f"generated {target.relative_path}")


def check_schemas() -> int:
    """Report checked-in schema drift and return a process exit status."""
    drifted = find_schema_drift()
    if not drifted:
        print("contract schemas are up to date")
        return 0

    print("contract schema drift detected:", file=sys.stderr)
    for path in drifted:
        print(f"  - {path}", file=sys.stderr)
    print(
        "run: python scripts/generate_contract_schemas.py --write",
        file=sys.stderr,
    )
    return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate checked-in schemas")
    mode.add_argument("--check", action="store_true", help="fail when checked-in schemas drift")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write:
        write_schemas()
        return 0
    return check_schemas()


if __name__ == "__main__":
    raise SystemExit(main())
