"""Deterministic material-change detection after semantic candidate matching."""

from enum import StrEnum

from stock_assistant.contracts.events import MarketEvent


class ChangeType(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    UPDATE = "update"
    CORRECTION = "correction"
    CONTRADICTION = "contradiction"


def _fact_map(event: MarketEvent) -> dict[tuple[str, str | None], str]:
    return {(fact.name, fact.period): f"{fact.value}|{fact.unit or ''}" for fact in event.facts}


def compare_event_revision(previous: MarketEvent | None, current: MarketEvent) -> ChangeType:
    """Compare fact slots; embeddings are intentionally not the final arbiter."""

    if previous is None:
        return ChangeType.NEW
    if previous.cluster_id != current.cluster_id:
        return ChangeType.NEW
    if current.event_status.value == "corrected":
        return ChangeType.CORRECTION

    old_facts = _fact_map(previous)
    new_facts = _fact_map(current)
    shared = old_facts.keys() & new_facts.keys()
    changed_shared = {key for key in shared if old_facts[key] != new_facts[key]}
    if changed_shared and current.direction != previous.direction:
        return ChangeType.CONTRADICTION
    if changed_shared or old_facts.keys() != new_facts.keys():
        return ChangeType.UPDATE

    stable_fields = (
        previous.event_status == current.event_status
        and previous.direction == current.direction
        and previous.impact_horizon == current.impact_horizon
        and abs(previous.materiality - current.materiality) < 0.01
    )
    return ChangeType.DUPLICATE if stable_fields else ChangeType.UPDATE
