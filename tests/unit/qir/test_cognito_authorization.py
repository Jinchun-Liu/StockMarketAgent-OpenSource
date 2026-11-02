from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from stock_assistant.contracts.security import BearerTokenVerificationRequest
from stock_assistant.platform.identity.cognito import CognitoAuthorizationV1_1

ISSUER = "https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_preview"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
CLIENT_ID = "preview-client-id"
KID = "preview-signing-material"


def _key_material() -> tuple[object, dict[str, object]]:
    signing_material = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(signing_material.public_key()))
    public_jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return signing_material, public_jwk


def _transport(jwk: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        return httpx.Response(200, json={"keys": [jwk]})

    return httpx.MockTransport(handler)


def _claims(now: datetime) -> dict[str, object]:
    return {
        "iss": ISSUER,
        "sub": "external-subject-never-persisted",
        "client_id": CLIENT_ID,
        "token_use": "access",
        "username": "owner",
        "scope": "openid",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }


@pytest.mark.asyncio
async def test_cognito_verifier_accepts_only_pinned_rs256_access_token() -> None:
    signing_material, jwk = _key_material()
    now = datetime.now(UTC).replace(microsecond=0)
    encoded = jwt.encode(
        _claims(now),
        signing_material,
        algorithm="RS256",
        headers={"kid": KID, "typ": "JWT"},
    )
    adapter = CognitoAuthorizationV1_1(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        jwks_url=JWKS_URL,
        transport=_transport(jwk),
    )
    request = BearerTokenVerificationRequest(
        schema_version="1.0",
        access_token=encoded,
        verified_at=now,
    )
    result = await adapter.authenticate(request)

    assert result.principal.username == "owner"
    assert result.principal.scopes == ["openid"]
    assert result.principal.principal_sha256 != _claims(now)["sub"]
    assert len(result.principal.principal_sha256) == 64
    assert len(result.key_id_sha256) == 64
