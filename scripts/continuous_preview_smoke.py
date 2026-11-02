"""CLI for the reusable continuous-preview public snapshot smoke."""

from __future__ import annotations

import argparse
import json
import sys

from stock_assistant.platform.publication.public_smoke import (
    ContinuousPreviewSmokeError,
    smoke_snapshot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--product-sha", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = smoke_snapshot(
            base_url=arguments.base_url,
            expected_product_sha=arguments.product_sha,
        )
    except (ContinuousPreviewSmokeError, OSError, ValueError) as error:
        print(f"Continuous preview smoke blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
