"""Entry point: ``python -m graphrag.mcp --mock``.

``--mock``  Start FastMCP in streamable-http mode on localhost:8000 backed
            by in-memory stores seeded from the fixture corpus.  No AWS
            credentials required.
"""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m graphrag.mcp",
        description="graphrag MCP tool server",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Start in mock mode (in-memory stores, no AWS credentials required)",
    )
    parser.add_argument("--host", default="localhost", help="Bind host (mock mode only)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (mock mode only)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.mock:
        from graphrag.mcp._mock import init_mock, run_mock_server

        init_mock()
        run_mock_server(host=args.host, port=args.port)
    else:
        print(
            "No mode specified. Use --mock for offline mock mode.",
            file=sys.stderr,
        )
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
