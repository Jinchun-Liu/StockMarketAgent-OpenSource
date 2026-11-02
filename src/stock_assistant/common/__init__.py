"""Cross-cutting primitives that do not depend on business modules."""

from stock_assistant.common.errors import ApplicationError, ErrorCode

__all__ = ["ApplicationError", "ErrorCode"]
