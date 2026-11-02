"""Container entrypoint for one bounded refresh command."""

from __future__ import annotations

import json
import re
import sys

from apps.refresh.main import main

_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_:,-]{1,159}$")


def run(argv: list[str] | None = None) -> int:
    """Keep secrets and internal tracebacks out of task logs at the process boundary."""

    try:
        return main(argv)
    except Exception as error:
        message = str(error)
        code = message if _SAFE_CODE.fullmatch(message) is not None else "REFRESH_OPERATION_FAILED"
        print(
            json.dumps({"status": "failed", "error_code": code}, sort_keys=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised by container smoke
    raise SystemExit(run())
