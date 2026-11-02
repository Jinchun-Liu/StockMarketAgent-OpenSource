"""Isolated AKShare child process; not an application entrypoint."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, cast

from stock_assistant.platform.sources.akshare_runner import AKSHARE_FUNCTION_ALLOWLIST


class BoundedFrame(Protocol):
    columns: Sequence[object]

    def head(self, limit: int) -> BoundedFrame: ...

    def __len__(self) -> int: ...

    def itertuples(self, *, index: bool, name: object) -> Iterable[tuple[object, ...]]: ...


def _bounded_cell(value: object, max_bytes: int) -> object:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    rendered = value if isinstance(value, str) else str(value)
    if len(rendered) > max_bytes or len(rendered.encode("utf-8")) > max_bytes:
        raise ValueError("AKSHARE_RESPONSE_TOO_LARGE")
    return rendered


def _bounded_rows(frame: object, *, max_rows: int, max_bytes: int) -> dict[str, object]:
    if not all(hasattr(frame, attribute) for attribute in ("head", "columns", "itertuples")):
        return {"ok": False, "error_code": "AKSHARE_SCHEMA_DRIFT"}
    bounded = cast(BoundedFrame, frame).head(max_rows + 1)
    if len(bounded) > max_rows:
        return {"ok": False, "error_code": "AKSHARE_ROW_LIMIT_EXCEEDED"}
    columns = [str(column) for column in bounded.columns]
    if len(columns) != len(set(columns)):
        return {"ok": False, "error_code": "AKSHARE_SCHEMA_DRIFT"}
    rows: list[dict[str, object]] = []
    encoded_size = len(b'{"ok":true,"rows":[]}')
    try:
        for values in bounded.itertuples(index=False, name=None):
            row = {
                column: _bounded_cell(value, max_bytes)
                for column, value in zip(columns, values, strict=True)
            }
            row_size = len(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            encoded_size += row_size + 1
            if encoded_size > max_bytes:
                return {"ok": False, "error_code": "AKSHARE_RESPONSE_TOO_LARGE"}
            rows.append(row)
    except ValueError as error:
        if str(error) == "AKSHARE_RESPONSE_TOO_LARGE":
            return {"ok": False, "error_code": "AKSHARE_RESPONSE_TOO_LARGE"}
        raise
    return {"ok": True, "rows": rows}


def _safe_error(error: Exception) -> str:
    message = str(error).lower()
    if "403" in message or "forbidden" in message:
        return "AKSHARE_FORBIDDEN"
    if "429" in message or "too many requests" in message:
        return "AKSHARE_RATE_LIMITED"
    if "captcha" in message or "验证码" in message:
        return "AKSHARE_CAPTCHA_REQUIRED"
    if "html" in message or "doctype" in message:
        return "AKSHARE_HTML_ERROR_PAGE"
    if "column" in message or "字段" in message or "schema" in message:
        return "AKSHARE_SCHEMA_DRIFT"
    if "timeout" in message or "timed out" in message:
        return "AKSHARE_TIMEOUT"
    return "AKSHARE_UPSTREAM_UNAVAILABLE"


def _run(request: Mapping[str, Any]) -> dict[str, object]:
    function_name = request.get("function")
    arguments = request.get("arguments")
    max_rows = request.get("max_rows")
    max_bytes = request.get("max_bytes")
    if function_name not in AKSHARE_FUNCTION_ALLOWLIST or not isinstance(arguments, dict):
        return {"ok": False, "error_code": "AKSHARE_FUNCTION_NOT_ALLOWED"}
    if (
        not isinstance(max_rows, int)
        or max_rows <= 0
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        return {"ok": False, "error_code": "AKSHARE_REQUEST_INVALID"}
    try:
        akshare = importlib.import_module("akshare")

        function = getattr(akshare, function_name)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            frame = function(**arguments)
        return _bounded_rows(frame, max_rows=max_rows, max_bytes=max_bytes)
    except Exception as error:
        return {"ok": False, "error_code": _safe_error(error)}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        response: dict[str, object] = {"ok": False, "error_code": "AKSHARE_REQUEST_INVALID"}
    else:
        response = (
            _run(request)
            if isinstance(request, dict)
            else {
                "ok": False,
                "error_code": "AKSHARE_REQUEST_INVALID",
            }
        )
    sys.stdout.write(json.dumps(response, default=str, ensure_ascii=False, separators=(",", ":")))
    return 0 if response.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
