"""Run the research egress proxy."""

from __future__ import annotations

import argparse
import asyncio

from stock_assistant.platform.network.connect_proxy import normalize_allowed_hosts, serve


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowed-hosts", required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(normalize_allowed_hosts(arguments.allowed_hosts)))


if __name__ == "__main__":
    main()
