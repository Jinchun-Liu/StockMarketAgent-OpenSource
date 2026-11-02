from stock_assistant.contracts.events import Direction, EventStatus
from stock_assistant.modules.events import ChangeType, compare_event_revision


def test_new_event_when_no_previous(event_factory) -> None:
    assert compare_event_revision(None, event_factory()) is ChangeType.NEW


def test_new_event_when_cluster_changes(event_factory) -> None:
    previous = event_factory()
    current = event_factory(revision=2)
    assert compare_event_revision(previous, current) is ChangeType.NEW


def test_exact_facts_are_duplicate(event_factory) -> None:
    previous = event_factory()
    current = event_factory(cluster_id=previous.cluster_id, revision=2)
    assert compare_event_revision(previous, current) is ChangeType.DUPLICATE


def test_numeric_change_is_update(event_factory) -> None:
    previous = event_factory(value="1")
    current = event_factory(cluster_id=previous.cluster_id, value="10", revision=2)
    assert compare_event_revision(previous, current) is ChangeType.UPDATE


def test_direction_reversal_with_changed_fact_is_contradiction(event_factory) -> None:
    previous = event_factory(value="1", direction=Direction.NEGATIVE)
    current = event_factory(
        cluster_id=previous.cluster_id,
        value="10",
        direction=Direction.POSITIVE,
        revision=2,
    )
    assert compare_event_revision(previous, current) is ChangeType.CONTRADICTION


def test_explicit_correction_wins(event_factory) -> None:
    previous = event_factory()
    current = event_factory(
        cluster_id=previous.cluster_id,
        status=EventStatus.CORRECTED,
        revision=2,
    )
    assert compare_event_revision(previous, current) is ChangeType.CORRECTION


def test_materiality_change_is_update(event_factory) -> None:
    previous = event_factory(materiality=0.8)
    current = event_factory(
        cluster_id=previous.cluster_id,
        materiality=0.6,
        revision=2,
    )
    assert compare_event_revision(previous, current) is ChangeType.UPDATE
