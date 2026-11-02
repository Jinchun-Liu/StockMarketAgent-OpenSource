"""Identity adapters for the private R2 preview."""

from stock_assistant.platform.identity.cognito import (
    CognitoAuthorizationError,
    CognitoAuthorizationV1_1,
)

__all__ = ["CognitoAuthorizationError", "CognitoAuthorizationV1_1"]
