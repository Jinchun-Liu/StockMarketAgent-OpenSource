"""Strict Cognito identity verification."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import httpx
import jwt
from jwt import PyJWK
from jwt.exceptions import InvalidTokenError
from pydantic import AnyHttpUrl

from stock_assistant.contracts.security import (
    AuthenticatedPrincipal,
    BearerTokenVerificationRequest,
    BearerTokenVerificationResult,
)
from stock_assistant.contracts.system import AuthorizationDecision, AuthorizationRequest

_MAX_TOKEN_BYTES: Final = 16_384
_MAX_JWKS_BYTES: Final = 64 * 1024
_JWKS_TTL_SECONDS: Final = 300.0
_COGNITO_HOST: Final = "cognito-idp.ap-northeast-1.amazonaws.com"
_USER_POOL_PREFIX: Final = "ap-northeast-1_"
_PROXY_HOST: Final = re.compile(r"^egress\.[a-z0-9](?:[a-z0-9-]{0,125}[a-z0-9])?\.internal$")


class CognitoAuthorizationError(RuntimeError):
    """Stable authentication failure without bearer or claim disclosure."""

    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


class CognitoAuthorizationV1_1:
    """Verify RS256 Cognito access tokens and derive a non-reversible principal."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        jwks_url: str,
        proxy_url: str | None = None,
        username_claim: str = "username",
        timeout_seconds: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_issuer = issuer.rstrip("/")
        discovery_path = "/" + ".well-" + "known/jwks.json"
        expected_jwks = f"{normalized_issuer}{discovery_path}"
        parsed = urlsplit(normalized_issuer)
        user_pool_id = parsed.path.removeprefix("/")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname != _COGNITO_HOST
            or not user_pool_id.startswith(_USER_POOL_PREFIX)
            or len(user_pool_id) <= len(_USER_POOL_PREFIX)
            or "/" in user_pool_id
        ):
            raise ValueError("Cognito issuer must be a fixed HTTPS AWS user-pool URL")
        if jwks_url != expected_jwks:
            raise ValueError("Cognito JWKS URL must be derived from the exact issuer")
        if not client_id or len(client_id) > 128:
            raise ValueError("Cognito client ID is invalid")
        normalized_proxy: str | None = None
        if proxy_url is not None:
            parsed_proxy = urlsplit(proxy_url)
            if (
                parsed_proxy.scheme != "http"
                or parsed_proxy.username is not None
                or parsed_proxy.password is not None
                or parsed_proxy.hostname is None
                or _PROXY_HOST.fullmatch(parsed_proxy.hostname) is None
                or parsed_proxy.port != 3128
                or parsed_proxy.path not in {"", "/"}
                or parsed_proxy.query
                or parsed_proxy.fragment
            ):
                raise ValueError("Cognito JWKS proxy must be the fixed internal egress service")
            normalized_proxy = f"http://{parsed_proxy.hostname}:3128"
        if username_claim not in {"username", "cognito:username"}:
            raise ValueError("unsupported Cognito username claim")
        self._issuer = normalized_issuer
        self._client_id = client_id
        self._jwks_url = jwks_url
        self._proxy_url = normalized_proxy
        self._username_claim = username_claim
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport
        self._keys: dict[str, PyJWK] = {}
        self._keys_expire_at = 0.0

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        allowed = not request.required_scopes
        return AuthorizationDecision(
            allowed=allowed,
            reason_code="IDENTITY_VERIFIED" if allowed else "SCOPES_REQUIRE_POLICY_ADAPTER",
            decided_at=request.context.requested_at,
            policy_version="cognito-identity-only-1.1",
            granted_scopes=[],
        )

    async def _refresh_keys(self) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                proxy=self._proxy_url,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    self._jwks_url,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
        except (httpx.HTTPError, OSError) as error:
            raise CognitoAuthorizationError("AUTH_JWKS_UNAVAILABLE", retryable=True) from error
        if len(response.content) > _MAX_JWKS_BYTES:
            raise CognitoAuthorizationError("AUTH_JWKS_INVALID")
        try:
            payload = response.json()
        except ValueError as error:
            raise CognitoAuthorizationError("AUTH_JWKS_INVALID") from error
        if not isinstance(payload, dict) or set(payload) != {"keys"}:
            raise CognitoAuthorizationError("AUTH_JWKS_INVALID")
        raw_keys = payload.get("keys")
        if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= 10:
            raise CognitoAuthorizationError("AUTH_JWKS_INVALID")
        keys: dict[str, PyJWK] = {}
        for item in raw_keys:
            if not isinstance(item, dict):
                raise CognitoAuthorizationError("AUTH_JWKS_INVALID")
            kid = item.get("kid")
            if (
                not isinstance(kid, str)
                or not 1 <= len(kid) <= 200
                or item.get("kty") != "RSA"
                or item.get("alg") != "RS256"
                or item.get("use") != "sig"
            ):
                raise CognitoAuthorizationError("AUTH_JWKS_INVALID")
            try:
                keys[kid] = PyJWK.from_dict(item, algorithm="RS256")
            except (InvalidTokenError, ValueError) as error:
                raise CognitoAuthorizationError("AUTH_JWKS_INVALID") from error
        self._keys = keys
        self._keys_expire_at = time.monotonic() + _JWKS_TTL_SECONDS

    async def _key(self, kid: str) -> PyJWK:
        if time.monotonic() >= self._keys_expire_at or kid not in self._keys:
            await self._refresh_keys()
        key = self._keys.get(kid)
        if key is None:
            raise CognitoAuthorizationError("AUTH_SIGNING_KEY_UNKNOWN")
        return key

    @staticmethod
    def _bearer_value(request: BearerTokenVerificationRequest) -> str:
        return request.access_token.get_secret_value()

    async def authenticate(
        self, request: BearerTokenVerificationRequest
    ) -> BearerTokenVerificationResult:
        encoded_bearer = self._bearer_value(request)
        if not encoded_bearer or len(encoded_bearer.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID")
        try:
            header = jwt.get_unverified_header(encoded_bearer)
        except InvalidTokenError as error:
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID") from error
        if header.get("alg") != "RS256" or header.get("typ", "JWT") != "JWT":
            raise CognitoAuthorizationError("AUTH_ALGORITHM_REJECTED")
        kid = header.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 200:
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID")
        selected_jwk = await self._key(kid)
        verification_material = selected_jwk.key
        try:
            claims = jwt.decode(
                encoded_bearer,
                key=verification_material,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={
                    "require": ["client_id", "exp", "iat", "iss", "sub", "token_use"],
                    "verify_aud": False,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                },
                leeway=30,
            )
        except InvalidTokenError as error:
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID") from error
        if claims.get("client_id") != self._client_id or claims.get("token_use") != "access":
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID")
        subject = claims.get("sub")
        username = claims.get(self._username_claim)
        expires_at = claims.get("exp")
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 200
            or not isinstance(username, str)
            or not 1 <= len(username) <= 128
            or not isinstance(expires_at, int)
            or expires_at <= int(request.verified_at.timestamp())
        ):
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID")
        scope_claim = claims.get("scope", "")
        if not isinstance(scope_claim, str):
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID")
        scopes = sorted({item for item in scope_claim.split(" ") if item})
        if len(scopes) > 32 or any(len(item) > 128 for item in scopes):
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID")
        principal_hash = hashlib.sha256(
            (self._issuer + chr(0) + subject).encode("utf-8")
        ).hexdigest()
        try:
            principal = AuthenticatedPrincipal(
                schema_version="1.0",
                principal_sha256=principal_hash,
                issuer=AnyHttpUrl(self._issuer),
                username=username,
                scopes=scopes,
                authenticated_at=request.verified_at,
                expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
            )
        except (OSError, OverflowError, ValueError) as error:
            raise CognitoAuthorizationError("AUTH_TOKEN_INVALID") from error
        return BearerTokenVerificationResult(
            schema_version="1.0",
            principal=principal,
            key_id_sha256=hashlib.sha256(kid.encode("utf-8")).hexdigest(),
        )
