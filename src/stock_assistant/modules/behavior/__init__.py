"""Deterministic R1 behavior-pressure research rules and application service."""

from stock_assistant.modules.behavior.analogs import (
    AnalogObservation,
    AnalogSelection,
    behavior_analog_vector,
    select_analogs,
)
from stock_assistant.modules.behavior.dev_reference import (
    DevOnlyBehaviorInputs,
    build_dev_only_behavior_inputs,
    validated_dev_only_behavior_inputs,
)
from stock_assistant.modules.behavior.scoring import (
    BehaviorScoreResult,
    compose_behavior_pressure,
)
from stock_assistant.modules.behavior.transitions import (
    TransitionDecision,
    TransitionObservation,
    advance_transition,
)

__all__ = [
    "AnalogObservation",
    "AnalogSelection",
    "BehaviorScoreResult",
    "DevOnlyBehaviorInputs",
    "TransitionDecision",
    "TransitionObservation",
    "advance_transition",
    "behavior_analog_vector",
    "build_dev_only_behavior_inputs",
    "compose_behavior_pressure",
    "select_analogs",
    "validated_dev_only_behavior_inputs",
]
