"""Compatibility exports for the former LLM port location.

New code imports contracts from ``stock_assistant.contracts.intelligence`` and the
port from ``stock_assistant.ports.intelligence``. This module remains until the
documented deprecation window ends so existing adapters do not break abruptly.
"""

from stock_assistant.contracts.intelligence import GenerationRequest, GenerationResponse
from stock_assistant.ports.intelligence import LLMProviderPort

LLMProvider = LLMProviderPort

__all__ = ["GenerationRequest", "GenerationResponse", "LLMProvider", "LLMProviderPort"]
