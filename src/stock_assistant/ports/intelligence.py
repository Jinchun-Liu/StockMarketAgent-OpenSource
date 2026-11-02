"""Provider-neutral ports and failures for model generation, embedding and reranking."""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from stock_assistant.contracts.intelligence import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    ModelProviderCapability,
    RerankRequest,
    RerankResponse,
)


class LLMProviderFailureKind(StrEnum):
    """Small internal classification used for retry and degradation decisions."""

    TRANSPORT = "transport"
    AUTH_CONFIG = "auth_config"
    INVALID_OUTPUT = "invalid_output"


class LLMProviderError(RuntimeError):
    """Safe provider-neutral model failure without upstream response content."""

    def __init__(
        self,
        code: str,
        *,
        failure_kind: LLMProviderFailureKind,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.failure_kind = failure_kind
        self.retryable = retryable


@runtime_checkable
class LLMProviderPort(Protocol):
    """Schema-constrained local or remote large-language-model provider."""

    @property
    def provider_name(self) -> str: ...

    @property
    def capability(self) -> ModelProviderCapability: ...

    async def generate(self, request: GenerationRequest) -> GenerationResponse: ...


@runtime_checkable
class EmbeddingProviderPort(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def capability(self) -> ModelProviderCapability: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...


@runtime_checkable
class RerankerPort(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def capability(self) -> ModelProviderCapability: ...

    async def rerank(self, request: RerankRequest) -> RerankResponse: ...
