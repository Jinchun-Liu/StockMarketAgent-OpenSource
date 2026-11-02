"""Public event evidence read model independent from MarketEvent 1.0."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, HttpUrl, model_validator

from stock_assistant.contracts.base import ContractModel, DecimalString
from stock_assistant.contracts.briefings import BriefingChangeType
from stock_assistant.contracts.ingestion import (
    SourceProvenance,
    SourceProvenanceV1_2,
    SourceRuntimeMode,
    SourceRuntimeModeV1_2,
)
from stock_assistant.contracts.premarket_views import SourceRole


class TextFactValue(ContractModel):
    kind: Literal["text"]
    value: str = Field(min_length=1, max_length=500)


class DecimalFactValue(ContractModel):
    kind: Literal["decimal"]
    value: DecimalString


FactValue = Annotated[TextFactValue | DecimalFactValue, Field(discriminator="kind")]


def _semantic_fact_value(value: TextFactValue | DecimalFactValue) -> tuple[str, object]:
    """Compare facts by public type semantics, not their JSON spelling."""

    return (value.kind, value.value)


class ReplacementEvidenceRelation(ContractModel):
    """A current evidence record explicitly replaces a prior record."""

    kind: Literal["replacement"]
    prior_evidence_id: UUID


class RetractionEvidenceRelation(ContractModel):
    """A current evidence record explicitly retracts a prior record."""

    kind: Literal["retraction"]
    prior_evidence_id: UUID


EvidenceCorrectionRelation = Annotated[
    ReplacementEvidenceRelation | RetractionEvidenceRelation,
    Field(discriminator="kind"),
]


class ReplacementCorrection(ContractModel):
    """Directional old-to-new fact replacement with evidence on both sides."""

    kind: Literal["replacement"]
    slot: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    unit: str | None = Field(default=None, max_length=50)
    period: str | None = Field(default=None, max_length=100)
    previous_value: FactValue
    current_value: FactValue
    previous_evidence_ids: list[UUID] = Field(min_length=1, max_length=50)
    current_evidence_ids: list[UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def replacement_must_change_value_and_evidence(self) -> "ReplacementCorrection":
        if _semantic_fact_value(self.previous_value) == _semantic_fact_value(self.current_value):
            raise ValueError("replacement correction must change the fact value")
        _validate_directional_evidence(
            self.previous_evidence_ids,
            self.current_evidence_ids,
        )
        return self


class RetractionCorrection(ContractModel):
    """Directional retraction with prior-value and retraction-notice evidence."""

    kind: Literal["retraction"]
    slot: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    unit: str | None = Field(default=None, max_length=50)
    period: str | None = Field(default=None, max_length=100)
    previous_value: FactValue
    current_value: Literal[None] = None
    previous_evidence_ids: list[UUID] = Field(min_length=1, max_length=50)
    current_evidence_ids: list[UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def retraction_must_bind_distinct_evidence(self) -> "RetractionCorrection":
        _validate_directional_evidence(
            self.previous_evidence_ids,
            self.current_evidence_ids,
        )
        return self


CorrectionDetail = Annotated[
    ReplacementCorrection | RetractionCorrection,
    Field(discriminator="kind"),
]


def _validate_directional_evidence(
    previous_evidence_ids: list[UUID],
    current_evidence_ids: list[UUID],
) -> None:
    if len(set(previous_evidence_ids)) != len(previous_evidence_ids):
        raise ValueError("previous correction evidence_ids must be unique")
    if len(set(current_evidence_ids)) != len(current_evidence_ids):
        raise ValueError("current correction evidence_ids must be unique")
    if set(previous_evidence_ids) & set(current_evidence_ids):
        raise ValueError("previous and current correction evidence must be distinct")


class EventFactSlot(ContractModel):
    """One normalized fact value with explicit evidence support."""

    model_config = ConfigDict(
        json_schema_extra={"x-runtime-invariants": ["evidence_ids entries are unique"]}
    )

    slot: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    value: FactValue
    unit: str | None = Field(default=None, max_length=50)
    period: str | None = Field(default=None, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def fact_evidence_must_be_unique(self) -> "EventFactSlot":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("fact evidence_ids must be unique")
        return self


class EventEvidenceSource(ContractModel):
    """Public evidence metadata with full point-in-time timestamps."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "published_at <= first_seen_at <= fetched_at <= revision_known_at"
            ]
        }
    )

    evidence_id: UUID
    source_id: str = Field(min_length=1, max_length=100)
    source_role: SourceRole
    source_url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    published_at: AwareDatetime
    first_seen_at: AwareDatetime
    fetched_at: AwareDatetime
    revision_known_at: AwareDatetime
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_id: str = Field(min_length=1, max_length=100)
    rights_snapshot_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    correction_relation: EvidenceCorrectionRelation | None = None

    @model_validator(mode="after")
    def evidence_timestamps_must_be_ordered(self) -> "EventEvidenceSource":
        if not (
            self.published_at <= self.first_seen_at <= self.fetched_at <= self.revision_known_at
        ):
            raise ValueError(
                "evidence timestamps must satisfy published_at <= first_seen_at "
                "<= fetched_at <= revision_known_at"
            )
        return self


class EventEvidenceRevisionSelector(ContractModel):
    """Point-in-time query for one selected event revision and its bounded timeline."""

    schema_version: Literal["1.0"] = "1.0"
    as_of: AwareDatetime
    revision: int | None = Field(default=None, ge=1)
    timeline_before_revision: int | None = Field(default=None, ge=1)
    timeline_limit: int = Field(default=20, ge=1, le=50)


class EventRevisionTimelineItem(ContractModel):
    """One immutable revision identity safe to expose in the evidence timeline."""

    revision: int = Field(ge=1)
    change_type: BriefingChangeType
    revision_known_at: AwareDatetime
    supersedes_revision: int | None = Field(default=None, ge=1)
    extraction_version: str = Field(min_length=1, max_length=100)
    ruleset_version: str = Field(min_length=1, max_length=100)
    audit_id: UUID

    @model_validator(mode="after")
    def timeline_item_revision_must_be_directional(self) -> "EventRevisionTimelineItem":
        if self.change_type is BriefingChangeType.NEW:
            if self.revision != 1 or self.supersedes_revision is not None:
                raise ValueError(
                    "new timeline item requires revision 1 without supersedes_revision"
                )
        elif (
            self.revision <= 1
            or self.supersedes_revision is None
            or self.supersedes_revision >= self.revision
        ):
            raise ValueError("changed timeline item requires a prior superseded revision")
        return self


class EventRevisionTimeline(ContractModel):
    """A descending, bounded page of revision identities."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "items revisions are unique and strictly descending",
                "before_revision=null is the first page and must not be empty",
                "continuation page items have revision < before_revision",
                "an empty page is permitted only for before_revision=1 termination",
                (
                    "has_older=true requires next_before_revision to equal the "
                    "oldest returned revision and to be greater than 1"
                ),
                "has_older=false requires next_before_revision=null",
            ]
        }
    )

    before_revision: int | None = Field(default=None, ge=1)
    items: list[EventRevisionTimelineItem] = Field(default_factory=list, max_length=50)
    has_older: bool
    next_before_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def page_must_be_ordered_and_resumable(self) -> "EventRevisionTimeline":
        revisions = [item.revision for item in self.items]
        if not revisions:
            if self.before_revision != 1 or self.has_older or self.next_before_revision is not None:
                raise ValueError(
                    "empty timeline is permitted only as the before_revision=1 terminal page"
                )
            return self
        if len(set(revisions)) != len(revisions) or revisions != sorted(revisions, reverse=True):
            raise ValueError("timeline revisions must be unique and strictly descending")
        if self.before_revision is not None and any(
            revision >= self.before_revision for revision in revisions
        ):
            raise ValueError("continuation timeline revisions must be earlier than before_revision")
        if self.has_older:
            if self.next_before_revision != revisions[-1] or self.next_before_revision <= 1:
                raise ValueError("has_older timeline requires a resumable next_before_revision")
        elif self.next_before_revision is not None:
            raise ValueError("complete timeline cannot expose next_before_revision")
        return self


class EventEvidenceView(ContractModel):
    """Evidence, fact slots and revision semantics for one public event."""

    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": [
                "instrument_ids entries are unique",
                "evidence evidence_id values are unique",
                "all fact evidence_ids reference evidence records in this response",
                "all evidence.revision_known_at values are <= as_of",
                "selected revision_known_at is <= as_of and covers all selected evidence",
                "the first revision_timeline page contains the selected revision identity",
                (
                    "any revision_timeline page containing the selected revision "
                    "matches its identity exactly"
                ),
                "all revision_timeline.items revision_known_at values are <= as_of",
                "correction and contradiction require at least two evidence records",
                "new requires revision=1 and supersedes_revision=null",
                (
                    "update, correction and contradiction require revision>1 and "
                    "supersedes_revision<revision"
                ),
                (
                    "correction requires directional replacement/retraction values and "
                    "matching evidence relations; replacement facts bind only to their "
                    "previous or current evidence side"
                ),
                (
                    "every correction_relation is a closed edge from a registered current "
                    "evidence record to a distinct registered previous evidence record in "
                    "this response"
                ),
                (
                    "contradiction requires semantically distinct values for the same "
                    "slot/period/unit and every conflicting value has evidence not used "
                    "by the other conflicting values"
                ),
            ]
        }
    )

    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    cluster_id: UUID
    revision: int = Field(ge=1)
    change_type: BriefingChangeType
    instrument_ids: list[str] = Field(min_length=1, max_length=100)
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,99}$")
    title: str = Field(min_length=1, max_length=500)
    as_of: AwareDatetime
    revision_known_at: AwareDatetime
    supersedes_revision: int | None = Field(default=None, ge=1)
    extraction_version: str = Field(min_length=1, max_length=100)
    ruleset_version: str = Field(min_length=1, max_length=100)
    audit_id: UUID
    revision_timeline: EventRevisionTimeline
    facts: list[EventFactSlot] = Field(default_factory=list, max_length=100)
    evidence: list[EventEvidenceSource] = Field(min_length=1, max_length=50)
    correction: CorrectionDetail | None = None

    @model_validator(mode="after")
    def event_evidence_must_be_consistent(self) -> "EventEvidenceView":
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError("instrument_ids must be unique")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence IDs must be unique")
        evidence_id_set = set(evidence_ids)
        missing = {
            evidence_id
            for fact in self.facts
            for evidence_id in fact.evidence_ids
            if evidence_id not in evidence_id_set
        }
        if missing:
            raise ValueError("fact slots reference missing evidence")
        if any(item.revision_known_at > self.as_of for item in self.evidence):
            raise ValueError("event cannot use evidence known after as_of")
        if self.revision_known_at > self.as_of:
            raise ValueError("selected revision cannot be known after as_of")
        if any(item.revision_known_at > self.revision_known_at for item in self.evidence):
            raise ValueError("selected revision identity must cover all selected evidence")
        if any(item.revision_known_at > self.as_of for item in self.revision_timeline.items):
            raise ValueError("timeline cannot expose revisions known after as_of")
        selected = [item for item in self.revision_timeline.items if item.revision == self.revision]
        if self.revision_timeline.before_revision is None and len(selected) != 1:
            raise ValueError("first timeline page must contain the selected revision")
        if selected:
            selected_item = selected[0]
            selected_identity = (
                selected_item.change_type,
                selected_item.revision_known_at,
                selected_item.supersedes_revision,
                selected_item.extraction_version,
                selected_item.ruleset_version,
                selected_item.audit_id,
            )
            response_identity = (
                self.change_type,
                self.revision_known_at,
                self.supersedes_revision,
                self.extraction_version,
                self.ruleset_version,
                self.audit_id,
            )
            if selected_identity != response_identity:
                raise ValueError("selected revision identity does not match its timeline item")
        if self.change_type is BriefingChangeType.NEW:
            if self.revision != 1 or self.supersedes_revision is not None:
                raise ValueError("new event requires revision 1 without supersedes_revision")
        elif (
            self.revision <= 1
            or self.supersedes_revision is None
            or self.supersedes_revision >= self.revision
        ):
            raise ValueError(
                "update, correction and contradiction require a prior superseded revision"
            )
        if self.change_type is BriefingChangeType.CORRECTION:
            self._validate_correction(evidence_id_set)
        elif self.correction is not None or any(
            item.correction_relation is not None for item in self.evidence
        ):
            raise ValueError("only correction events may carry correction details or relations")
        if (
            self.change_type in {BriefingChangeType.CORRECTION, BriefingChangeType.CONTRADICTION}
            and len(self.evidence) < 2
        ):
            raise ValueError("correction and contradiction require at least two evidence records")
        if self.change_type is not BriefingChangeType.CORRECTION and not self.facts:
            raise ValueError("non-correction events require at least one fact slot")
        if self.change_type is BriefingChangeType.CONTRADICTION:
            self._validate_contradiction()
        return self

    def _validate_correction(self, evidence_ids: set[UUID]) -> None:
        correction = self.correction
        if correction is None:
            raise ValueError("correction requires directional correction detail")
        previous_ids = set(correction.previous_evidence_ids)
        current_ids = set(correction.current_evidence_ids)
        if not previous_ids | current_ids <= evidence_ids:
            raise ValueError("correction detail references missing evidence")

        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        for evidence in self.evidence:
            relation = evidence.correction_relation
            if relation is None:
                continue
            if relation.prior_evidence_id not in evidence_ids:
                raise ValueError(
                    "correction relation references prior evidence missing from the response"
                )
            if evidence.evidence_id == relation.prior_evidence_id:
                raise ValueError("correction relation cannot reference its own evidence record")
            if evidence.evidence_id not in current_ids:
                raise ValueError(
                    "evidence carrying a correction relation must be registered in "
                    "current_evidence_ids"
                )
            if relation.prior_evidence_id not in previous_ids:
                raise ValueError(
                    "correction relation prior must be registered in previous_evidence_ids"
                )
            if relation.kind != correction.kind:
                raise ValueError(
                    "correction relation kind must match the directional correction kind"
                )
        for current_id in current_ids:
            relation = evidence_by_id[current_id].correction_relation
            if relation is None:
                raise ValueError(
                    "current correction evidence requires a matching "
                    "replacement/retraction relation"
                )

        if isinstance(correction, ReplacementCorrection):
            corrected_slot = (correction.slot, correction.period, correction.unit)
            previous_value = _semantic_fact_value(correction.previous_value)
            current_value = _semantic_fact_value(correction.current_value)
            has_current_fact = False
            for fact in self.facts:
                if (fact.slot, fact.period, fact.unit) != corrected_slot:
                    continue
                fact_evidence = set(fact.evidence_ids)
                fact_value = _semantic_fact_value(fact.value)
                if fact_value == current_value:
                    if fact_evidence & previous_ids:
                        raise ValueError(
                            "current replacement fact cannot reference previous evidence"
                        )
                    if not fact_evidence <= current_ids:
                        raise ValueError(
                            "current replacement fact must reference only current evidence"
                        )
                    has_current_fact = True
                elif fact_value == previous_value:
                    if fact_evidence & current_ids:
                        raise ValueError(
                            "previous replacement fact cannot reference current evidence"
                        )
                    if not fact_evidence <= previous_ids:
                        raise ValueError(
                            "previous replacement fact must reference only previous evidence"
                        )
                else:
                    raise ValueError(
                        "replacement correction facts for the corrected slot must expose "
                        "the previous or current value"
                    )
            if not has_current_fact:
                raise ValueError(
                    "replacement correction facts must expose the current evidence-bound value"
                )
        elif self.facts:
            raise ValueError("retraction correction must not expose a current fact value")

    def _validate_contradiction(self) -> None:
        groups: dict[
            tuple[str, str | None, str | None],
            dict[tuple[str, object], set[UUID]],
        ] = {}
        for fact in self.facts:
            claim_group = groups.setdefault((fact.slot, fact.period, fact.unit), {})
            claim_group.setdefault(_semantic_fact_value(fact.value), set()).update(
                fact.evidence_ids
            )

        conflicting_groups = [claims for claims in groups.values() if len(claims) > 1]
        if not conflicting_groups:
            raise ValueError(
                "contradiction requires conflicting values supported by different evidence"
            )
        for claims in conflicting_groups:
            evidence_sets = list(claims.values())
            for index, claim_evidence in enumerate(evidence_sets):
                other_evidence = set().union(
                    *(
                        item
                        for other_index, item in enumerate(evidence_sets)
                        if other_index != index
                    )
                )
                if not claim_evidence - other_evidence:
                    raise ValueError(
                        "each conflicting value requires evidence exclusive to that claim"
                    )


class EventEvidenceSourceV1_1(EventEvidenceSource):
    """R1 1.1 evidence metadata with event time and provider lineage."""

    event_time: AwareDatetime
    provenance: SourceProvenance
    model_config = ConfigDict(
        json_schema_extra={"x-runtime-invariants": ["event_time is not later than first_seen_at"]}
    )

    @model_validator(mode="after")
    def event_time_must_be_visible_when_first_seen(self) -> "EventEvidenceSourceV1_1":
        if self.event_time > self.first_seen_at:
            raise ValueError("event_time cannot be later than first_seen_at")
        return self


class EventEvidenceViewV1_1(EventEvidenceView):
    """R1 1.1 evidence response with a single explicit runtime mode."""

    schema_version: Literal["1.1"] = "1.1"  # type: ignore[assignment]
    runtime_mode: SourceRuntimeMode
    evidence: list[EventEvidenceSourceV1_1] = Field(  # type: ignore[assignment]
        min_length=1,
        max_length=50,
    )
    model_config = ConfigDict(
        json_schema_extra={
            "x-runtime-invariants": ["every evidence provenance runtime_mode equals runtime_mode"]
        }
    )

    @model_validator(mode="after")
    def evidence_runtime_modes_must_match(self) -> "EventEvidenceViewV1_1":
        if any(item.provenance.runtime_mode is not self.runtime_mode for item in self.evidence):
            raise ValueError("evidence provenance runtime_mode must match the response")
        return self


class EventEvidenceSourceV1_2(EventEvidenceSource):
    """R1 1.2 evidence metadata with complete provider lineage."""

    event_time: AwareDatetime
    provenance: SourceProvenanceV1_2

    @model_validator(mode="after")
    def event_time_must_be_visible_when_first_seen(self) -> "EventEvidenceSourceV1_2":
        if self.event_time > self.first_seen_at:
            raise ValueError("event_time cannot be later than first_seen_at")
        return self


class EventEvidenceViewV1_2(EventEvidenceView):
    """R1 1.2 evidence response with one explicit source runtime mode."""

    schema_version: Literal["1.2"] = "1.2"  # type: ignore[assignment]
    runtime_mode: SourceRuntimeModeV1_2
    evidence: list[EventEvidenceSourceV1_2] = Field(  # type: ignore[assignment]
        min_length=1,
        max_length=50,
    )

    @model_validator(mode="after")
    def evidence_runtime_modes_must_match(self) -> "EventEvidenceViewV1_2":
        if any(item.provenance.runtime_mode is not self.runtime_mode for item in self.evidence):
            raise ValueError("evidence provenance runtime_mode must match the response")
        return self
