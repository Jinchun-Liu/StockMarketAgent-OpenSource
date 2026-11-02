"""FastAPI identity dependency for the authenticated R2 preview."""

from __future__ import annotations

from typing import Annotated

from fastapi import Request, Security
from fastapi.security import OAuth2AuthorizationCodeBearer
from pydantic import SecretStr

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.common.time import utc_now
from stock_assistant.config import Settings, get_settings
from stock_assistant.contracts.security import (
    AuthenticatedPrincipal,
    BearerTokenVerificationRequest,
)
from stock_assistant.platform.identity import (
    CognitoAuthorizationError,
    CognitoAuthorizationV1_1,
)

oauth2_authorization_code = OAuth2AuthorizationCodeBearer(
    authorizationUrl="https://auth.stockmarketagent.invalid/oauth2/authorize",
    tokenUrl="https://auth.stockmarketagent.invalid/oauth2/token",
    scopes={"openid": "Authenticate the private R2 preview user."},
    auto_error=False,
    scheme_name="CognitoAuthorizationCodePKCE",
    description=(
        "Authorization Code with PKCE. Runtime endpoints and client ID are supplied by "
        "runtime-config.json; this contract never accepts a client secret."
    ),
)


def validate_private_preview_identity_configuration(settings: Settings) -> None:
    """Fail the API process before serving when its identity boundary is incomplete."""

    if (
        not settings.enable_r2_private_preview
        or settings.cognito_issuer is None
        or settings.cognito_client_id is None
        or settings.cognito_jwks_url is None
        or settings.cognito_jwks_proxy_url is None
    ):
        raise ApplicationError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "R2_PRIVATE_PREVIEW_IDENTITY_NOT_CONFIGURED",
        )


def _authorization_adapter(request: Request) -> CognitoAuthorizationV1_1:
    existing: object = getattr(request.app.state, "r2_authorization", None)
    if isinstance(existing, CognitoAuthorizationV1_1):
        return existing
    settings = get_settings()
    validate_private_preview_identity_configuration(settings)
    assert settings.cognito_issuer is not None
    assert settings.cognito_client_id is not None
    assert settings.cognito_jwks_url is not None
    assert settings.cognito_jwks_proxy_url is not None
    adapter = CognitoAuthorizationV1_1(
        issuer=str(settings.cognito_issuer).rstrip("/"),
        client_id=settings.cognito_client_id,
        jwks_url=str(settings.cognito_jwks_url),
        proxy_url=str(settings.cognito_jwks_proxy_url),
        username_claim=settings.cognito_username_claim,
    )
    request.app.state.r2_authorization = adapter
    return adapter


async def require_principal(
    request: Request,
    encoded_bearer: Annotated[str | None, Security(oauth2_authorization_code)],
) -> AuthenticatedPrincipal:
    if encoded_bearer is None:
        raise ApplicationError(ErrorCode.UNAUTHENTICATED, "AUTH_BEARER_REQUIRED")
    try:
        result = await _authorization_adapter(request).authenticate(
            BearerTokenVerificationRequest(
                schema_version="1.0",
                access_token=SecretStr(encoded_bearer),
                verified_at=utc_now(),
            )
        )
    except CognitoAuthorizationError as error:
        code = ErrorCode.DEPENDENCY_UNAVAILABLE if error.retryable else ErrorCode.UNAUTHENTICATED
        raise ApplicationError(code, error.reason_code, retryable=error.retryable) from error
    return result.principal


PrincipalDependency = Annotated[AuthenticatedPrincipal, Security(require_principal)]

__all__ = [
    "PrincipalDependency",
    "oauth2_authorization_code",
    "require_principal",
    "validate_private_preview_identity_configuration",
]
