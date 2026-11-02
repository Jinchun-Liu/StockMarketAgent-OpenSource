"""Fail-closed Cognito claim and algorithm regressions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import apps.api.identity as api_identity
import jwt
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from tests.unit.qir.test_cognito_authorization import (
    CLIENT_ID,
    ISSUER,
    JWKS_URL,
    KID,
    _claims,
    _key_material,
    _transport,
)

from stock_assistant.common.errors import ApplicationError, ErrorCode
from stock_assistant.config import Settings
from stock_assistant.contracts.security import BearerTokenVerificationRequest
from stock_assistant.platform.identity.cognito import (
    CognitoAuthorizationError,
    CognitoAuthorizationV1_1,
)


def _adapter(jwk: dict[str, object]) -> CognitoAuthorizationV1_1:
    return CognitoAuthorizationV1_1(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        jwks_url=JWKS_URL,
        transport=_transport(jwk),
    )


@pytest.mark.parametrize(
    "issuer",
    [
        "https://cognito-idp.ap-southeast-1.amazonaws.com/ap-northeast-1_preview",
        "https://example.amazonaws.com/ap-northeast-1_preview",
        "https://cognito-idp.ap-northeast-1.amazonaws.com/foreign-preview",
        "https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_preview/extra",
    ],
)
def test_rejects_identity_configuration_outside_the_fixed_user_pool(issuer: str) -> None:
    with pytest.raises(ValueError, match="fixed HTTPS AWS user-pool URL"):
        CognitoAuthorizationV1_1(
            issuer=issuer,
            client_id=CLIENT_ID,
            jwks_url=f"{issuer}/.well-known/jwks.json",
        )


@pytest.mark.parametrize(
    "proxy_url",
    [
        "https://egress.stockmarketagent-r2-preview.internal:3128",
        "http://attacker.example:3128",
        "http://egress.stockmarketagent-r2-preview.internal:8080",
        "http://user@egress.stockmarketagent-r2-preview.internal:3128",
    ],
)
def test_rejects_unbounded_cognito_jwks_proxy(proxy_url: str) -> None:
    with pytest.raises(ValueError, match="fixed internal egress service"):
        CognitoAuthorizationV1_1(
            issuer=ISSUER,
            client_id=CLIENT_ID,
            jwks_url=JWKS_URL,
            proxy_url=proxy_url,
        )


def test_api_identity_fails_closed_without_explicit_jwks_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api_identity,
        "get_settings",
        lambda: SimpleNamespace(
            enable_r2_private_preview=True,
            cognito_issuer=ISSUER,
            cognito_client_id=CLIENT_ID,
            cognito_jwks_url=JWKS_URL,
            cognito_jwks_proxy_url=None,
        ),
    )
    request = Request({"type": "http", "app": FastAPI()})

    with pytest.raises(ApplicationError) as caught:
        api_identity._authorization_adapter(request)

    assert caught.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert caught.value.safe_message == "R2_PRIVATE_PREVIEW_IDENTITY_NOT_CONFIGURED"


def test_api_startup_rejects_shared_preview_settings_without_identity() -> None:
    settings = Settings(
        app_env="preview",
        enable_r2_synthetic_research=True,
        enable_r2_private_preview=True,
        r2_artifact_bucket="stockmarketagent-r2-artifacts-preview",
        product_commit="a" * 40,
        aws_region="ap-northeast-1",
        _env_file=None,
    )

    with pytest.raises(ApplicationError, match="R2_PRIVATE_PREVIEW_IDENTITY_NOT_CONFIGURED"):
        api_identity.validate_private_preview_identity_configuration(settings)


async def _reject(claims: dict[str, object], now: datetime) -> None:
    signing_material, jwk = _key_material()
    encoded = jwt.encode(
        claims,
        signing_material,
        algorithm="RS256",
        headers={"kid": KID, "typ": "JWT"},
    )
    with pytest.raises(CognitoAuthorizationError, match="AUTH_TOKEN_INVALID"):
        await _adapter(jwk).authenticate(
            BearerTokenVerificationRequest(
                schema_version="1.0", access_token=encoded, verified_at=now
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_name", "claim_value"),
    [
        ("iss", "wrong-issuer"),
        ("client_id", "wrong-client"),
        ("token_use", "id"),
        ("username", ""),
        ("scope", 7),
    ],
)
async def test_rejects_invalid_claims(claim_name: str, claim_value: object) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    claims = _claims(now)
    claims.update({claim_name: claim_value})
    await _reject(claims, now)


@pytest.mark.asyncio
async def test_rejects_expired_token() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    await _reject(_claims(now - timedelta(minutes=20)), now)


@pytest.mark.asyncio
async def test_rejects_algorithm_fallback_before_key_fetch() -> None:
    _, jwk = _key_material()
    now = datetime.now(UTC).replace(microsecond=0)
    encoded = jwt.encode(
        _claims(now),
        "synthetic-invalid-material-for-test-only",
        algorithm="HS256",
        headers={"kid": KID, "typ": "JWT"},
    )
    with pytest.raises(CognitoAuthorizationError, match="AUTH_ALGORITHM_REJECTED"):
        await _adapter(jwk).authenticate(
            BearerTokenVerificationRequest(
                schema_version="1.0", access_token=encoded, verified_at=now
            )
        )


@pytest.mark.asyncio
async def test_rejects_malformed_and_oversized_tokens() -> None:
    _, jwk = _key_material()
    now = datetime.now(UTC).replace(microsecond=0)
    for encoded in ("not-a-token", "x" * 16_385):
        with pytest.raises(CognitoAuthorizationError, match="AUTH_TOKEN_INVALID"):
            await _adapter(jwk).authenticate(
                BearerTokenVerificationRequest(
                    schema_version="1.0", access_token=encoded, verified_at=now
                )
            )
