"""Single-request contracts for identity and secret security boundaries."""

from typing import Literal

from pydantic import AnyHttpUrl, AwareDatetime, Field, SecretStr, model_validator

from stock_assistant.contracts.base import ContractModel
from stock_assistant.contracts.common import OperationContext
from stock_assistant.contracts.system import SecretMaterial, SecretRef


class SecretResolveRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    context: OperationContext
    reference: SecretRef


class SecretResolveResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    reference: SecretRef
    material: SecretMaterial
    resolved_at: AwareDatetime

    @model_validator(mode="after")
    def material_must_match_reference(self) -> "SecretResolveResult":
        if self.material.reference != self.reference:
            raise ValueError("resolved secret material must match the requested reference")
        if self.resolved_at < self.material.loaded_at:
            raise ValueError("resolved_at cannot be earlier than material.loaded_at")
        return self


class AuthenticatedPrincipal(ContractModel):
    """Anonymous application identity derived from a verified external subject."""

    schema_version: Literal["1.0"] = "1.0"
    principal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    issuer: AnyHttpUrl
    username: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(default_factory=list, max_length=32)
    authenticated_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def expiry_must_follow_authentication(self) -> "AuthenticatedPrincipal":
        if self.expires_at <= self.authenticated_at:
            raise ValueError("principal expiry must follow authentication")
        return self


class BearerTokenVerificationRequest(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    access_token: SecretStr
    verified_at: AwareDatetime


class BearerTokenVerificationResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    principal: AuthenticatedPrincipal
    key_id_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RuntimeWebConfig(ContractModel):
    """Non-secret browser bootstrap values emitted as runtime-config.json."""

    schema_version: Literal["1.0"] = "1.0"
    issuer: AnyHttpUrl
    authorization_endpoint: AnyHttpUrl
    token_endpoint: AnyHttpUrl
    end_session_endpoint: AnyHttpUrl
    client_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(pattern=r"^openid(?: [a-zA-Z0-9:._/-]+)*$", max_length=256)
    api_base_path: Literal["/api/v2"] = "/api/v2"
    product_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    environment_label: Literal["私有合成 R2 预览"] = "私有合成 R2 预览"
    synthetic_data: Literal[True] = True


class RuntimeRealWebConfig(ContractModel):
    """Non-secret real preview bootstrap; synthetic bootstrap 1.0 stays immutable."""

    schema_version: Literal["1.1"] = "1.1"
    issuer: AnyHttpUrl
    authorization_endpoint: AnyHttpUrl
    token_endpoint: AnyHttpUrl
    end_session_endpoint: AnyHttpUrl
    client_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(pattern=r"^openid(?: [a-zA-Z0-9:._/-]+)*$", max_length=256)
    api_base_path: Literal["/api/v2"] = "/api/v2"
    product_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    environment_label: Literal["真实资料私人研究 · DEV_ONLY / UNVERIFIED"] = (
        "真实资料私人研究 · DEV_ONLY / UNVERIFIED"
    )
    synthetic_data: Literal[False] = False
