"""Typed adapter failure exposed by ports without leaking vendor exceptions."""

from stock_assistant.common.errors import ApplicationError, ErrorCode


class PortError(ApplicationError):
    """Base failure all port adapters translate their implementation errors into."""


__all__ = ["ErrorCode", "PortError"]
