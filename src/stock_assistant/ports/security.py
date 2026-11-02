"""Ports for authorization, audit, redaction and secret resolution."""

from typing import Protocol, runtime_checkable

from stock_assistant.contracts.security import (
    BearerTokenVerificationRequest,
    BearerTokenVerificationResult,
    SecretResolveRequest,
    SecretResolveResult,
)
from stock_assistant.contracts.system import (
    AuditWriteRequest,
    AuditWriteResult,
    AuthorizationDecision,
    AuthorizationRequest,
    RedactionRequest,
    RedactionResult,
)


@runtime_checkable
class AuthorizationPort(Protocol):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision: ...


@runtime_checkable
class AuthorizationIdentityPort(AuthorizationPort, Protocol):
    """Compatible extension that also verifies an external bearer identity."""

    async def authenticate(
        self, request: BearerTokenVerificationRequest
    ) -> BearerTokenVerificationResult: ...


@runtime_checkable
class AuditSinkPort(Protocol):
    async def record(self, request: AuditWriteRequest) -> AuditWriteResult: ...


@runtime_checkable
class RedactionPort(Protocol):
    async def redact(self, request: RedactionRequest) -> RedactionResult: ...


@runtime_checkable
class SecretProviderPort(Protocol):
    async def resolve(self, request: SecretResolveRequest) -> SecretResolveResult: ...
