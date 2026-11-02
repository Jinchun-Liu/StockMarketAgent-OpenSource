"""Local and external OpenAI-compatible LLM adapters."""

from stock_assistant.platform.llm.deepseek import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_PRO_MODEL,
    DeepSeekLLMProvider,
    DeepSeekProviderError,
    build_deepseek_provider,
)
from stock_assistant.platform.llm.provider import (
    GenerationRequest,
    GenerationResponse,
    LLMProvider,
    LLMProviderPort,
)

__all__ = [
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_FLASH_MODEL",
    "DEEPSEEK_PRO_MODEL",
    "DeepSeekLLMProvider",
    "DeepSeekProviderError",
    "GenerationRequest",
    "GenerationResponse",
    "LLMProvider",
    "LLMProviderPort",
    "build_deepseek_provider",
]
from stock_assistant.platform.llm.synthetic_research import SyntheticResearchLLMProvider

__all__ = ["SyntheticResearchLLMProvider"]
